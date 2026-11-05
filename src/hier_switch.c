#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <net/if.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "hier.h"

#define HPORTS 32
#define HEPT 128
#define HROUTES 255
#define HNEXT 4
typedef struct { unsigned id, external, lport; int fd, up; struct sockaddr_in local, peer; char dev[IF_NAMESIZE]; } HPort;
typedef struct { unsigned lport, sid, pid, bandwidth, status; } HExternal;
typedef struct { unsigned pid, hops, status; } HNext;
typedef struct { unsigned sid, count; HNext next[HNEXT]; } HRoute;
typedef struct { unsigned sid; struct in6_addr address; } HNode;
typedef struct {
    unsigned id, a, b, have_ab, have_domain, nports, nept, nroutes, nnodes;
    struct in6_addr domain;
    HPort ports[HPORTS]; HExternal ept[HEPT]; HRoute routes[HROUTES]; HNode nodes[HROUTES];
    unsigned long received, sent, delivered, crc, source, invalid, no_route, ttl, failover;
} HSwitch;

static int uint_value(const char *text, int base, unsigned max, unsigned *out)
{
    unsigned long n; char *end;
    if (!text || !*text || *text=='-') return -1;
    errno=0; n=strtoul(text,&end,base);
    if (errno || *end || n>max) return -1;
    *out=(unsigned)n; return 0;
}
static int number(const char *text, unsigned max, unsigned *out) { return uint_value(text,10,max,out); }
static HPort *port(HSwitch *s, unsigned pid)
{
    unsigned i; for (i=0;i<s->nports;i++) if (s->ports[i].id==pid) return &s->ports[i];
    return NULL;
}
static HNode *node(HSwitch *s, unsigned sid)
{
    unsigned i; for (i=0;i<s->nnodes;i++) if (s->nodes[i].sid==sid) return &s->nodes[i];
    return NULL;
}
static unsigned destination_node(HSwitch *s, const struct in6_addr *address)
{
    unsigned i; for (i=0;i<s->nnodes;i++) if (!memcmp(&s->nodes[i].address,address,16)) return s->nodes[i].sid;
    return 0;
}
static unsigned logical_channel(HSwitch *s, const struct in6_addr *address)
{
    return hier_prefix_equal(address,&s->domain,s->a)?hier_field(address,s->a,s->b):HIER_UP;
}
static int ip_port(struct sockaddr_in *out, const char *ip, const char *udp)
{
    unsigned n;
    if (number(udp,65535,&n) || !n || inet_pton(AF_INET,ip,&out->sin_addr)!=1) return -1;
    out->sin_family=AF_INET; out->sin_port=htons((uint16_t)n); return 0;
}

int hier_config_enabled(const char *path)
{
    char line[512], word[32]; FILE *f=fopen(path,"r");
    if (!f) return 0;
    while (fgets(line,sizeof(line),f))
        if (sscanf(line,"%31s",word)==1 && !strcmp(word,"ab")) { fclose(f); return 1; }
    fclose(f); return 0;
}

