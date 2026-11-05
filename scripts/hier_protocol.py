"""IPv6 实验报文与 a/b 解释；字节序使用网络序，bit 0 为最高位。"""
import binascii
import ipaddress
import struct

UP=65535


def field(address,a,b):
    if not (0<=a<128 and 1<=b<=16 and a+b<=128): raise ValueError('a/b 越界')
    return (int(ipaddress.IPv6Address(address))>>(128-a-b))&((1<<b)-1)


def logical(address,prefix,a,b):
    ip=int(ipaddress.IPv6Address(address)); domain=int(ipaddress.IPv6Address(prefix))
    if a and ip>>(128-a)!=domain>>(128-a): return UP
    return field(address,a,b)


def encode(source,destination,text,epid=None,ttl=16):
    body=text.encode() if isinstance(text,str) else text
    if len(body)>199: raise ValueError('文本最多 199 字节')
    header=b'HN\x01'+bytes([bool(epid)])
    if epid: header+=bytes(epid)+b'\0\0'
    packet=struct.pack('!IHBB',0x60000000,len(body),253,ttl)
    packet+=ipaddress.IPv6Address(source).packed+ipaddress.IPv6Address(destination).packed+body
    wire=header+packet
    return wire+struct.pack('!H',binascii.crc_hqx(wire,0xffff))


def decode(wire):
    if len(wire)<46 or len(wire)>249 or wire[:3]!=b'HN\x01' or wire[3] not in (0,1):
        raise ValueError('帧长度或版本非法')
    offset=8 if wire[3] else 4
    if len(wire)<offset+42 or wire[offset]>>4!=6 or wire[offset+6]!=253:
        raise ValueError('IPv6 报文非法')
    if wire[3] and (not wire[4] or wire[6:8]!=b'\0\0'): raise ValueError('EPID 非法')
    length=struct.unpack('!H',wire[offset+4:offset+6])[0]
    if length>199 or len(wire)!=offset+42+length: raise ValueError('帧长度非法')
    if binascii.crc_hqx(wire[:-2],0xffff)!=struct.unpack('!H',wire[-2:])[0]: raise ValueError('CRC校验失败')
    return {'source':str(ipaddress.IPv6Address(wire[offset+8:offset+24])),
            'destination':str(ipaddress.IPv6Address(wire[offset+24:offset+40])),
            'epid':tuple(wire[4:6]) if wire[3] else None,'ttl':wire[offset+7],
            'body':wire[offset+40:-2]}
