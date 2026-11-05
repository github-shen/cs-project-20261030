#include <arpa/inet.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "hier.h"

int main(void)
{
    struct in6_addr ip, other; HierPacket p={0}, q; unsigned char wire[HIER_WIRE_MAX+1]; size_t n;
    assert(inet_pton(AF_INET6,"2001:db8:1234:5678:9abc:def0:1234:5678",&ip)==1);
    assert(hier_field(&ip,32,8)==0x12);
    assert(hier_field(&ip,36,12)==0x234);
    assert(hier_field(&ip,28,16)==0x8123);
    assert(hier_field(&ip,120,8)==0x78);
    assert(hier_field(&ip,127,1)==0);
    assert(inet_pton(AF_INET6,"2001:db8:ffff::1",&other)==1);
    assert(hier_prefix_equal(&ip,&other,32));
    assert(!hier_prefix_equal(&ip,&other,33));
    assert(hier_prefix_equal(&ip,&other,0));
    p.src=ip; p.dst=other; p.flow=0x0ab12345; p.ttl=16; p.len=199; memset(p.text,'x',p.len);
    n=hier_encode(&p,wire,0); assert(n==245); assert(!hier_decode(&q,wire,n));
    assert(!q.encapsulated && q.len==199 && q.ttl==16 && !memcmp(&q.src,&p.src,16));
    assert(q.flow==0x0ab12345);
    p.encapsulated=1; p.sid=4; p.pid=5;
    n=hier_encode(&p,wire,0); assert(n==249); assert(!hier_decode(&q,wire,n));
    assert(q.sid==4 && q.pid==5 && q.encapsulated && !memcmp(q.text,p.text,199));
    assert(hier_decode(&q,wire,n-1)==-1);
    wire[n]=0; assert(hier_decode(&q,wire,n+1)==-1);
    hier_encode(&p,wire,1); assert(hier_decode(&q,wire,n)==-2);
    wire[6]=1; assert(!hier_wire_length(wire,n));
    puts("a/b bit vectors, IPv6, EPID, exact lengths and CRC: PASS");
    return 0;
}
