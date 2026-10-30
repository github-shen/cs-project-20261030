/* switch.c —— 交换网络中的逻辑交换节点 (v2.1)
 *
 * 对应实验要求：
 *  (1) 与 Open vSwitch 配合：内部链路可跑在 OVS 建的二层域上（bind 到 OVS 内部端口 IP），
 *      见 scripts/ovs_setup.sh；
 *  (2) 外部端口表 + 外部转发：ept_lookup()，目的网络直连本节点外部端口则直接送出；
 *  (3) 内部转发表 + 内部转发：ift_lookup() + route_frame()，在节点域内逐跳转发；
 *  (4) 负载均衡：转发表项含多条等价端口，按流 (src^dst) 选路，同一数据流固定同一路径；
 *  (5) 源地址校验：外部端口收帧时校验 src>>8 == 该端口所连网络号；
 *  (6) 数据包校验：帧尾 CRC-16-CCITT，坏帧丢弃并计数；
 *  (7) 外部信道类型：端口可标注 eth/wifi/bt，并可绑定到指定网卡/IP（有线、WiFi、蓝牙任选两种）；
 *  (8) 外部端口源地址学习：同一信道挂多台主机时，回程帧按学习的地址回送，而不是死发给配置里的 peer。
 *
 * 端口之间的“链路”用 UDP 报文模拟：单机回环用 127.0.0.1；
 * 走 OVS 时用 10.1.0.x（OVS 内部端口）；跨机时用真实网卡 IP（有线/WiFi/蓝牙均可）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#include <errno.h>
#include <pthread.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/socket.h>
#include "frame.h"

#define MAX_PORTS   8       /* 每个节点最多端口数 */
#define MAX_IFT     128     /* 内部转发表最大表项数 */
#define MAX_LB      4       /* 每个表项最多等价端口数（负载均衡） */
#define MAX_LEARN   16      /* 每个外部端口最多学习的站点数 */
#define LINK_MAX    8       /* 信道类型字符串长度 */

/* ---------------- 外部端口上学习到的站点（地址 -> UDP 源地址） ---------------- */
typedef struct {
    uint16_t addr;
    struct sockaddr_in sa;
} LearnEntry;

/* ---------------- 端口 ---------------- */
typedef struct {
    int      id;                 /* 端口号 */
    int      is_ext;             /* 1=外部端口 0=内部端口 */
    uint8_t  ext_net;            /* 外部端口所连网络号（仅外部端口有效） */
    uint16_t lport;              /* 本地绑定的 UDP 端口 */
    char     bind_ip[INET_ADDRSTRLEN]; /* 本地绑定 IP，空=INADDR_ANY */
    char     dev[IF_NAMESIZE];   /* 绑定网卡（SO_BINDTODEVICE，需 root） */
    char     link[LINK_MAX];     /* 信道类型：eth/wifi/bt/int（展示用） */
    char     peer_ip[INET_ADDRSTRLEN];
    int      sock;
    struct sockaddr_in peer;
    int      has_peer;
    LearnEntry learn[MAX_LEARN]; /* 外部端口源地址学习表 */
    int      nlearn;
    int      learn_rr;           /* 学习表轮转替换指针 */
} Port;

/* ---------------- 内部转发表项 ---------------- */
typedef struct {
    uint16_t key;            /* 本域用完整目的地址(0x0102)，外部网络用网络号左移8位(0x0B00) */
    int      n;              /* 等价端口个数 */
    int      port[MAX_LB];   /* 等价端口列表 */
} IftEntry;

static Port     ports[MAX_PORTS];
static int      nports = 0;
static IftEntry ift[MAX_IFT];
static int      nift = 0;
static int      myid;
static uint16_t myaddr;

/* 统计计数（多线程并发热点，用原子加减，避免丢更新） */
static unsigned long st_rx, st_tx, st_crc_err, st_src_err, st_noroute,
                     st_ttl_drop, st_deliver, st_nolearn, st_senderr;

#define ST_INC(x) __atomic_add_fetch(&(x), 1UL, __ATOMIC_RELAXED)

static pthread_mutex_t log_lock = PTHREAD_MUTEX_INITIALIZER;

