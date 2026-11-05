#ifndef HIER_H
#define HIER_H
#include <stddef.h>
#include <stdint.h>
#include <netinet/in.h>

#define HIER_TEXT_MAX 199
#define HIER_WIRE_MAX 249
#define HIER_UP 65535U

/* HN/version/flags + optional SID/PID/RES + IPv6 packet + CRC-16. */
typedef struct {
    struct in6_addr src, dst;
    uint32_t flow; /* IPv6 Traffic Class + Flow Label, low 28 bits. */
    uint8_t sid, pid, encapsulated, ttl;
    uint16_t len;
    char text[HIER_TEXT_MAX + 1];
} HierPacket;

int hier_wire_length(const unsigned char *buf, size_t n);
int hier_decode(HierPacket *packet, const unsigned char *buf, size_t n);
size_t hier_encode(const HierPacket *packet, unsigned char *buf, int corrupt);
uint16_t hier_field(const struct in6_addr *address, unsigned a, unsigned b);
int hier_prefix_equal(const struct in6_addr *left, const struct in6_addr *right, unsigned bits);
uint32_t hier_hash(const HierPacket *packet);
int hier_config_enabled(const char *path);
int hier_switch_main(int argc, char **argv);
int hier_host_main(int argc, char **argv);
#endif
