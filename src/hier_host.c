#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include "hier.h"

static int udp_port(const char *s, uint16_t *out)
{
    char *end; unsigned long n;
    if (!s || !*s || *s=='-') return -1;
    errno=0; n=strtoul(s,&end,10);
    if (errno || *end || !n || n>65535) return -1;
    *out=htons((uint16_t)n); return 0;
}

int hier_host_main(int argc, char **argv)
{
    struct in6_addr address;
    struct sockaddr_in local={0}, peer={0};
    struct pollfd fds[2]; int fd=-1, i, result=1;
    const char *dev=NULL;
    setvbuf(stdout,NULL,_IOLBF,0); setvbuf(stdin,NULL,_IONBF,0);
    if (argc<5 || inet_pton(AF_INET6,argv[1],&address)!=1 || udp_port(argv[2],&local.sin_port) ||
        udp_port(argv[4],&peer.sin_port) || inet_pton(AF_INET,argv[3],&peer.sin_addr)!=1) goto bad;
    local.sin_family=AF_INET; peer.sin_family=AF_INET;
    for (i=5;i<argc;i+=2) {
        if (i+1>=argc) goto bad;
        if (!strcmp(argv[i],"bind")) { if (inet_pton(AF_INET,argv[i+1],&local.sin_addr)!=1) goto bad; }
        else if (!strcmp(argv[i],"dev")) dev=argv[i+1];
        else goto bad;
    }
    fd=socket(AF_INET,SOCK_DGRAM,0); if (fd<0) goto finish;
#ifdef SO_BINDTODEVICE
    if (dev && setsockopt(fd,SOL_SOCKET,SO_BINDTODEVICE,dev,(socklen_t)strlen(dev))<0) { perror("SO_BINDTODEVICE"); goto finish; }
#else
    (void)dev;
#endif
    if (bind(fd,(struct sockaddr *)&local,sizeof(local))<0) { perror("bind"); goto finish; }
    fds[0].fd=STDIN_FILENO; fds[0].events=POLLIN; fds[1].fd=fd; fds[1].events=POLLIN;
    printf("主机%s已就绪\n命令: send <IPv6> <文本> | sendbad <IPv6> <文本> | quit\n",argv[1]);
    for (;;) {
        int ready=poll(fds,2,-1);
        if (ready<0) { if (errno==EINTR) continue; goto finish; }
        if (fds[0].revents&(POLLIN|POLLHUP)) {
            char line[512], *cmd, *dst, *body, *save=NULL; HierPacket p={0};
            unsigned char wire[HIER_WIRE_MAX]; size_t size;
            if (!fgets(line,sizeof(line),stdin)) break;
            cmd=strtok_r(line," \t\r\n",&save); if (!cmd) continue;
            if (!strcmp(cmd,"quit")) break;
            dst=strtok_r(NULL," \t\r\n",&save); body=strtok_r(NULL,"\r\n",&save);
            if ((strcmp(cmd,"send") && strcmp(cmd,"sendbad")) || !dst || !body || inet_pton(AF_INET6,dst,&p.dst)!=1) {
                puts("[ERROR] 用法: send|sendbad <IPv6> <文本>"); continue;
            }
            while (*body==' ') body++;
            p.src=address; p.ttl=16; p.len=(uint16_t)strnlen(body,HIER_TEXT_MAX); memcpy(p.text,body,p.len);
            size=hier_encode(&p,wire,!strcmp(cmd,"sendbad"));
            if (sendto(fd,wire,size,0,(struct sockaddr *)&peer,sizeof(peer))!=(ssize_t)size) perror("sendto");
        }
        if (fds[1].revents&POLLIN) {
            unsigned char wire[HIER_WIRE_MAX+1]; HierPacket p; char src[INET6_ADDRSTRLEN]; int error;
            ssize_t size=recv(fd,wire,sizeof(wire),0); if (size<0) continue;
            error=hier_decode(&p,wire,(size_t)size);
            if (error) { puts(error==-2?"[DROP] CRC校验失败":"[DROP] 帧长度或格式非法"); continue; }
            if (p.encapsulated || !p.ttl || memcmp(&p.dst,&address,16)) { puts("[DROP] 非法封装或目的地址"); continue; }
            inet_ntop(AF_INET6,&p.src,src,sizeof(src));
            printf("[RECV] 来自%s: \"%s\"\n",src,p.text);
        }
    }
    result=0;
finish:
    if (fd>=0) close(fd);
    return result;
bad:
    fputs("用法: host <IPv6> <本地UDP> <信道IP> <信道UDP> [bind <IPv4>] [dev <网卡>]\n",stderr);
    return 1;
}