/* ---------------- 工具函数 ---------------- */

static Port *find_port(int id)
{
    int i;
    for (i = 0; i < nports; i++)
        if (ports[i].id == id)
            return &ports[i];
    return NULL;
}

/* 外部端口表查找：目的网络 net 是否直接连在本节点某个外部端口上 */
static Port *ept_lookup(uint8_t net)
{
    int i;
    for (i = 0; i < nports; i++)
        if (ports[i].is_ext && ports[i].ext_net == net)
            return &ports[i];
    return NULL;
}

/* 内部转发表查找 + 负载均衡选择
 * 同一数据流(源,目的)固定走同一条路径，避免乱序；不同流散到不同端口。 */
static int ift_lookup(uint16_t key, uint16_t src, uint16_t dst)
{
    int i;
    for (i = 0; i < nift; i++) {
        if (ift[i].key == key) {
            int idx = (int)(((unsigned)(src ^ dst)) % (unsigned)ift[i].n);
            return ift[i].port[idx];
        }
    }
    return -1;
}

/* 外部端口源地址学习：把“站点地址 -> 该站点发送时的 UDP 源地址”记下来 */
static void learn_add(Port *p, uint16_t addr, const struct sockaddr_in *from)
{
    int i;

    for (i = 0; i < p->nlearn; i++) {
        if (p->learn[i].addr == addr) {
            p->learn[i].sa = *from;
            return;
        }
    }
    if (p->nlearn < MAX_LEARN) {
        i = p->nlearn++;
    } else {
        i = p->learn_rr;
        p->learn_rr = (p->learn_rr + 1) % MAX_LEARN;
    }
    p->learn[i].addr = addr;
    p->learn[i].sa = *from;
}

static const struct sockaddr_in *learn_find(Port *p, uint16_t addr)
{
    int i;
    for (i = 0; i < p->nlearn; i++)
        if (p->learn[i].addr == addr)
            return &p->learn[i].sa;
    return NULL;
}

static void log_line(const char *fmt, ...)
{
    va_list ap;
    pthread_mutex_lock(&log_lock);
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
    fflush(stdout);
    pthread_mutex_unlock(&log_lock);
}

/* ---------------- 转发核心 ---------------- */

/* 从端口 p 把帧发给对端
 * 外部端口优先用“学到的站点地址”回送（同一信道可挂多台主机），
 * 没学到才退回配置里的 peer。 */
static void port_send(Port *p, Frame *f)
{
    const struct sockaddr_in *dst = NULL;

    if (p->is_ext) {
        dst = learn_find(p, f->dst);
        if (!dst && p->has_peer) {
            dst = &p->peer;
            ST_INC(st_nolearn);
        }
    } else if (p->has_peer) {
        dst = &p->peer;
    }
    if (!dst) {
        log_line("[DROP] 端口%d无对端且未学到目的站点 dst=0x%04X", p->id, f->dst);
        return;
    }

    if (f->ttl <= 1) {
        ST_INC(st_ttl_drop);
        log_line("[DROP] TTL超期 dst=0x%04X (端口%d)", f->dst, p->id);
        return;
    }
    /* ttl 属于 CRC 覆盖范围，改了就必须重新封 CRC，否则下一跳校验必然失败 */
    f->ttl--;
    frame_seal(f);

    if (sendto(p->sock, f, frame_wire_size(f), 0,
               (struct sockaddr *)dst, sizeof(*dst)) < 0) {
        ST_INC(st_senderr);
        log_line("[DROP] 端口%d发送失败: %s", p->id, strerror(errno));
        return;
    }
    ST_INC(st_tx);
}

