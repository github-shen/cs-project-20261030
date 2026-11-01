/* 蓝牙信道适配器：仅本机 UDP <-> Bluetooth BR/EDR L2CAP。
 * 一对进程代表一条双向信道，PSM 区分同一设备上的多条逻辑信道。
 * UDP 只用于接入已有 switch/host；跨设备的帧必须经过 AF_BLUETOOTH。
 * 不修改 Frame/CRC/TTL，不冒充新的交换节点；逐跳查表仍由 switch 负责。
 */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <bluetooth/bluetooth.h>
#include <bluetooth/l2cap.h>
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include "frame.h"

static volatile sig_atomic_t stopping, report;
static unsigned long udp_rx, bt_tx, bt_rx, udp_tx, invalid, denied, offline, errors, connections;
static unsigned long bt_tx_bytes, bt_rx_bytes;

static void on_signal(int sig)
{
    if (sig == SIGUSR1) report = 1;
    else stopping = 1;
}

static long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void stats(void)
{
    printf("[STATS] udp_rx=%lu bt_tx=%lu bt_rx=%lu udp_tx=%lu invalid=%lu "
           "denied=%lu offline=%lu errors=%lu connections=%lu bt_tx_bytes=%lu bt_rx_bytes=%lu\n",
           udp_rx, bt_tx, bt_rx, udp_tx, invalid, denied, offline, errors, connections,
           bt_tx_bytes, bt_rx_bytes);
}

static int number(const char *s, unsigned long max)
{
    char *end;
    unsigned long value;
    errno = 0;
    value = strtoul(s, &end, 0);
    if (errno || !*s || *end || s[0] == '-' || !value || value > max) return -1;
    return (int)value;
}

static int address(const char *s, bdaddr_t *out)
{
    size_t i;
    if (strlen(s) != 17) return -1;
    for (i = 0; i < 17; i++)
        if (i % 3 == 2 ? s[i] != ':' : !isxdigit((unsigned char)s[i])) return -1;
    if (str2ba(s, out) || bacmp(out, BDADDR_ANY) == 0) return -1;
    return 0;
}

static int nonblock(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    return flags < 0 ? -1 : fcntl(fd, F_SETFL, flags | O_NONBLOCK);
}

static int bluetooth_socket(const bdaddr_t *local, int psm, int security)
{
    struct sockaddr_l2 addr = {0};
    struct bt_security sec = {0};
    int fd = socket(AF_BLUETOOTH, SOCK_SEQPACKET | SOCK_CLOEXEC, BTPROTO_L2CAP);
    if (fd < 0) return -1;
    sec.level = (uint8_t)security;
    addr.l2_family = AF_BLUETOOTH;
    bacpy(&addr.l2_bdaddr, local);
    addr.l2_psm = htobs((uint16_t)psm);
    if (setsockopt(fd, SOL_BLUETOOTH, BT_SECURITY, &sec, sizeof(sec)) < 0 ||
        bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0 || nonblock(fd) < 0) {
        int saved = errno;
        close(fd); errno = saved; return -1;
    }
    return fd;
}

/* 先验证数据报边界，再允许下游解析 Frame。CRC 留给现有接收程序校验，
 * 因此 sendbad 的坏 CRC 会原样到达下一跳，适配器不会重封 CRC。 */
static int valid_frame(const unsigned char *buf, ssize_t n)
{
    Frame f;
    if (n < (ssize_t)FRAME_HDR_SIZE || n > (ssize_t)sizeof(Frame)) return 0;
    memcpy(&f, buf, FRAME_HDR_SIZE);
    return f.len < MAX_PAYLOAD && n == (ssize_t)(FRAME_HDR_SIZE + f.len);
}

/* 返回正数表示合法帧，0表示已拒绝的数据报，-1保留recvfrom的errno。 */
static ssize_t receive_local_frame(int udp, const struct sockaddr_in *app,
                                   unsigned char buf[sizeof(Frame) + 1])
{
    struct sockaddr_in from = {0};
    socklen_t len = sizeof(from);
    ssize_t n = recvfrom(udp, buf, sizeof(Frame) + 1, MSG_TRUNC,
                         (struct sockaddr *)&from, &len);
    if (n < 0) return -1;
    if (from.sin_addr.s_addr != app->sin_addr.s_addr || from.sin_port != app->sin_port) {
        denied++; printf("[DROP] 非配置本地应用端口\n"); return 0;
    }
    udp_rx++;
    if (!valid_frame(buf, n)) {
        invalid++; printf("[DROP] 本地帧长度非法\n"); return 0;
    }
    return n;
}

