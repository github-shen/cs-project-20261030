#!/usr/bin/env python3
"""共享外部信道：两个交换节点接入同一网络，主机回程不依赖入口节点。"""
import argparse
import binascii
import json
import ipaddress
import os
from pathlib import Path
import select
import socket
import struct
import sys
import hier_protocol


def address(value):
    return int(value, 16) if isinstance(value, str) else value


def endpoint(value):
    ip, port = value
    socket.inet_pton(socket.AF_INET, ip)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('UDP 端口必须为 1～65535')
    return ip, port


def decode(data):
    if len(data) < 8:
        raise ValueError('帧长度非法')
    dst, src, ttl, size, crc = struct.unpack('=HHBBH', data[:8])
    if size >= 200 or len(data) != 8 + size:
        raise ValueError('帧长度非法')
    if binascii.crc_hqx(data[:6] + b'\0\0' + data[8:], 0xffff) != crc:
        raise ValueError('CRC校验失败')
    return dst, src


class Channel:
    def __init__(self, config):
        self.net = address(config['network'])
        self.ab = config.get('ab')
        self.domain = config.get('domain')
        if self.ab:
            hier_protocol.field(self.domain,*self.ab)
            if self.net != hier_protocol.UP and not 0 <= self.net < 1 << self.ab[1]:
                raise ValueError('逻辑信道号超出 a/b 字段范围')
        elif not 1 < self.net <= 255:
            raise ValueError('外部网络号必须为 02～FF')
        self.listen = endpoint(config['listen'])
        self.nodes = {int(p['node']): endpoint(p['peer']) for p in config['uplinks']}
        if (len(config['uplinks']) != 2 or len(self.nodes) != 2 or
                len(set(self.nodes.values())) != 2 or
                any(not 1 <= n <= 255 for n in self.nodes)):
            raise ValueError('每条题图信道必须连接两个不同节点和端点')
        self.hosts = {}
        for host in config.get('hosts', []):
            addr = str(ipaddress.IPv6Address(host['address'])) if self.ab else address(host['address'])
            logical = hier_protocol.logical(addr,self.domain,*self.ab) if self.ab else addr >> 8
            if logical != self.net or addr in self.hosts:
                raise ValueError('主机地址重复或不属于此信道')
            self.hosts[addr] = endpoint(host['peer'])
        self.gateway = int(config.get('gateway', next(iter(self.nodes))))
        if self.gateway not in self.nodes:
            raise ValueError('入口节点不在此信道上')
        self.counts = {'host_rx': 0, 'node_rx': 0, 'delivered': 0, 'dropped': 0}

    def destination(self, data, sender):
        if self.ab:
            packet=hier_protocol.decode(data)
            if packet['epid'] is not None: raise ValueError('外部信道不接受域内 EPID 封装')
            dst,src=packet['destination'],packet['source']
            source_net=hier_protocol.logical(src,self.domain,*self.ab)
            dest_net=hier_protocol.logical(dst,self.domain,*self.ab)
        else:
            dst, src = decode(data)
            source_net,dest_net=src>>8,dst>>8
        if sender in self.nodes.values():
            self.counts['node_rx'] += 1
            if dest_net != self.net:
                raise ValueError('目的网络不属于此信道')
            if dst not in self.hosts:
                raise ValueError('未学习目的主机地址')
            return self.hosts[dst]
        if source_net != self.net:
            raise ValueError('源地址校验失败')
        # 全部接入节点共享同一主机表，另一节点也能回送新学习的站点。
        self.hosts[src] = sender
        self.counts['host_rx'] += 1
        if dest_net == self.net:
            if dst not in self.hosts:
                raise ValueError('未学习目的主机地址')
            return self.hosts[dst]
        return self.nodes[self.gateway]

    def control(self, line):
        words = line.split()
        if words == ['quit']:
            return False
        if words == ['stats']:
            print('[STATS] ' + json.dumps(self.counts), flush=True)
        elif words == ['table']:
            print('[TABLE] ' + json.dumps({'network': self.net, 'uplinks': self.nodes,
                                           'hosts': self.hosts}), flush=True)
        elif len(words) == 2 and words[0] == 'via' and words[1].isdigit() and int(words[1]) in self.nodes:
            self.gateway = int(words[1])
            print('[GATEWAY] node=' + str(self.gateway), flush=True)
        else:
            print('[ERROR] 命令: via <接入节点号> | table | stats | quit', flush=True)
        return True

    def run(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(self.listen)
            print(f'[READY] channel=0x{self.net:02X} nodes={list(self.nodes)} gateway={self.gateway}', flush=True)
            inputs = [sock, sys.stdin]
            pending=b''
            while True:
                readable, _, _ = select.select(inputs, [], [])
                if sys.stdin in readable:
                    data = os.read(sys.stdin.fileno(),4096)
                    if not data:
                        inputs.remove(sys.stdin)
                    else:
                        pending+=data
                        while b'\n' in pending:
                            line,pending=pending.split(b'\n',1)
                            if not self.control(line.decode('utf-8')): return
                if sock in readable:
                    data, sender = sock.recvfrom(65535)
                    try:
                        target = self.destination(data, sender)
                        # 信道只搬运原帧，不增加逻辑跳数，不重算 TTL/CRC，不重复泛洪。
                        sock.sendto(data, target)
                        self.counts['delivered'] += 1
                    except (ValueError, OSError) as error:
                        self.counts['dropped'] += 1
                        print('[DROP] ' + str(error), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('--network', required=True, type=lambda x: int(x, 16))
    args = parser.parse_args()
    try:
        entries = json.loads(args.config.read_text(encoding='utf-8'))['channels']
        matches = [c for c in entries if address(c['network']) == args.network]
        if len(matches) != 1:
            raise ValueError('信道配置缺失或重复')
        Channel(matches[0]).run()
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
