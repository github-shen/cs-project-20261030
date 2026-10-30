#!/usr/bin/env python3
"""双VM实验的一端；JSON行命令供协调器控制，EOF时清理自己创建的资源。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from test_bluetooth import ROOT, Process, command, environment, frame, make_configs


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('role',choices=['a','b'])
    p.add_argument('--local-ip',required=True)
    p.add_argument('--peer-ip',required=True)
    p.add_argument('--output',type=Path,required=True)
    o=p.parse_args()
    if os.geteuid()!=0: p.error('需要sudo和私有mount namespace')
    lock=open('/run/lock/cnet-bluetooth-test.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if list(Path('/sys/class/bluetooth').glob('hci*')): p.error('已有控制器，拒绝运行')
    socket.inet_pton(socket.AF_INET,o.local_ip); socket.inet_pton(socket.AF_INET,o.peer_ip)
    out=o.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    before=environment(); procs={}; all_procs=[]; masked=False
    active=subprocess.run(['systemctl','is-active','--quiet','bluetooth']).returncode==0
    service_state=subprocess.run(['systemctl','is-enabled','bluetooth'],text=True,capture_output=True).stdout.strip()
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    def emit(data): print(json.dumps(data,ensure_ascii=False),flush=True)
    def start(name,args):
        proc=Process(name,list(map(str,args)),out); procs[name]=proc; all_procs.append(proc); return proc
    def gateway(name,mode,local,peer,psm,udp,app):
        return start(name,[ROOT/'bin/bt-channel',mode,local,peer,psm,udp,app,'low'])

    try:
        if not service_state.startswith('masked'):
            command('systemctl','mask','--runtime','bluetooth.service'); masked=True
        command('systemctl','stop','bluetooth')
        command('modprobe','hci_vhci')
        # btvirt -s 的固定 /tmp socket 置于私有挂载空间，不覆盖其它项目。
        if os.readlink('/proc/self/ns/mnt')==os.readlink('/proc/1/ns/mnt'):
            raise RuntimeError('请通过 unshare --mount --propagation private 启动')
        command('mount','-t','tmpfs','-o','size=16m','cnet-bt-test','/tmp')
        relay=[sys.executable,ROOT/'tests/hci_relay.py']
        if o.role=='a':
            start('btvirt',['btvirt','-l1','-B','-s'])
            end=time.monotonic()+10
            while not Path('/tmp/bt-server-bredr').exists():
                if time.monotonic()>end: raise RuntimeError('btvirt server未就绪')
                time.sleep(.1)
            start('hci-relay',relay+['serve','--address',o.local_ip,'--peer',o.peer_ip]).wait('[READY]')
        else:
            start('hci-relay',relay+['connect','--address',o.peer_ip]).wait('[READY]')
        end=time.monotonic()+15
        while True:
            controllers=list(Path('/sys/class/bluetooth').glob('hci*'))
            if controllers: break
            if time.monotonic()>end: raise RuntimeError('虚拟HCI未创建')
            time.sleep(.1)
        hci=controllers[0]
        if '/virtual/' not in str(hci.resolve()): raise RuntimeError('拒绝真实HCI')
        command('btmgmt','-i',hci.name,'power','off')
        command('btmgmt','-i',hci.name,'ssp','off')
        command('btmgmt','-i',hci.name,'power','on')
        command('hciconfig',hci.name,'up'); command('hciconfig',hci.name,'piscan')
        address=re.search(r'BD Address:\s*([0-9A-F:]{17})',command('hciconfig',hci.name)).group(1)
        start('btmon',['btmon','-i',hci.name,'-w',out/'transport.btsnoop'])
        emit({'ready':o.role,'address':address})
        host_params={'upper':('0x0A01',29001,49001),'down3':('0x0B01',29002,23104),
                     'down5':('0x0C01',29003,22104),'down1':('0x0D01',29004,24103)}
        for line in sys.stdin:
            try:
                req=json.loads(line); action=req['action']; answer=None
                if action=='setup':
                    peer=req['peer']; configs=make_configs(out)
                    # 跨VM的两条UDP链路显式改为对端IP；蓝牙端口仍只连本机适配器。
                    edits={2:{2:o.peer_ip,3:o.peer_ip},3:{1:o.peer_ip},4:{1:o.peer_ip}}
                    owned_nodes=(1,2) if o.role=='a' else (3,4)
                    for n in owned_nodes:
                        path=configs/('node%d.conf'%n); result=[]
                        for row in path.read_text().splitlines():
                            w=row.split()
                            if w and w[0]=='port' and int(w[1]) in edits.get(n,{}):
                                w[4]=edits[n][int(w[1])]; row=' '.join(w)
                            result.append(row)
                        path.write_text('\n'.join(result)+'\n')
                    if o.role=='a':
                        gateway('ext','listen',address,peer,'0x1001',41003,21103)
                        gateway('int','connect',address,peer,'0x1003',41102,21102)
                    else:
                        gateway('int','listen',address,peer,'0x1003',42002,22002)
                        gateway('ext','connect',address,peer,'0x1001',49001,29001)
                    for n in owned_nodes:
                        proc=start('node%d'%n,[ROOT/'bin/switch',configs/('node%d.conf'%n)])
                        proc.wait('已启动'); proc.send('table')
                    for name in (['down3'] if o.role=='a' else ['upper','down5','down1']):
                        addr,udp,dest=host_params[name]
                        start(name,[ROOT/'bin/host',addr,udp,'127.0.0.1',dest]).wait('已就绪')
                    answer='started'
                elif action=='wait': answer=procs[req['process']].wait(req['text'],req.get('offset',0),req.get('timeout',15))
                elif action=='send': procs[req['process']].send(req['text']); answer=True
                elif action=='lines': answer=procs[req['process']].lines
                elif action=='stop': procs[req['process']].stop(); answer=True
                elif action=='signal': os.kill(procs[req['process']].p.pid,signal.SIGUSR1); answer=True
                elif action=='inject':
                    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                        sock.bind(('127.0.0.1',29001))
                        for i in range(req.get('count',1)):
                            packet=frame(req['source']+i,req['destination'],req['token']+str(i))
                            sock.sendto(packet,('127.0.0.1',49001)); time.sleep(.01)
                    answer=True
                elif action=='restart':
                    if o.role!='b' or req['process']!='ext': raise ValueError('只重连B端外部信道')
                    gateway('ext-reconnected','connect',address,req['peer'],'0x1001',49001,29001).wait('[BT] connected')
                    answer=True
                elif action=='finish': break
                else: raise ValueError('未知操作')
                emit({'ok':True,'result':answer})
            except Exception as error: emit({'ok':False,'error':str(error)})
    finally:
        for proc in reversed(all_procs):
            if not proc.log.closed: proc.stop()
        if masked: command('systemctl','unmask','--runtime','bluetooth.service')
        if active: command('systemctl','start','bluetooth')
        time.sleep(.3)
        cleanup={'processes_stopped':all(p.p.poll() is not None for p in all_procs),
                 'hci_removed':not list(Path('/sys/class/bluetooth').glob('hci*')),
                 'network_unchanged':environment()==before,
                 'bluetooth_service_restored':
                     (subprocess.run(['systemctl','is-active','--quiet','bluetooth']).returncode==0)==active
                     and subprocess.run(['systemctl','is-enabled','bluetooth'],capture_output=True,text=True).stdout.strip()==service_state}
        (out/'cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
        emit({'cleanup':cleanup})


if __name__=='__main__': main()
