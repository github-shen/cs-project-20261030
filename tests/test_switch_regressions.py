#!/usr/bin/env python3
"""节点CLI坏帧和多终端学习回程的真实UDP回归，无需root。"""
import argparse
import binascii
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import struct
import threading
import time
from test_bluetooth import ROOT, Process, frame


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--binary', type=Path, default=ROOT/'bin/switch')
    opts = parser.parse_args()
    out = opts.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'status': 'FAIL', 'checks': {}}
    processes = []; sockets = []; workers = []; stop = threading.Event(); errors = []

    def start(name, args):
        p = Process(name, list(map(str, args)), out); processes.append(p); return p

    def check(name, fn):
        result['checks'][name] = {'status': 'PASS', 'evidence': fn()}
        print(name+': PASS', flush=True)

    try:
        reserved = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(6)]
        sockets.extend(reserved)
        for s in reserved: s.bind(('127.0.0.1', 0))
        aint, aext, bint, bext, up, down = [s.getsockname()[1] for s in reserved]
        for s in reserved: s.close()
        conf1 = out/'node1.conf'; conf2 = out/'node2.conf'
        conf1.write_text('id 1\nport 1 int %d 127.0.0.1 %d\nport 3 ext 0x0A %d 127.0.0.1 %d\nift 0102 1\nift 0B00 1\n' % (aint,bint,aext,up))
        conf2.write_text('id 2\nport 1 int %d 127.0.0.1 %d\nport 4 ext 0x0B %d 127.0.0.1 %d\nift 0101 1\nift 0A00 1\n' % (bint,aint,bext,down))
        n1 = start('node1', [opts.binary.resolve(),conf1]); n1.wait('已启动')
        n2 = start('node2', [opts.binary.resolve(),conf2]); n2.wait('已启动')
        hu = start('upper', [ROOT/'bin/host','0x0A01',up,'127.0.0.1',aext]); hu.wait('已就绪')
        hd = start('down', [ROOT/'bin/host','0x0B01',down,'127.0.0.1',bext]); hd.wait('已就绪')

        def delivery(name, dst, receiver, corrupt=False, checker=None):
            checker = checker or receiver
            offset = len(checker.lines); token = 'review_'+name
            n1.send('%s %s %s' % ('sendbad' if corrupt else 'send', dst, token))
            if corrupt:
                evidence = checker.wait('[DROP] CRC校验失败', offset)
                time.sleep(.08)
                assert not any('[RECV]' in s and token in s for s in receiver.lines)
                return evidence
            return receiver.wait(token)

        for name,dst,receiver,checker in (
                ('remote_node','0x0102',n2,n2), ('routed_external','0x0B01',hd,n2),
                ('direct_external','0x0A01',hu,hu), ('self','0x0101',n1,n1)):
            check('send_'+name, lambda n=name,d=dst,r=receiver: delivery(n,d,r))
            check('sendbad_'+name, lambda n=name,d=dst,r=receiver,c=checker: delivery('bad_'+n,d,r,True,c))

        def learning():
            clients = []
            for i in range(8):
                s = socket.socket(socket.AF_INET,socket.SOCK_DGRAM); sockets.append(s)
                s.bind(('127.0.0.1',0)); s.settimeout(3)
                addr = 0x0a10+i; token = 'learn_%d'%i
                s.sendto(frame(addr,0x0101,token),('127.0.0.1',aext)); n1.wait(token)
                clients.append((addr,s))

            def refresh(addr,s):
                try:
                    while not stop.is_set():
                        s.sendto(frame(addr,0x0101,'refresh'),('127.0.0.1',aext))
                        stop.wait(.01)
                except Exception as e: errors.append(str(e))

            for addr,s in clients:
                t=threading.Thread(target=refresh,args=(addr,s)); workers.append(t); t.start()
            received = 0
            for round_id in range(8):
                n1.send('table'); n1.send('stats'); n1.send('reset')
                for addr,s in clients: n2.send('send 0x%04X reply_%d_%04X'%(addr,round_id,addr))
                for addr,s in clients:
                    data,_=s.recvfrom(4096)
                    dst,src,ttl,size,crc=struct.unpack('=HHBBH',data[:8])
                    assert dst==addr and src==0x0102 and ttl==6
                    assert len(data)==8+size
                    assert binascii.crc_hqx(data[:6]+b'\0\0'+data[8:],0xffff)==crc
                    assert data[8:].decode()=='reply_%d_%04X'%(round_id,addr)
                    received+=1
            stop.set()
            for t in workers: t.join(timeout=3)
            assert not errors and not any(t.is_alive() for t in workers)
            return {'terminals':8,'replies':received,'wrong_destination':0,'invalid_crc':0,'ttl':6}
        check('concurrent_learning_and_return_traffic',learning)
        result['status']='PASS'
    except Exception as e:
        result['error']=str(e); print('FAIL: '+str(e),flush=True)
    finally:
        stop.set()
        for t in workers: t.join(timeout=3)
        for s in sockets: s.close()
        for p in reversed(processes): p.stop()
        result['cleanup']={'processes_stopped':all(p.p.poll() is not None for p in processes),
                           'threads_stopped':all(not t.is_alive() for t in workers)}
        if not all(result['cleanup'].values()): result['status']='FAIL'
        (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
