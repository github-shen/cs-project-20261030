/* switch.c —— 交换网络中的逻辑交换节点
 *
 * 功能（对应实验要求）：
 *  (1) 维护"外部端口表"：本节点哪些外部端口连着哪个外部网络（逻辑信道）；
 *  (2) 实现"外部转发"：目的网络在本域之外时，从外部端口送出；
 *  (3) 维护"内部转发表"并实现"内部转发"：帧在本节点域内逐跳转发；
 *  (4) 负载均衡：转发表项可含多条等价端口，按 (源^目的) 哈希分流；
 *  (5) 源地址校验：从外部端口进入的帧，源网络号必须等于该端口所连网络；
 *  (6) 数据包校验：CRC-16，损坏帧丢弃并计数。
 *
 * 端口之间的"链路"用 UDP 报文模拟：单机调试时用 127.0.0.1，
 * 实验室部署时把配置中的对端 IP 换成真实机器 IP 即可（有线/WiFi 通用）。
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <pthread.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include "frame.h"

#define MAX_PORTS 8     /* 每个节点最多端口数 */
#define MAX_IFT   64    /* 内部转发表最大表项数 */
#define MAX_LB    2     /* 每个表项最多等价端口数（负载均衡） */

/* ---------------- 端口 ---------------- */
typedef struct {
    int  id;                 /* 端口号 */
    int  is_ext;             /* 1=外部端口 0=内部端口 */
    uint8_t ext_net;         /* 外部端口所连网络号（仅外部端口有效） */
    uint16_t lport;          /* 本地绑定的 UDP 端口（排错用） */
    char peer_ip[24];        /* 对端 IP 字符串（排错用） */
    int  sock;               /* 该端口绑定的 UDP socket */
    struct sockaddr_in peer; /* 对端地址 */
    int  has_peer;           /* 是否配置了对端 */
} Port;

/* ---------------- 内部转发表项 ---------------- */
typedef struct {
    uint16_t key;            /* 本地网络用完整目的地址(0x0102)，外部网络用网络号左移8位(0x0B00) */
    int      n;              /* 等价端口个数 */
    int      port[MAX_LB];   /* 等价端口列表 */
} IftEntry;

static Port     ports[MAX_PORTS];
static int      nports = 0;
static IftEntry ift[MAX_IFT];
static int      nift = 0;
static int      myid;
static uint16_t myaddr;

/* 统计计数 */
static unsigned long st_rx, st_tx, st_crc_err, st_src_err, st_noroute,
                     st_ttl_drop, st_deliver;

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