static int load(HSwitch *s, const char *path)
{
    char line[1024]; unsigned lineno=0; FILE *f=fopen(path,"r");
    if (!f) { perror(path); return -1; }
    while (fgets(line,sizeof(line),f)) {
        char *w[32], *save=NULL, *token, *comment=strchr(line,'#'); unsigned n=0;
        lineno++; if (comment) *comment='\0';
        for (token=strtok_r(line," \t\r\n",&save); token; token=strtok_r(NULL," \t\r\n",&save)) {
            if (n==32) goto bad;
            w[n++]=token;
        }
        if (!n) continue;
        if (!strcmp(w[0],"id")) {
            if (n!=2 || s->id || number(w[1],255,&s->id) || !s->id) goto bad;
        } else if (!strcmp(w[0],"ab")) {
            char *slash;
            if (n!=2 || s->have_ab || !(slash=strchr(w[1],'/'))) goto bad;
            *slash++='\0';
            if (number(w[1],127,&s->a) || number(slash,16,&s->b) || !s->b || s->a+s->b>128) goto bad;
            s->have_ab=1;
        } else if (!strcmp(w[0],"domain")) {
            if (n!=2 || s->have_domain || inet_pton(AF_INET6,w[1],&s->domain)!=1) goto bad;
            s->have_domain=1;
        } else if (!strcmp(w[0],"node")) {
            HNode *entry;
            if (n!=3 || s->nnodes==HROUTES) goto bad;
            entry=&s->nodes[s->nnodes];
            if (number(w[1],255,&entry->sid) || !entry->sid || node(s,entry->sid) ||
                inet_pton(AF_INET6,w[2],&entry->address)!=1 || destination_node(s,&entry->address)) goto bad;
            s->nnodes++;
        } else if (!strcmp(w[0],"port")) {
            HPort *p; unsigned i;
            if (n<6 || s->nports==HPORTS) goto bad;
            p=&s->ports[s->nports]; p->fd=-1; p->up=1;
            if (number(w[1],255,&p->id) || !p->id || port(s,p->id)) goto bad;
            if (!strcmp(w[2],"ext")) {
                p->external=1;
                if (n<7 || uint_value(w[3],16,65535,&p->lport)) goto bad;
                i=4;
            } else if (!strcmp(w[2],"int")) i=3;
            else goto bad;
            if (ip_port(&p->local,"0.0.0.0",w[i]) || ip_port(&p->peer,w[i+1],w[i+2])) goto bad;
            for (i+=3;i<n;i+=2) {
                if (i+1>=n) goto bad;
                if (!strcmp(w[i],"bind")) {
                    if (inet_pton(AF_INET,w[i+1],&p->local.sin_addr)!=1) goto bad;
                } else if (!strcmp(w[i],"dev")) {
                    if (strlen(w[i+1])>=sizeof(p->dev)) goto bad;
                    strcpy(p->dev,w[i+1]);
                } else if (!strcmp(w[i],"type")) {
                    if (strcmp(w[i+1],"eth") && strcmp(w[i+1],"wifi") && strcmp(w[i+1],"bt") && strcmp(w[i+1],"int")) goto bad;
                } else goto bad;
            }
            s->nports++;
        } else if (!strcmp(w[0],"ept")) {
            HExternal *e;
            if (n!=6 || s->nept==HEPT) goto bad;
            e=&s->ept[s->nept++];
            if (number(w[1],65535,&e->lport) || number(w[2],255,&e->sid) || !e->sid ||
                number(w[3],255,&e->pid) || !e->pid || number(w[4],1000000,&e->bandwidth) ||
                !e->bandwidth || number(w[5],1,&e->status)) goto bad;
        } else if (!strcmp(w[0],"route")) {
            HRoute *r; char *next, *rest=NULL; unsigned j;
            if (n!=3 || s->nroutes==HROUTES) goto bad;
            r=&s->routes[s->nroutes];
            if (number(w[1],255,&r->sid) || !r->sid) goto bad;
            for (j=0;j<s->nroutes;j++) if (s->routes[j].sid==r->sid) goto bad;
            for (next=strtok_r(w[2],",",&rest);next;next=strtok_r(NULL,",",&rest)) {
                HNext *entry; char *hops, *status;
                if (r->count==HNEXT || !(hops=strchr(next,':'))) goto bad;
                *hops++='\0'; if (!(status=strchr(hops,':'))) goto bad; *status++='\0';
                entry=&r->next[r->count++];
                if (number(next,255,&entry->pid) || !entry->pid || number(hops,255,&entry->hops) ||
                    !entry->hops || number(status,1,&entry->status)) goto bad;
            }
            if (!r->count) goto bad;
            s->nroutes++;
        } else goto bad;
    }
    fclose(f);
    if (!s->id || !s->have_ab || !s->have_domain || !node(s,s->id) || !s->nports || !s->nept) {
        fprintf(stderr,"配置错误: 缺少 id/ab/domain/node/port/ept\n"); return -1;
    }
    return 0;
bad:
    fprintf(stderr,"配置错误: %s:%u\n",path,lineno); fclose(f); return -1;
}