static int ready(int fd, int udp, const struct sockaddr_in *app)
{
    struct l2cap_options opts;
    socklen_t len = sizeof(opts);
    unsigned char pending[sizeof(Frame) + 1];
    if (getsockopt(fd, SOL_L2CAP, L2CAP_OPTIONS, &opts, &len) < 0) return -1;
    if (opts.omtu < sizeof(Frame) || opts.imtu < sizeof(Frame)) {
        errno = EMSGSIZE; return -1;
    }
    /* poll可同时报告连接完成和UDP可读。上线前清空内核UDP队列，
     * 避免把离线期间积压的帧按新连接状态发送；三种建连路径共用此边界。 */
    while (!stopping) {
        ssize_t n = receive_local_frame(udp, app, pending);
        if (n < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) break;
            return -1;
        }
        if (n > 0) {
            offline++; printf("[DROP] 蓝牙信道未连接，无缓存\n");
        }
    }
    if (stopping) { errno = EINTR; return -1; }
    connections++;
    printf("[BT] connected transport=L2CAP imtu=%u omtu=%u\n", opts.imtu, opts.omtu);
    return 0;
}

static void usage(const char *name)
{
    fprintf(stderr, "用法: %s listen|connect 本地蓝牙地址 对端蓝牙地址 PSM "
            "本地UDP端口 应用UDP端口 [medium|high|low]\n"
            "PSM需为动态奇数且满足 (PSM & 0x0101)==1；UDP仅绑定127.0.0.1。\n"
            "默认medium要求链路加密；low仅用于明确隔离的虚拟实验。\n", name);
}