/* 帧的路由决策。in_port = -1 表示本节点自己产生的帧 */
static void route_frame(Frame *f, int in_port)
{
    uint8_t net  = (uint8_t)(f->dst >> 8);
    uint8_t node = (uint8_t)(f->dst & 0xFF);
    int out;
    Port *p;
    char from[32];

    if (in_port < 0)
        snprintf(from, sizeof(from), "本机");
    else
        snprintf(from, sizeof(from), "入端口%d", in_port);

    /* 1) 目的在本节点域内 */
    if (net == NET_LOCAL) {
        if (node == myid) {                 /* 就是发给本节点的，交付 */
            ST_INC(st_deliver);
            log_line("[RECV] 交付本节点: src=0x%04X 内容=\"%s\"", f->src, f->payload);
            return;
        }
        out = ift_lookup(f->dst, f->src, f->dst);   /* 内部转发表按完整目的地址查 */
        if (out < 0)
            goto no_route;
        p = find_port(out);
        if (p == NULL)                  /* 表项引用了不存在的端口 */
            goto no_route;
        if (in_port < 0)
            log_line("[FWD]  内部转发: 本机 -> 出端口%d dst=0x%04X", out, f->dst);
        else
            log_line("[FWD]  内部转发: 入端口%d -> 出端口%d dst=0x%04X", in_port, out, f->dst);
        port_send(p, f);
        return;
    }

    /* 2) 目的在外部网络：先查外部端口表（直接相连则送出） */
    p = ept_lookup(net);
    if (p != NULL) {
        log_line("[FWD]  外部转发: %s -> 外部端口%d(网络0x%02X/%s) dst=0x%04X",
                 from, p->id, net, p->link, f->dst);
        port_send(p, f);
        return;
    }

    /* 3) 外部网络不直接相连：查内部转发表，往拥有该网络的节点转发 */
    out = ift_lookup((uint16_t)(net << 8), f->src, f->dst);
    if (out < 0)
        goto no_route;
    p = find_port(out);
    if (p == NULL)
        goto no_route;
    log_line("[FWD]  内部转发(跨域途中): %s -> 出端口%d dst=0x%04X",
             from, out, f->dst);
    port_send(p, f);
    return;

no_route:
    ST_INC(st_noroute);
    log_line("[DROP] 无路由 dst=0x%04X src=0x%04X", f->dst, f->src);
}

/* 每个端口一个接收线程 */
static void *rx_loop(void *arg)
{
    Port *p = (Port *)arg;
    Frame f;
    ssize_t n;

    for (;;) {
        struct sockaddr_in from;
        socklen_t fromlen = sizeof(from);

        memset(&from, 0, sizeof(from));
        memset(&f, 0, sizeof(f));
        n = recvfrom(p->sock, &f, sizeof(f), 0, (struct sockaddr *)&from, &fromlen);
        if (n < (ssize_t)FRAME_HDR_SIZE)
            continue;                       /* 太短，连帧头都不完整 */
        ST_INC(st_rx);

        /* 实收字节必须够 帧头 + len，否则说明报文被截断；
         * len 必须 < MAX_PAYLOAD，给下面的 '\0' 留一个字节，避免写越界 */
        if ((size_t)n < (size_t)FRAME_HDR_SIZE + (size_t)f.len ||
            f.len >= MAX_PAYLOAD) {
            ST_INC(st_crc_err);
            log_line("[DROP] 帧长度非法或截断 (端口%d, 收到%ld字节, len=%u)",
                     p->id, (long)n, (unsigned)f.len);
            continue;
        }
        /* 数据包校验 */
        if (!frame_verify(&f)) {
            ST_INC(st_crc_err);
            log_line("[DROP] CRC校验失败 (端口%d)", p->id);
            continue;
        }
        /* 源地址校验：外部端口进来的帧，源网络必须等于该端口所连网络 */
        if (p->is_ext && (uint8_t)(f.src >> 8) != p->ext_net) {
            ST_INC(st_src_err);
            log_line("[DROP] 源地址校验失败: 端口%d连网络0x%02X, 但帧源地址0x%04X",
                     p->id, p->ext_net, f.src);
            continue;
        }
        /* 外部端口学习站点地址，用于把回程帧送回真正的主机 */
        if (p->is_ext)
            learn_add(p, f.src, &from);

        f.payload[f.len] = '\0';            /* 长度已校验，此处一定在缓冲区内 */
        route_frame(&f, p->id);
    }
    return NULL;
}

/* ---------------- 配置解析 ---------------- */