static int validate(HSwitch *s)
{
    unsigned i,j,k;
    for (i=0;i<s->nept;i++) {
        HExternal *e=&s->ept[i]; HPort *p=port(s,e->pid);
        if (!node(s,e->sid) || (e->lport!=HIER_UP && e->lport>=(1U<<s->b))) return -1;
        if (e->sid==s->id && (!p || !p->external || p->lport!=e->lport)) return -1;
        for (j=i+1;j<s->nept;j++) if (e->sid==s->ept[j].sid && e->pid==s->ept[j].pid) return -1;
    }
    for (i=0;i<s->nports;i++) {
        HPort *p=&s->ports[i]; unsigned found=0;
        for (j=i+1;j<s->nports;j++)
            if (p->local.sin_port==s->ports[j].local.sin_port && p->local.sin_addr.s_addr==s->ports[j].local.sin_addr.s_addr) return -1;
        if (!p->external) continue;
        for (j=0;j<s->nept;j++) found+=s->ept[j].sid==s->id && s->ept[j].pid==p->id;
        if (found!=1) return -1;
    }
    for (i=0;i<s->nroutes;i++) {
        HRoute *r=&s->routes[i];
        if (!node(s,r->sid) || r->sid==s->id) return -1;
        for (j=0;j<r->count;j++) {
            HPort *p=port(s,r->next[j].pid);
            if (!p || p->external) return -1;
            for (k=j+1;k<r->count;k++) if (r->next[j].pid==r->next[k].pid) return -1;
        }
    }
    for (i=0;i<s->nnodes;i++) if (s->nodes[i].sid!=s->id) {
        unsigned found=0;
        for (j=0;j<s->nroutes;j++) found+=s->routes[j].sid==s->nodes[i].sid;
        if (found!=1) return -1;
    }
    return 0;
}

static void tables(HSwitch *s)
{
    unsigned i,j; char address[INET6_ADDRSTRLEN];
    inet_ntop(AF_INET6,&s->domain,address,sizeof(address));
    printf("a/b=%u/%u domain=%s（从最高位 bit 0 开始）\n外部端口表 Lport SID PID Bandwidth Status\n",s->a,s->b,address);
    for (i=0;i<s->nept;i++) { HExternal *e=&s->ept[i]; printf("%u %u %u %u %u\n",e->lport,e->sid,e->pid,e->bandwidth,e->status); }
    printf("内部转发表 SID PID:HOP:Status\n");
    for (i=0;i<s->nroutes;i++) {
        printf("%u",s->routes[i].sid);
        for (j=0;j<s->routes[i].count;j++) { HNext *n=&s->routes[i].next[j]; printf(" %u:%u:%u",n->pid,n->hops,n->status); }
        puts("");
    }
}
static int send_packet(HSwitch *s, HPort *port, const HierPacket *original, int corrupt)
{
    unsigned char wire[HIER_WIRE_MAX]; HierPacket p=*original; size_t size;
    if (!port->up) { errno=ENETDOWN; return -1; }
    if (p.ttl<=1) { s->ttl++; errno=EHOSTUNREACH; return -1; }
    p.ttl--;
    if (port->external) { p.encapsulated=0; p.sid=0; p.pid=0; }
    size=hier_encode(&p,wire,corrupt);
    if (sendto(port->fd,wire,size,0,(struct sockaddr *)&port->peer,sizeof(port->peer))!=(ssize_t)size) return -1;
    s->sent++; return 0;
}

static void forward(HSwitch *s, HierPacket *p, int corrupt)
{
    unsigned i,j; char dst[INET6_ADDRSTRLEN];
    inet_ntop(AF_INET6,&p->dst,dst,sizeof(dst));
    if (p->sid==s->id) {
        HPort *out;
        if (!p->pid) {
            if (corrupt) { s->crc++; puts("[DROP] CRC校验失败（本地交付）"); return; }
            char src[INET6_ADDRSTRLEN]; inet_ntop(AF_INET6,&p->src,src,sizeof(src)); s->delivered++;
            printf("[RECV] 交付本节点: src=%s 内容=\"%s\"\n",src,p->text); return;
        }
        out=port(s,p->pid);
        if (!out || !out->external) goto no_route;
        printf("[FWD] 外部转发: EPID=%u/%u 外部端口%u(逻辑信道%u) dst=%s\n",p->sid,p->pid,out->id,out->lport,dst);
        if (send_packet(s,out,p,corrupt)<0) printf("[DROP] 端口%u发送失败: %s\n",out->id,strerror(errno));
        return;
    }
    for (i=0;i<s->nroutes;i++) if (s->routes[i].sid==p->sid) {
        HRoute *r=&s->routes[i]; unsigned used[HNEXT]={0}, attempts=0;
        for (j=0;j<r->count;j++) {
            unsigned k, best=256, choices[HNEXT], count=0; HPort *out;
            for (k=0;k<r->count;k++) {
                HNext *n=&r->next[k]; out=port(s,n->pid);
                if (used[k] || n->status || !out->up) continue;
                if (n->hops<best) { best=n->hops; count=0; }
                if (n->hops==best) choices[count++]=k;
            }
            if (!count) break;
            k=choices[hier_hash(p)%count]; used[k]=1; out=port(s,r->next[k].pid);
            printf("[FWD] 内部转发: EPID=%u/%u 出端口%u dst=%s\n",p->sid,p->pid,out->id,dst);
            if (send_packet(s,out,p,corrupt)==0) {
                if (attempts) { s->failover++; printf("[REROUTE] 改走端口%u EPID=%u/%u\n",out->id,p->sid,p->pid); }
                return;
            }
            attempts++;
        }
        break;
    }
no_route:
    s->no_route++; printf("[DROP] 无路由或信道不可用 EPID=%u/%u dst=%s\n",p->sid,p->pid,dst);
}