int main(int argc, char **argv)
{
    bdaddr_t local, peer;
    struct sockaddr_in udp_local = {0}, app = {0};
    struct sockaddr_l2 remote = {0};
    struct sigaction sa = {0};
    int server, psm, udp_port, app_port, security = BT_SECURITY_MEDIUM;
    int udp = -1, listener = -1, conn = -1, connecting = 0, result = 1;
    const char *security_name;
    long long retry_at = 0, deadline = 0;
    unsigned char buf[sizeof(Frame) + 1];

    if (argc == 2 && strcmp(argv[1], "--help") == 0) { usage(argv[0]); return 0; }
    if (argc < 7 || argc > 8) { usage(argv[0]); return 2; }
    security_name = argc == 8 ? argv[7] : "medium";
    server = strcmp(argv[1], "listen") == 0;
    psm = number(argv[4], 65535);
    udp_port = number(argv[5], 65535);
    app_port = number(argv[6], 65535);
    if (!server && strcmp(argv[1], "connect")) { usage(argv[0]); return 2; }
    if (address(argv[2], &local) || address(argv[3], &peer) || !bacmp(&local, &peer) ||
        psm < 0x1001 || (psm & 0x0101) != 1 || udp_port < 1024 || app_port < 1 ||
        udp_port == app_port) { fprintf(stderr, "配置错误: 地址、PSM或UDP端口非法\n"); return 2; }
    if (strcmp(security_name, "medium") == 0) security = BT_SECURITY_MEDIUM;
    else if (strcmp(security_name, "high") == 0) security = BT_SECURITY_HIGH;
    else if (strcmp(security_name, "low") == 0) security = BT_SECURITY_LOW;
    else { fprintf(stderr, "配置错误: 安全级别应为medium/high/low\n"); return 2; }

    setvbuf(stdout, NULL, _IOLBF, 0);
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL); sigaction(SIGTERM, &sa, NULL); sigaction(SIGUSR1, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);
    udp = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (udp < 0) { perror("UDP socket"); goto done; }
    udp_local.sin_family = app.sin_family = AF_INET;
    udp_local.sin_addr.s_addr = app.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    udp_local.sin_port = htons((uint16_t)udp_port);
    app.sin_port = htons((uint16_t)app_port);
    if (bind(udp, (struct sockaddr *)&udp_local, sizeof(udp_local)) < 0 || nonblock(udp) < 0) {
        perror("绑定本地UDP失败"); goto done;
    }
    remote.l2_family = AF_BLUETOOTH;
    remote.l2_psm = htobs((uint16_t)psm);
    bacpy(&remote.l2_bdaddr, &peer);
    if (server) {
        listener = bluetooth_socket(&local, psm, security);
        if (listener < 0 || listen(listener, 1) < 0) { perror("蓝牙监听失败"); goto done; }
    } else {
        /* 启动时先检查适配器/权限；运行中的断线才进入重连状态。 */
        int check = bluetooth_socket(&local, 0, security);
        if (check < 0) { perror("蓝牙接口不可用"); goto done; }
        close(check);
    }
    printf("[READY] mode=%s local=%s peer=%s psm=0x%04X udp=%d app=%d security=%s\n",
           argv[1], argv[2], argv[3], psm, udp_port, app_port, security_name);

    while (!stopping) {
        struct pollfd fds[3];
        int count;
        if (report) { report = 0; stats(); }
        if (!server && conn < 0 && now_ms() >= retry_at) {
            conn = bluetooth_socket(&local, 0, security);
            if (conn < 0) { errors++; retry_at = now_ms() + 1000; }
            else if (connect(conn, (struct sockaddr *)&remote, sizeof(remote)) == 0) {
                if (ready(conn, udp, &app) < 0) goto disconnected;
            } else if (errno == EINPROGRESS) {
                connecting = 1; deadline = now_ms() + 5000;
            } else goto disconnected;
        }
        fds[0] = (struct pollfd){udp, POLLIN, 0};
        fds[1] = (struct pollfd){listener, POLLIN, 0};
        fds[2] = (struct pollfd){conn, connecting ? POLLOUT : POLLIN, 0};
        count = poll(fds, 3, 200);
        if (count < 0) { if (errno == EINTR) continue; perror("poll"); goto done; }
        if (connecting && now_ms() >= deadline) { errno = ETIMEDOUT; goto disconnected; }
        if (fds[1].revents & POLLIN) {
            struct sockaddr_l2 from = {0};
            socklen_t n = sizeof(from);
            int incoming = accept(listener, (struct sockaddr *)&from, &n);
            if (incoming >= 0) {
                if (bacmp(&from.l2_bdaddr, &peer) || conn >= 0) {
                    denied++; close(incoming); printf("[DROP] 非配置蓝牙对端或信道已占用\n");
                } else if (nonblock(incoming) < 0 || ready(incoming, udp, &app) < 0) {
                    errors++; close(incoming);
                } else conn = incoming;
            }
        }
        if (connecting && fds[2].revents) {
            int err = 0;
            socklen_t len = sizeof(err);
            if (getsockopt(conn, SOL_SOCKET, SO_ERROR, &err, &len) < 0) goto disconnected;
            if (err) { errno = err; goto disconnected; }
            if (ready(conn, udp, &app) < 0) goto disconnected;
            connecting = 0;
        } else if (fds[2].revents & (POLLERR | POLLHUP | POLLNVAL)) {
            errno = ECONNRESET; goto disconnected;
        } else if (fds[2].revents & POLLIN) {
            ssize_t n = recv(conn, buf, sizeof(buf), MSG_TRUNC);
            if (n == 0) { errno = ECONNRESET; goto disconnected; }
            if (n < 0) { if (errno != EAGAIN && errno != EINTR) goto disconnected; }
            else if (!valid_frame(buf, n)) { invalid++; printf("[DROP] 蓝牙帧长度非法\n"); }
            else {
                bt_rx++;
                bt_rx_bytes += (unsigned long)n;
                if (sendto(udp, buf, (size_t)n, 0, (struct sockaddr *)&app, sizeof(app)) == n) udp_tx++;
                else errors++;
            }
        }
        if (fds[0].revents & POLLIN) {
            ssize_t n = receive_local_frame(udp, &app, buf);
            if (n < 0) { if (errno != EAGAIN && errno != EINTR) errors++; continue; }
            if (n == 0) continue;
            if (conn < 0 || connecting) { offline++; printf("[DROP] 蓝牙信道未连接，无缓存\n"); continue; }
            if (send(conn, buf, (size_t)n, MSG_NOSIGNAL) == n) {
                bt_tx++; bt_tx_bytes += (unsigned long)n;
            }
            else {
                errors++;
                if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) goto disconnected;
            }
        }
        continue;
disconnected:
        printf("[BT] disconnected: %s\n", strerror(errno));
        if (conn >= 0) close(conn);
        conn = -1; connecting = 0; retry_at = now_ms() + 1000;
    }
    result = 0;
done:
    stats();
    if (conn >= 0) close(conn);
    if (listener >= 0) close(listener);
    if (udp >= 0) close(udp);
    return result;
}
