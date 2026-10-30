/* frame.h —— 实验帧格式定义与 CRC-16 校验
 * 帧在节点（交换机）之间、节点与外部主机之间传输。
 * 地址为 16 位：高 8 位 = 网络号（逻辑信道/节点域），低 8 位 = 站点号。
 */
#ifndef FRAME_H
#define FRAME_H

#include <stdint.h>

#define MAX_PAYLOAD 200          /* 数据字段最大长度 */

/* 网络号约定（对应题目图中的逻辑信道） */
#define NET_LOCAL   0x01         /* 本节点域（4 台交换机互连的内部网络） */
#define NET_UP      0x0A         /* 上行逻辑信道 */
#define NET_DOWN_L  0x0B         /* 下行逻辑信道 3 */
#define NET_DOWN_R  0x0C         /* 下行逻辑信道 5 */

#define DEFAULT_TTL 8            /* 帧最大跳数，防止成环后无限转发 */

#pragma pack(push, 1)
typedef struct {
    uint16_t dst;                /* 目的地址 */
    uint16_t src;                /* 源地址   */
    uint8_t  ttl;                /* 剩余跳数 */
    uint8_t  len;                /* payload 实际长度 */
    uint16_t crc;                /* 对 dst..payload 计算的 CRC-16-CCITT */
    char     payload[MAX_PAYLOAD];
} Frame;
#pragma pack(pop)

/* 帧头（不含 payload）在线路上的字节数 */
#define FRAME_HDR_SIZE (sizeof(Frame) - MAX_PAYLOAD)

/* CRC-16-CCITT (多项式 0x1021，初值 0xFFFF) */
uint16_t crc16_ccitt(const void *buf, int len);

/* 组装一个待发送的数据帧（自动计算 CRC） */
void frame_fill(Frame *f, uint16_t dst, uint16_t src, const char *data);

/* 校验帧的 CRC，返回 1=正确，0=损坏 */
int frame_verify(const Frame *f);

/* 重新计算并写入帧的 CRC。帧头任何字段(如 ttl)被修改后必须调用，否则接收端校验失败 */
void frame_seal(Frame *f);

/* 帧在线路上实际占用的字节数 */
int frame_wire_size(const Frame *f);

/* 把 16 位地址格式化成 "0x0A01" 形式（buf 至少 8 字节） */
const char *addr_str(uint16_t a, char *buf);

#endif