static int epid_valid(HSwitch *s, const HierPacket *p)
{
    unsigned i;
    if (!p->pid) return destination_node(s,&p->dst)==p->sid;
    for (i=0;i<s->nept;i++) if (s->ept[i].sid==p->sid && s->ept[i].pid==p->pid)
        return s->ept[i].lport==logical_channel(s,&p->dst) && !s->ept[i].status;
    return 0;
}

static void enter(HSwitch *s, HierPacket *p, HPort *in, int corrupt)
{
    unsigned i, lport, total=0, pick;
    if (in && !in->external) {
        if (!p->encapsulated || !epid_valid(s,p)) { s->invalid++; puts("[DROP] 非法 EPID"); return; }
        forward(s,p,0); return;
    }
    if (p->encapsulated) { s->invalid++; puts("[DROP] 外部输入不允许携带 EPID"); return; }
    if (in && logical_channel(s,&p->src)!=in->lport) {
        s->source++; puts("[DROP] 源地址校验失败（前缀 / a/b 字段与入口不符）"); return;
    }
    p->encapsulated=1; p->sid=(uint8_t)destination_node(s,&p->dst); p->pid=0;
    if (p->sid) { forward(s,p,corrupt); return; }
    lport=logical_channel(s,&p->dst);
    if (in && in->lport==HIER_UP && lport==HIER_UP) {
        s->no_route++; puts("[DROP] 上行输入目的不在本域，拒绝回送上行"); return;
    }
    for (i=0;i<s->nept;i++) if (s->ept[i].lport==lport && !s->ept[i].status) {
        HExternal *e=&s->ept[i]; HPort *local=e->sid==s->id?port(s,e->pid):NULL;
        if (!local || local->up) total+=e->bandwidth;
    }
    if (!total) { s->no_route++; printf("[DROP] 无路由 Lport=%u\n",lport); return; }
    pick=hier_hash(p)%total;
    for (i=0;i<s->nept;i++) {
        HExternal *e=&s->ept[i]; HPort *local=e->sid==s->id?port(s,e->pid):NULL;
        if (e->lport!=lport || e->status || (local && !local->up)) continue;
        if (pick<e->bandwidth) {
            p->sid=(uint8_t)e->sid; p->pid=(uint8_t)e->pid;
            printf("[SELECT] Lport=%u EPID=%u/%u a/b=%u/%u\n",lport,p->sid,p->pid,s->a,s->b);
            forward(s,p,corrupt); return;
        }
        pick-=e->bandwidth;
    }
}

static int control(HSwitch *s, char *line)
{
    char *save=NULL, *cmd=strtok_r(line," \t\r\n",&save), *arg;
    if (!cmd) return 1;
    if (!strcmp(cmd,"quit")) return 0;
    if (!strcmp(cmd,"table")) { tables(s); return 1; }
    if (!strcmp(cmd,"stats")) {
        printf("接收=%lu 发送=%lu 交付=%lu CRC错误=%lu 源地址错误=%lu 非法=%lu 无路由=%lu TTL丢弃=%lu 重路由=%lu\n",
               s->received,s->sent,s->delivered,s->crc,s->source,s->invalid,s->no_route,s->ttl,s->failover); return 1;
    }
    if (!strcmp(cmd,"send") || !strcmp(cmd,"sendbad")) {
        HierPacket p={0}; char *data;
        arg=strtok_r(NULL," \t\r\n",&save); data=strtok_r(NULL,"\r\n",&save);
        if (!arg || !data || inet_pton(AF_INET6,arg,&p.dst)!=1) goto help;
        while (*data==' ') data++;
        p.src=node(s,s->id)->address; p.ttl=16; p.len=(uint16_t)strnlen(data,HIER_TEXT_MAX);
        memcpy(p.text,data,p.len); enter(s,&p,NULL,!strcmp(cmd,"sendbad")); return 1;
    }
    if (!strcmp(cmd,"link")) {
        unsigned pid; HPort *p; char *state;
        arg=strtok_r(NULL," \t\r\n",&save); state=strtok_r(NULL," \t\r\n",&save);
        if (number(arg,255,&pid) || !(p=port(s,pid)) || !state || (strcmp(state,"up") && strcmp(state,"down"))) goto help;
        p->up=!strcmp(state,"up"); printf("[STATE] port=%u %s\n",pid,state); return 1;
    }
    if (!strcmp(cmd,"ept")) {
        unsigned sid,pid,i; char *pid_text, *state;
        arg=strtok_r(NULL," \t\r\n",&save); pid_text=strtok_r(NULL," \t\r\n",&save); state=strtok_r(NULL," \t\r\n",&save);
        if (number(arg,255,&sid) || number(pid_text,255,&pid) || !state || (strcmp(state,"up") && strcmp(state,"down"))) goto help;
        for (i=0;i<s->nept;i++) if (s->ept[i].sid==sid && s->ept[i].pid==pid) {
            s->ept[i].status=(unsigned)strcmp(state,"up")!=0; printf("[STATE] EPID=%u/%u %s\n",sid,pid,state); return 1;
        }
    }
help:
    puts("命令: send <IPv6> <文本> | sendbad <IPv6> <文本> | table | stats | link <PID> up|down | ept <SID> <PID> up|down | quit");
    return 1;
}