static int load_config(const char *path)
{
    FILE *fp = fopen(path, "r");
    char line[256];

    if (!fp) {
        perror("无法打开配置文件");
        return -1;
    }
    while (fgets(line, sizeof(line), fp)) {
        char *save = NULL, *tok, *hash = strchr(line, '#');
        if (hash) *hash = '\0';
        tok = strtok_r(line, " \t\n", &save);
        if (!tok)
            continue;

        if (strcmp(tok, "id") == 0) {
            char *sid = strtok_r(NULL, " \t\n", &save);
            if (sid) myid = atoi(sid);
        } else if (strcmp(tok, "port") == 0) {
            /* port <端口号> <int|ext> [外部网络号] <本地UDP> <对端IP> <对端UDP>
             *      [bind <本地IP>] [dev <网卡>] [type <eth|wifi|bt>] */
            char *sid, *stype, *snet = NULL, *sludp, *sip, *sudp, *opt, *val;
            Port *p;

            sid   = strtok_r(NULL, " \t\n", &save);
            stype = strtok_r(NULL, " \t\n", &save);
            if (!sid || !stype) continue;
            if (nports >= MAX_PORTS) {
                fprintf(stderr, "端口数超过上限 %d\n", MAX_PORTS);
                return -1;
            }
            p = &ports[nports++];
            memset(p, 0, sizeof(*p));
            p->id = atoi(sid);
            p->is_ext = (strcmp(stype, "ext") == 0);
            if (p->is_ext) {
                snet = strtok_r(NULL, " \t\n", &save);
                if (!snet) continue;
                p->ext_net = (uint8_t)strtoul(snet, NULL, 16);
                snprintf(p->link, sizeof(p->link), "ext");
            } else {
                snprintf(p->link, sizeof(p->link), "int");
            }
            sludp = strtok_r(NULL, " \t\n", &save);
            sip   = strtok_r(NULL, " \t\n", &save);
            sudp  = strtok_r(NULL, " \t\n", &save);
            if (!sludp || !sip || !sudp) {
                fprintf(stderr, "port 行参数不完整: %s\n", line);
                return -1;
            }
            p->lport = (uint16_t)atoi(sludp);
            snprintf(p->peer_ip, sizeof(p->peer_ip), "%s", sip);

            /* 可选属性：bind / dev / type */
            for (opt = strtok_r(NULL, " \t\n", &save); opt;
                 opt = strtok_r(NULL, " \t\n", &save)) {
                val = strtok_r(NULL, " \t\n", &save);
                if (!val) break;
                if (strcmp(opt, "bind") == 0) {
                    snprintf(p->bind_ip, sizeof(p->bind_ip), "%s", val);
                } else if (strcmp(opt, "dev") == 0) {
                    snprintf(p->dev, sizeof(p->dev), "%s", val);
                } else if (strcmp(opt, "type") == 0) {
                    snprintf(p->link, sizeof(p->link), "%s", val);
                } else {
                    fprintf(stderr, "未知端口属性: %s\n", opt);
                }
            }

            p->sock = socket(AF_INET, SOCK_DGRAM, 0);
            if (p->sock < 0) {
                perror("创建 socket 失败");
                return -1;
            }
            {
                int on = 1;
                struct sockaddr_in local;
                setsockopt(p->sock, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
                if (p->dev[0]) {
#ifdef SO_BINDTODEVICE
                    if (setsockopt(p->sock, SOL_SOCKET, SO_BINDTODEVICE,
                                   p->dev, (socklen_t)strlen(p->dev)) < 0) {
                        fprintf(stderr, "[警告] 绑定网卡 %s 失败: %s（SO_BINDTODEVICE 需要 root）\n",
                                p->dev, strerror(errno));
                    }
#endif
                }
                memset(&local, 0, sizeof(local));
                local.sin_family = AF_INET;
                local.sin_port = htons(p->lport);
                if (p->bind_ip[0]) {
                    if (inet_pton(AF_INET, p->bind_ip, &local.sin_addr) != 1) {
                        fprintf(stderr, "本地绑定IP非法: %s\n", p->bind_ip);
                        return -1;
                    }
                } else {
                    local.sin_addr.s_addr = htonl(INADDR_ANY);
                }
                if (bind(p->sock, (struct sockaddr *)&local, sizeof(local)) < 0) {
                    fprintf(stderr, "绑定端口失败: %s:%u (%s)%s\n",
                            p->bind_ip[0] ? p->bind_ip : "0.0.0.0", p->lport,
                            strerror(errno),
                            p->lport < 1024 ? "  <-- 小于1024是特权端口，需要root或改端口" : "");
                    return -1;
                }
            }
            if (strcmp(sip, "-") != 0) {
                memset(&p->peer, 0, sizeof(p->peer));
                p->peer.sin_family = AF_INET;
                p->peer.sin_port = htons((uint16_t)atoi(sudp));
                if (inet_pton(AF_INET, sip, &p->peer.sin_addr) != 1) {
                    if (p->is_ext) {
                        fprintf(stderr, "[警告] 端口%d 对端IP非法: %s\n", p->id, sip);
                    } else {
                        fprintf(stderr, "端口%d 对端IP非法: %s\n", p->id, sip);
                        return -1;
                    }
                } else {
                    p->has_peer = 1;
                }
            }
        } else if (strcmp(tok, "ift") == 0) {
            /* ift <目的键(hex)> <端口1[,端口2...]> */
            char *skey = strtok_r(NULL, " \t\n", &save);
            char *slist = strtok_r(NULL, " \t\n", &save);
            IftEntry *e;
            char *lsave = NULL, *pt;
            if (!skey || !slist) continue;
            if (nift >= MAX_IFT) {
                fprintf(stderr, "转发表项超过上限 %d\n", MAX_IFT);
                return -1;
            }
            e = &ift[nift++];
            e->key = (uint16_t)strtoul(skey, NULL, 16);
            e->n = 0;
            for (pt = strtok_r(slist, ",", &lsave);
                 pt && e->n < MAX_LB;
                 pt = strtok_r(NULL, ",", &lsave))
                e->port[e->n++] = atoi(pt);
            if (e->n == 0) e->n = 1;   /* 兜底：至少一个端口 */
        }
    }
    fclose(fp);
    return 0;
}

/* ---------------- 命令行界面 ---------------- */

static void print_tables(void)
{
    int i, j;

    printf("==== 外部端口表 (节点%d) ====\n", myid);
    printf("  网络号  端口  信道   本地绑定            学到主机\n");
    for (i = 0; i < nports; i++) {
        if (!ports[i].is_ext)
            continue;
        printf("  0x%02X    %d     %-5s  %-18s  %d\n",
               ports[i].ext_net, ports[i].id, ports[i].link,
               ports[i].bind_ip[0] ? ports[i].bind_ip : "0.0.0.0(all)", ports[i].nlearn);
    }

    printf("==== 内部转发表 ====\n");
    printf("  目的键      等价端口(负载均衡)\n");
    for (i = 0; i < nift; i++) {
        printf("  0x%04X    ", ift[i].key);
        for (j = 0; j < ift[i].n; j++)
            printf("%d%s", ift[i].port[j], j + 1 < ift[i].n ? " / " : "");
        putchar('\n');
    }

    for (i = 0; i < nports; i++) {
        int k;
        if (!ports[i].is_ext || ports[i].nlearn == 0)
            continue;
        printf("==== 端口%d 学到的外部主机 ====\n", ports[i].id);
        for (k = 0; k < ports[i].nlearn; k++) {
            char ip[INET_ADDRSTRLEN];
            inet_ntop(AF_INET, &ports[i].learn[k].sa.sin_addr, ip, sizeof(ip));
            printf("  0x%04X -> %s:%u\n", ports[i].learn[k].addr, ip,
                   ntohs(ports[i].learn[k].sa.sin_port));
        }
    }
}

static void print_stats(void)
{
    printf("接收=%lu 发送=%lu 交付=%lu CRC错误=%lu 源地址错误=%lu 无路由=%lu TTL超期=%lu 未学到回退=%lu 发送失败=%lu\n",
           __atomic_load_n(&st_rx, __ATOMIC_RELAXED),
           __atomic_load_n(&st_tx, __ATOMIC_RELAXED),
           __atomic_load_n(&st_deliver, __ATOMIC_RELAXED),
           __atomic_load_n(&st_crc_err, __ATOMIC_RELAXED),
           __atomic_load_n(&st_src_err, __ATOMIC_RELAXED),
           __atomic_load_n(&st_noroute, __ATOMIC_RELAXED),
           __atomic_load_n(&st_ttl_drop, __ATOMIC_RELAXED),
           __atomic_load_n(&st_nolearn, __ATOMIC_RELAXED),
           __atomic_load_n(&st_senderr, __ATOMIC_RELAXED));
}

static void reset_stats(void)
{
    st_rx = st_tx = st_crc_err = st_src_err = st_noroute = 0;
    st_ttl_drop = st_deliver = st_nolearn = st_senderr = 0;
    printf("统计计数已清零\n");
}

static void print_help(void)
{
    printf("命令:\n"
           "  send <目的地址hex> <内容>   发送数据帧, 如 send 0x0B01 hello\n"
           "  sendbad <目的地址hex> <内容> 发送CRC故意损坏的帧(演示数据包校验)\n"
           "  table                       显示外部端口表/内部转发表/学习表\n"
           "  stats                       显示统计计数\n"
           "  reset                       计数清零(截图前用)\n"
           "  quit                        退出\n");
}

int main(int argc, char *argv[])
{
    int i;
    char line[256];

    if (argc < 2) {
        fprintf(stderr, "用法: %s <配置文件>\n", argv[0]);
        return 1;
    }
    if (load_config(argv[1]) < 0)
        return 1;
    myaddr = (uint16_t)((NET_LOCAL << 8) | myid);

    for (i = 0; i < nports; i++) {
        pthread_t tid;
        if (pthread_create(&tid, NULL, rx_loop, &ports[i]) != 0) {
            fprintf(stderr, "创建接收线程失败 (端口%d)\n", ports[i].id);
            return 1;
        }
        pthread_detach(tid);
    }

    printf("节点%d已启动, 本机地址0x%04X, 共%d个端口\n", myid, myaddr, nports);
    for (i = 0; i < nports; i++) {
        Port *p = &ports[i];
        char local[INET_ADDRSTRLEN + 8];
        snprintf(local, sizeof(local), "%s:%u", p->bind_ip[0] ? p->bind_ip : "0.0.0.0", p->lport);
        if (p->is_ext != 0)
            printf("  端口%d 外部(网络0x%02X/%-4s) 本地 %-20s -> 对端 %s:%u\n",
                   p->id, p->ext_net, p->link, local,
                   p->peer_ip[0] ? p->peer_ip : "-", ntohs(p->peer.sin_port));
        else
            printf("  端口%d 内部(信道%-4s)     本地 %-20s -> 对端 %s:%u\n",
                   p->id, p->link, local,
                   p->peer_ip[0] ? p->peer_ip : "-", ntohs(p->peer.sin_port));
    }
    print_help();

    while (fgets(line, sizeof(line), stdin)) {
        char *save = NULL;
        char *cmd = strtok_r(line, " \t\n", &save);
        Frame f;
        uint16_t dst;
        char *sdst, *data;

        if (!cmd) continue;
        if (strcmp(cmd, "quit") == 0) {
            break;
        } else if (strcmp(cmd, "help") == 0) {
            print_help();
        } else if (strcmp(cmd, "table") == 0) {
            print_tables();
        } else if (strcmp(cmd, "stats") == 0) {
            print_stats();
        } else if (strcmp(cmd, "reset") == 0) {
            reset_stats();
        } else if (strcmp(cmd, "send") == 0 || strcmp(cmd, "sendbad") == 0) {
            sdst = strtok_r(NULL, " \t\n", &save);
            data = strtok_r(NULL, "\n", &save);
            if (!sdst || !data) {
                printf("用法: %s <目的地址hex> <内容>\n", cmd);
                continue;
            }
            while (*data == ' ') data++;   /* 去掉内容前导空格 */
            dst = (uint16_t)strtoul(sdst, NULL, 16);
            frame_fill(&f, dst, myaddr, data);
            if (strcmp(cmd, "sendbad") == 0)
                f.crc ^= 0x0001;   /* 故意破坏CRC */
            route_frame(&f, -1);
        } else {
            printf("未知命令, 输入 help 查看帮助\n");
        }
    }
    return 0;
}
