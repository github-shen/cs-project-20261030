#include <stdio.h>
#include <string.h>
#include "frame.h"

uint16_t crc16_ccitt(const void *buf, int len)
{
    const unsigned char *p = (const unsigned char *)buf;
    uint16_t crc = 0xFFFF;
    int i, bit;

    for (i = 0; i < len; i++) {
        crc ^= (uint16_t)p[i] << 8;
        for (bit = 0; bit < 8; bit++) {
            if (crc & 0x8000)
                crc = (uint16_t)((crc << 1) ^ 0x1021);
            else
                crc = (uint16_t)(crc << 1);
        }
    }
    return crc;
}

/* 帧的 CRC 计算范围：整个帧头(8字节, 其中 crc 字段置0) + payload 实际长度。
 * 注意 CAN NOT 用 (FRAME_HDR_SIZE - sizeof(crc)) + len 作为长度：
 * 那样会从偏移 6 开始往后数，把 crc 字段的 2 字节算进去、
 * 却漏掉 payload 的起始 2 字节，且发送端 crc 为 0、接收端 crc 为实际值，
 * 两端结果必然不同 —— 任何非空 payload 都会校验失败。 */
static uint16_t frame_crc(const Frame *f)
{
    Frame tmp = *f;
    tmp.crc = 0;
    return crc16_ccitt(&tmp, (int)FRAME_HDR_SIZE + f->len);
}

void frame_fill(Frame *f, uint16_t dst, uint16_t src, const char *data)
{
    int n = (int)strlen(data);
    if (n > MAX_PAYLOAD - 1)
        n = MAX_PAYLOAD - 1;
    memset(f, 0, sizeof(*f));
    f->dst = dst;
    f->src = src;
    f->ttl = DEFAULT_TTL;
    f->len = (uint8_t)n;
    memcpy(f->payload, data, n);
    f->crc = frame_crc(f);
}

int frame_verify(const Frame *f)
{
    return frame_crc(f) == f->crc;
}

void frame_seal(Frame *f)
{
    f->crc = frame_crc(f);
}

int frame_wire_size(const Frame *f)
{
    return (int)FRAME_HDR_SIZE + f->len;
}

const char *addr_str(uint16_t a, char *buf)
{
    snprintf(buf, 8, "0x%04X", a & 0xFFFF);
    return buf;
}
