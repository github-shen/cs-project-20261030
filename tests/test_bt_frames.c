/* Exercise the adapter's actual framing gate without a Bluetooth controller. */
#define main bt_channel_program_main
#include "../src/bt_channel.c"
#undef main
#include <assert.h>

int main(void)
{
    unsigned char wire[HIER_WIRE_MAX+1]; Frame legacy={0}; HierPacket packet={0}; size_t n;
    memcpy(&legacy.dst,"HN",2); legacy.src=0x0101; legacy.ttl=8; legacy.len=3;
    memcpy(legacy.payload,"old",3);
    memcpy(wire,&legacy,FRAME_HDR_SIZE+3);
    assert(valid_frame(wire,FRAME_HDR_SIZE+3));
    assert(!valid_frame(wire,FRAME_HDR_SIZE+2));
    assert(!valid_frame(wire,FRAME_HDR_SIZE+4));
    packet.encapsulated=1; packet.sid=4; packet.pid=5; packet.ttl=16; packet.len=199;
    n=hier_encode(&packet,wire,0); assert(n==249 && valid_frame(wire,(ssize_t)n));
    assert(!valid_frame(wire,(ssize_t)n-1)); assert(!valid_frame(wire,(ssize_t)n+1));
    hier_encode(&packet,wire,1); assert(valid_frame(wire,(ssize_t)n));
    puts("Bluetooth legacy HN address, IPv6/EPID lengths, CRC passthrough: PASS");
    return 0;
}
