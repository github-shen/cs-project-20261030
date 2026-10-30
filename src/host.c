/* host.c —— 外部主机程序
 *
 * 挂在某个节点的外部端口上，代表上行/下行逻辑信道中的一台主机。
 *
 * 用法: host <本机地址hex> <本地UDP端口> <节点IP> <节点外部端口UDP>
 *            [bind <本地绑定IP>] [dev <网卡名>]
 * 例（单机回环）: ./bin/host 0x0A01 9001 127.0.0.1 1103
 * 例（有线信道）: ./bin/host 0x0A01 9001 192.168.1.10 1103 bind 192.168.1.20
 * 例（WiFi 信道）: ./bin/host 0x0B01 9002 192.168.43.10 3104 bind 192.168.43.20
 *
 * 说明：bind 指定本机发帧时使用的源地址（多网卡机器上用于“指定走哪块网卡”），
 *       dev 用 SO_BINDTODEVICE 强制绑定网卡，需要 root 权限。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <sys/socket.h>
#include "frame.h"

static int sockfd;
static struct sockaddr_in sw;   /* 所连节点外部端口的地址 */
static uint16_t myaddr;

static void *rx_loop(void *arg)
{
    Frame f;
    ssize_t n;
    (void)arg;

    for (;;) {
        memset(&f, 0, sizeof(f));
        n = recv(sockfd, &f, sizeof(f), 0);
        if (n < (ssize_t)FRAME_HDR_SIZE)
            continue;
        if ((size_t)n < (size_t)FRAME_HDR_SIZE + (size_t)f.len || f.len >= MAX_PAYLOAD) {
            printf("[DROP] 帧长度非法或截断\n");
            continue;
        }
        if (!frame_verify(&f)) {
            printf("[DROP] CRC校验失败, 帧已丢弃\n");
            continue;
        }
        if (f.dst != myaddr) {
            printf("[忽略] 帧目的地址0x%04X不是本机\n", f.dst);
            continue;
        }
        f.payload[f.len] = '\0';
        printf("[RECV] 来自0x%04X: \"%s\"\n", f.src, f.payload);
        fflush(stdout);
    }
    return NULL;
}

int main(int argc, char *argv[])
{
    pthread_t tid;
    char line[256];
    const char *bind_ip = NULL;
    const char *dev = NULL;
    int i;

    if (argc < 5) {
        fprintf(stderr,
                "用法: %s <本机地址hex> <本地UDP> <节点IP> <节点UDP> [bind <本地IP>] [dev <网卡>]\n",
                argv[0]);
        return 1;
    }
    for (i = 5; i + 1 < argc; i += 2) {
        if (strcmp(argv[i], "bind") == 0)
            bind_ip = argv[i + 1];
        else if (strcmp(argv[i], "dev") == 0)
            dev = argv[i + 1];
        else
            fprintf(stderr, "[警告] 未知参数: %s\n", argv[i]);
    }

    myaddr = (uint16_t)strtoul(argv[1], NULL, 16);

    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) {
        perror("创建 socket 失败");
        return 1;
    }
    {
        int on = 1;
        struct sockaddr_in local;
        setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &on, sizeof(on));
        if (dev) {
#ifdef SO_BINDTODEVICE
            if (setsockopt(sockfd, SOL_SOCKET, SO_BINDTODEVICE, dev,
                           (socklen_t)strlen(dev)) < 0)
                fprintf(stderr, "[警告] 绑定网卡 %s 失败: %s（需要 root）\n",
                        dev, strerror(errno));
#endif
        }
        memset(&local, 0, sizeof(local));
        local.sin_family = AF_INET;
        local.sin_port = htons((uint16_t)atoi(argv[2]));
        if (bind_ip && inet_pton(AF_INET, bind_ip, &local.sin_addr) != 1) {
            fprintf(stderr, "本地绑定IP非法: %s\n", bind_ip);
            return 1;
        }
        if (!bind_ip)
            local.sin_addr.s_addr = htonl(INADDR_ANY);
        if (bind(sockfd, (struct sockaddr *)&local, sizeof(local)) < 0) {
            fprintf(stderr, "绑定失败 %s:%s (%s)%s\n", bind_ip ? bind_ip : "0.0.0.0",
                    argv[2], strerror(errno),
                    atoi(argv[2]) < 1024 ? "  <-- 小于1024是特权端口" : "");
            return 1;
        }
    }
    memset(&sw, 0, sizeof(sw));
    sw.sin_family = AF_INET;
    sw.sin_port = htons((uint16_t)atoi(argv[4]));
    if (inet_pton(AF_INET, argv[3], &sw.sin_addr) != 1) {
        fprintf(stderr, "节点IP非法: %s\n", argv[3]);
        return 1;
    }

    if (pthread_create(&tid, NULL, rx_loop, NULL) != 0) {
        fprintf(stderr, "创建接收线程失败\n");
        return 1;
    }
    pthread_detach(tid);

    printf("主机0x%04X已就绪, 本地 %s:%s, 连接到节点 %s:%s%s%s\n",
           myaddr, bind_ip ? bind_ip : "0.0.0.0", argv[2], argv[3], argv[4],
           dev ? ", 网卡 " : "", dev ? dev : "");
    printf("命令: send <目的地址hex> <内容> | sendbad <目的地址hex> <内容> | quit\n");

    while (fgets(line, sizeof(line), stdin)) {
        char *save = NULL;
        char *cmd = strtok_r(line, " \t\n", &save);
        char *sdst, *data;
        uint16_t dst;
        Frame f;

        if (!cmd) continue;
        if (strcmp(cmd, "quit") == 0)
            break;
        if (strcmp(cmd, "send") && strcmp(cmd, "sendbad")) {
            printf("未知命令\n");
            continue;
        }
        sdst = strtok_r(NULL, " \t\n", &save);
        data = strtok_r(NULL, "\n", &save);
        if (!sdst || !data) {
            printf("用法: %s <目的地址hex> <内容>\n", cmd);
            continue;
        }
        while (*data == ' ') data++;
        dst = (uint16_t)strtoul(sdst, NULL, 16);
        frame_fill(&f, dst, myaddr, data);
        if (strcmp(cmd, "sendbad") == 0)
            f.crc ^= 0x0001;
        if (sendto(sockfd, &f, frame_wire_size(&f), 0,
                   (struct sockaddr *)&sw, sizeof(sw)) < 0)
            printf("[错误] 发送失败: %s\n", strerror(errno));
    }
    return 0;
}
