#include <string.h>
#include "frame.h"
#include "hier.h"

static uint16_t get16(const unsigned char *p) { return (uint16_t)((p[0] << 8) | p[1]); }
static void put16(unsigned char *p, uint16_t n) { p[0]=(unsigned char)(n>>8); p[1]=(unsigned char)n; }

int hier_wire_length(const unsigned char *buf, size_t n)
{
    size_t ip;
    unsigned size;
    if (n < 46 || n > HIER_WIRE_MAX || buf[0]!='H' || buf[1]!='N' || buf[2]!=1 || buf[3]>1)
        return 0;
    ip=buf[3] ? 8 : 4;
    if (n < ip+42 || (buf[ip] >> 4)!=6 || buf[ip+6]!=253) return 0;
    if (buf[3] && (!buf[4] || buf[6] || buf[7])) return 0;
    size=get16(buf+ip+4);
    return size<=HIER_TEXT_MAX && n==ip+40+size+2;
}

int hier_decode(HierPacket *p, const unsigned char *buf, size_t n)
{
    size_t ip;
    if (!hier_wire_length(buf,n)) return -1;
    if (crc16_ccitt(buf,(int)n-2)!=get16(buf+n-2)) return -2;
    memset(p,0,sizeof(*p)); p->encapsulated=buf[3]; ip=p->encapsulated?8:4;
    if (p->encapsulated) { p->sid=buf[4]; p->pid=buf[5]; }
    p->flow=((uint32_t)(buf[ip]&15)<<24)|((uint32_t)buf[ip+1]<<16)|((uint32_t)buf[ip+2]<<8)|buf[ip+3];
    p->len=get16(buf+ip+4); p->ttl=buf[ip+7];
    memcpy(&p->src,buf+ip+8,16); memcpy(&p->dst,buf+ip+24,16);
    memcpy(p->text,buf+ip+40,p->len); p->text[p->len]='\0';
    return 0;
}

size_t hier_encode(const HierPacket *p, unsigned char *buf, int corrupt)
{
    size_t ip=p->encapsulated?8:4, n=ip+40+p->len+2;
    if (p->len>HIER_TEXT_MAX) return 0;
    memset(buf,0,n); buf[0]='H'; buf[1]='N'; buf[2]=1; buf[3]=p->encapsulated?1:0;
    if (p->encapsulated) { buf[4]=p->sid; buf[5]=p->pid; }
    buf[ip]=(unsigned char)(0x60|((p->flow>>24)&15)); buf[ip+1]=(unsigned char)(p->flow>>16);
    buf[ip+2]=(unsigned char)(p->flow>>8); buf[ip+3]=(unsigned char)p->flow;
    put16(buf+ip+4,p->len); buf[ip+6]=253; buf[ip+7]=p->ttl;
    memcpy(buf+ip+8,&p->src,16); memcpy(buf+ip+24,&p->dst,16);
    memcpy(buf+ip+40,p->text,p->len);
    put16(buf+n-2,(uint16_t)(crc16_ccitt(buf,(int)n-2) ^ (corrupt?1:0)));
    return n;
}

uint16_t hier_field(const struct in6_addr *address, unsigned a, unsigned b)
{
    uint16_t value=0;
    unsigned i;
    for (i=a;i<a+b;i++) value=(uint16_t)((value<<1)|((address->s6_addr[i/8]>>(7-i%8))&1));
    return value;
}

int hier_prefix_equal(const struct in6_addr *l, const struct in6_addr *r, unsigned bits)
{
    unsigned bytes=bits/8, remainder=bits%8;
    if (memcmp(l->s6_addr,r->s6_addr,bytes)) return 0;
    return !remainder || !((l->s6_addr[bytes]^r->s6_addr[bytes]) & (0xffU<<(8-remainder)));
}

uint32_t hier_hash(const HierPacket *p)
{
    uint32_t h=2166136261U;
    unsigned i;
    for (i=0;i<16;i++) { h^=p->src.s6_addr[i]; h*=16777619U; }
    for (i=0;i<16;i++) { h^=p->dst.s6_addr[i]; h*=16777619U; }
    return h;
}