/* 内部转发表查找 + 负载均衡选择 */
static int ift_lookup(uint16_t key, uint16_t src, uint16_t dst)
{
    int i;
    for (i = 0; i < nift; i++) {
        if (ift[i].key == key) {
            /* 同一数据流(源,目的)固定走同一条路径，不同流散到不同端口 */
            int idx = (src ^ dst) % ift[i].n;
            return ift[i].port[idx];
        }
    }
    return -1;
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

/* 从端口 p 把帧发给对端 */
static void port_send(Port *p, Frame *f)
{
    if (!p->has_peer) {
        log_line("[DROP] 端口%d未连接对端", p->id);
        return;
    }
    f->ttl--;
    if (f->ttl == 0) {
        st_ttl_drop++;
        log_line("[DROP] TTL超期 dst=0x%04X (端口%d)", f->dst, p->id);
        return;
    }
    /* ttl 属于 CRC 覆盖范围，改了就必须重新封 CRC，否则下一跳校验必然失败 */
    frame_seal(f);
    sendto(p->sock, f, frame_wire_size(f), 0,
           (struct sockaddr *)&p->peer, sizeof(p->peer));
    st_tx++;
}

/* 帧的路由决策。in_port=-1 表示本节点自己产生的帧 */
static void route_frame(Frame *f, int in_port)
{
    uint8_t net  = (uint8_t)(f->dst >> 8);
    uint8_t node = (uint8_t)(f->dst & 0xFF);
    int out;
    Port *p;

    /* 1) 目的在本节点域内 */
    if (net == NET_LOCAL) {
        if (node == myid) {                 /* 就是发给本节点的，交付 */
            st_deliver++;
            log_line("[RECV] 交付本节点: src=0x%04X 内容=\"%s\"", f->src, f->payload);
            return;
        }
        out = ift_lookup(f->dst, f->src, f->dst);   /* 内部转发表按完整目的地址查 */
        if (out < 0)
            goto no_route;
        p = find_port(out);
        if (p == NULL)                  /* 表项引用了不存在的端口 */
            goto no_route;
        log_line("[FWD]  内部转发: 入端口%d -> 出端口%d dst=0x%04X", in_port, out, f->dst);
        port_send(p, f);
        return;
    }

    /* 2) 目的在外部网络：先查外部端口表（直接相连则送出） */
    p = ept_lookup(net);
    if (p != NULL) {
        log_line("[FWD]  外部转发: 入端口%d -> 外部端口%d(网络0x%02X) dst=0x%04X",
                 in_port, p->id, net, f->dst);
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
    log_line("[FWD]  内部转发(跨域途中): 入端口%d -> 出端口%d dst=0x%04X", in_port, out, f->dst);
    port_send(p, f);
    return;

no_route:
    st_noroute++;
    log_line("[DROP] 无路由 dst=0x%04X src=0x%04X", f->dst, f->src);
}

/* 每个端口一个接收线程 */
static void *rx_loop(void *arg)
{
    Port *p = (Port *)arg;
    Frame f;
    ssize_t n;

    for (;;) {
        n = recv(p->sock, &f, sizeof(f), 0);
        if (n < (ssize_t)FRAME_HDR_SIZE)
            continue;
        st_rx++;

        /* (6) 数据包校验 */
        if (f.len > MAX_PAYLOAD || !frame_verify(&f)) {
            st_crc_err++;
            log_line("[DROP] CRC校验失败 (端口%d)", p->id);
            continue;
        }
        /* (5) 源地址校验：外部端口进来的帧，源网络必须等于该端口所连网络 */
        if (p->is_ext && (uint8_t)(f.src >> 8) != p->ext_net) {
            st_src_err++;
            log_line("[DROP] 源地址校验失败: 端口%d连网络0x%02X, 但帧源地址0x%04X",
                     p->id, p->ext_net, f.src);
            continue;
        }
        if (f.len < MAX_PAYLOAD)
            f.payload[f.len] = '\0';    /* 保证打印安全 */
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
            /* port <端口号> <int|ext> [外部网络号] <本地UDP> <对端IP> <对端UDP> */
            char *sid, *stype, *snet, *sludp, *sip, *sudp;
            Port *p;
            sid   = strtok_r(NULL, " \t\n", &save);
            stype = strtok_r(NULL, " \t\n", &save);
            if (!sid || !stype) continue;
            p = &ports[nports++];
            memset(p, 0, sizeof(*p));
            p->id = atoi(sid);
            p->is_ext = (strcmp(stype, "ext") == 0);
            if (p->is_ext) {
                snet  = strtok_r(NULL, " \t\n", &save);
                p->ext_net = (uint8_t)strtoul(snet, NULL, 16);
            }
            sludp = strtok_r(NULL, " \t\n", &save);
            sip   = strtok_r(NULL, " \t\n", &save);
            sudp  = strtok_r(NULL, " \t\n", &save);

            p->sock = socket(AF_INET, SOCK_DGRAM, 0);
            {
                struct sockaddr_in local;
                memset(&local, 0, sizeof(local));
                local.sin_family = AF_INET;
                local.sin_addr.s_addr = htonl(INADDR_ANY);
                local.sin_port = htons((uint16_t)atoi(sludp));
                if (bind(p->sock, (struct sockaddr *)&local, sizeof(local)) < 0) {
                    perror("绑定端口失败");
                    return -1;
                }
            }
            if (sip && sudp && strcmp(sip, "-") != 0) {
                memset(&p->peer, 0, sizeof(p->peer));
                p->peer.sin_family = AF_INET;
                p->peer.sin_port = htons((uint16_t)atoi(sudp));
                inet_pton(AF_INET, sip, &p->peer.sin_addr);
                p->has_peer = 1;
            }
        } else if (strcmp(tok, "ift") == 0) {
            /* ift <目的键(hex)> <端口1[,端口2]> */
            char *skey = strtok_r(NULL, " \t\n", &save);
            char *slist = strtok_r(NULL, " \t\n", &save);
            IftEntry *e;
            char *lsave = NULL, *pt;
            if (!skey || !slist) continue;
            e = &ift[nift++];
            e->key = (uint16_t)strtoul(skey, NULL, 16);
            e->n = 0;
            for (pt = strtok_r(slist, ",", &lsave);
                 pt && e->n < MAX_LB;
                 pt = strtok_r(NULL, ",", &lsave))
                e->port[e->n++] = atoi(pt);
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
    printf("  网络号    外部端口\n");
    for (i = 0; i < nports; i++)
        if (ports[i].is_ext)
            printf("  0x%02X      %d\n", ports[i].ext_net, ports[i].id);
    printf("==== 内部转发表 ====\n");
    printf("  目的键      等价端口(负载均衡)\n");
    for (i = 0; i < nift; i++) {
        printf("  0x%04X    ", ift[i].key);
        for (j = 0; j < ift[i].n; j++)
            printf("%d%s", ift[i].port[j], j + 1 < ift[i].n ? " / " : "");
        putchar('\n');
    }
}

static void print_stats(void)
{
    printf("接收=%lu 发送=%lu 交付=%lu CRC错误=%lu 源地址错误=%lu 无路由=%lu TTL超期=%lu\n",
           st_rx, st_tx, st_deliver, st_crc_err, st_src_err, st_noroute, st_ttl_drop);
}

static void print_help(void)
{
    printf("命令:\n"
           "  send <目的地址hex> <内容>   发送数据帧, 如 send 0x0B01 hello\n"
           "  sendbad <目的地址hex> <内容> 发送CRC故意损坏的帧(演示数据包校验)\n"
           "  table                       显示外部端口表和内部转发表\n"
           "  stats                       显示统计计数\n"
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
        pthread_create(&tid, NULL, rx_loop, &ports[i]);
        pthread_detach(tid);
    }

    printf("节点%d已启动, 本机地址0x%04X, 共%d个端口 (pid=%d, 编译于 %s %s)\n",
           myid, myaddr, nports, (int)getpid(), __DATE__, __TIME__);
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
