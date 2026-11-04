#!/usr/bin/env python3
"""仅供双VM实验：在隔离网络上传输虚拟HCI H4报文，不模拟业务交换。"""
import argparse
import os
import select
import signal
import socket
import struct


def packets(buffer):
    """TCP没有消息边界；重组完整H4帧后才写入vhci/BlueZ socket。"""
    while buffer:
        kind = buffer[0]
        header = {1:4, 2:5, 3:4, 4:3}.get(kind)
        if header is None: raise ValueError('不支持的H4包类型: %d'%kind)
        if len(buffer)<header: return
        size = (int.from_bytes(buffer[3:5], 'little') if kind==2 else buffer[header-1])+header
        if len(buffer)<size: return
        yield bytes(buffer[:size])
        del buffer[:size]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['serve','connect'])
    p.add_argument('--address',required=True,help='显式填写监听/远端IPv4；仅隔离实验网络')
    p.add_argument('--peer',help='serve只接受该源IPv4')
    p.add_argument('--port',type=int,default=45678)
    p.add_argument('--unix',default='/tmp/bt-server-bredr')
    opt=p.parse_args()
    socket.inet_pton(socket.AF_INET,opt.address)
    if opt.mode=='serve' and not opt.peer: p.error('serve必须指定--peer')
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    resources=[]; fd=None
    try:
        if opt.mode=='serve':
            listener=socket.socket(); resources.append(listener)
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind((opt.address,opt.port)); listener.listen(1); listener.settimeout(60)
            print('[READY] HCI relay; radio=EMULATED',flush=True)
            network,peer=listener.accept(); resources.append(network)
            if peer[0]!=opt.peer: raise ValueError('拒绝非配置VM')
            local=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); resources.append(local)
            local.connect(opt.unix)
            reader=local.recv; writer=local.sendall; source=local
        else:
            network=socket.create_connection((opt.address,opt.port),timeout=15); resources.append(network)
            fd=os.open('/dev/vhci',os.O_RDWR)
            os.write(fd,b'\xff\x00')
            response=os.read(fd,4)
            if len(response)!=4 or response[:2]!=b'\xff\x00': raise ValueError('vhci创建失败')
            print('[READY] HCI hci%d; radio=EMULATED'%struct.unpack('<H',response[2:])[0],flush=True)
            reader=lambda n:os.read(fd,n)
            def writer(data):
                if os.write(fd,data)!=len(data): raise OSError('vhci短写')
            source=fd
        network.settimeout(10); network.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
        incoming,outgoing=bytearray(),bytearray()
        received=sent=0
        while True:
            ready,_,_=select.select([network,source],[],[],1)
            if network in ready:
                data=network.recv(65536)
                if not data: break
                incoming.extend(data)
                for packet in packets(incoming): writer(packet); received+=1
            if source in ready:
                data=reader(65536)
                if not data: break
                outgoing.extend(data)
                for packet in packets(outgoing): network.sendall(packet); sent+=1
        print('[STATS] HCI packets sent=%d received=%d'%(sent,received),flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        if fd is not None: os.close(fd)
        for resource in reversed(resources): resource.close()


if __name__=='__main__': main()