int hier_switch_main(int argc, char **argv)
{
    HSwitch s={0}; unsigned i; int result=1; struct pollfd fds[HPORTS+1];
    (void)argc; setvbuf(stdout,NULL,_IOLBF,0); setvbuf(stdin,NULL,_IONBF,0);
    if (load(&s,argv[1]) || validate(&s)) { fputs("配置错误: 层次网络表不一致\n",stderr); return 1; }
    for (i=0;i<s.nports;i++) {
        HPort *p=&s.ports[i]; p->fd=socket(AF_INET,SOCK_DGRAM,0);
        if (p->fd<0) goto finish;
#ifdef SO_BINDTODEVICE
        if (*p->dev && setsockopt(p->fd,SOL_SOCKET,SO_BINDTODEVICE,p->dev,(socklen_t)strlen(p->dev))<0) {
            perror("SO_BINDTODEVICE"); goto finish;
        }
#endif
        if (bind(p->fd,(struct sockaddr *)&p->local,sizeof(p->local))<0) { perror("bind"); goto finish; }
        fds[i+1].fd=p->fd; fds[i+1].events=POLLIN;
    }
    fds[0].fd=STDIN_FILENO; fds[0].events=POLLIN;
    printf("节点%u已启动 a/b=%u/%u IPv6 共%u个端口\n",s.id,s.a,s.b,s.nports);
    { char help[]="help"; control(&s,help); }
    for (;;) {
        int ready=poll(fds,s.nports+1,-1);
        if (ready<0) { if (errno==EINTR) continue; goto finish; }
        if (fds[0].revents&(POLLIN|POLLHUP)) {
            char line[512]; if (!fgets(line,sizeof(line),stdin) || !control(&s,line)) break;
        }
        for (i=0;i<s.nports;i++) if (fds[i+1].revents&POLLIN) {
            unsigned char wire[HIER_WIRE_MAX+1]; struct sockaddr_in sender; socklen_t size=sizeof(sender);
            HPort *p=&s.ports[i]; HierPacket packet; ssize_t n; int decoded;
            n=recvfrom(p->fd,wire,sizeof(wire),0,(struct sockaddr *)&sender,&size);
            if (n<0) continue;
            s.received++;
            decoded=hier_decode(&packet,wire,(size_t)n);
            if (decoded) { if (decoded==-2) s.crc++; else s.invalid++; puts(decoded==-2?"[DROP] CRC校验失败":"[DROP] 帧长度或格式非法"); continue; }
            if (!packet.ttl) { s.ttl++; puts("[DROP] TTL 耗尽"); continue; }
            if (!p->up) { s.invalid++; puts("[DROP] 输入端口已停用"); continue; }
            if (!p->external && (sender.sin_port!=p->peer.sin_port || sender.sin_addr.s_addr!=p->peer.sin_addr.s_addr)) {
                s.source++; puts("[DROP] 内部输入来源不是配置邻居"); continue;
            }
            enter(&s,&packet,p,0);
        }
    }
    result=0;
finish:
    for (i=0;i<s.nports;i++) if (s.ports[i].fd>=0) close(s.ports[i].fd);
    return result;
}
