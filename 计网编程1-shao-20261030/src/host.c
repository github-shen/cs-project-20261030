/* host.c —— 外部主机程序
 *
 * 挂在某个节点的外部端口上，代表上行/下行逻辑信道中的一台主机。
 * 用法: host <本机地址hex> <本地UDP端口> <节点IP> <节点外部端口UDP>
 * 例:   host 0x0A01 9001 127.0.0.1 1103
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <unistd.h>
#include <arpa/inet.h>
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
        n = recv(sockfd, &f, sizeof(f), 0);
        if (n < (ssize_t)FRAME_HDR_SIZE)
            continue;
        if (f.len > MAX_PAYLOAD || !frame_verify(&f)) {
            printf("[DROP] CRC校验失败, 帧已丢弃\n");
            continue;
        }
        if (f.dst != myaddr) {
            printf("[忽略] 帧目的地址0x%04X不是本机\n", f.dst);
            continue;
        }
        if (f.len < MAX_PAYLOAD)
            f.payload[f.len] = '\0';
        else
            f.payload[MAX_PAYLOAD - 1] = '\0';
        printf("[RECV] 来自0x%04X: \"%s\"\n", f.src, f.payload);
        fflush(stdout);
    }
    return NULL;
}

int main(int argc, char *argv[])
{
    pthread_t tid;
    char line[256];

    if (argc < 5) {
        fprintf(stderr, "用法: %s <本机地址hex> <本地UDP> <节点IP> <节点UDP>\n", argv[0]);
        return 1;
    }
    myaddr = (uint16_t)strtoul(argv[1], NULL, 16);

    sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    {
        struct sockaddr_in local;
        memset(&local, 0, sizeof(local));
        local.sin_family = AF_INET;
        local.sin_addr.s_addr = htonl(INADDR_ANY);
        local.sin_port = htons((uint16_t)atoi(argv[2]));
        if (bind(sockfd, (struct sockaddr *)&local, sizeof(local)) < 0) {
            perror("绑定失败");
            return 1;
        }
    }
    memset(&sw, 0, sizeof(sw));
    sw.sin_family = AF_INET;
    sw.sin_port = htons((uint16_t)atoi(argv[4]));
    inet_pton(AF_INET, argv[3], &sw.sin_addr);

    pthread_create(&tid, NULL, rx_loop, NULL);
    pthread_detach(tid);

    printf("主机0x%04X已就绪, 连接到节点 %s:%s (pid=%d, 编译于 %s %s)\n",
           myaddr, argv[3], argv[4], (int)getpid(), __DATE__, __TIME__);
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
        sendto(sockfd, &f, frame_wire_size(&f), 0,
               (struct sockaddr *)&sw, sizeof(sw));
    }
    return 0;
}
