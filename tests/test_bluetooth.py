#!/usr/bin/env python3
"""在 Linux 虚拟蓝牙控制器上运行真实 L2CAP + 四节点交换实验。"""
import argparse
import binascii
import fcntl
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from bluetooth import probe
from bluetooth_regressions import queued_offline_frames


class Process:
    def __init__(self, name, args, out):
        self.name, self.lines, self.args = name, [], args
        self.log = (out/(name+'.log')).open('w', encoding='utf-8')
        self.p = subprocess.Popen(['stdbuf', '-oL', '-eL'] + args, cwd=ROOT,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding='utf-8')
        self.t = threading.Thread(target=self.read, daemon=True)
        self.t.start()

    def read(self):
        for line in self.p.stdout:
            self.lines.append(line.rstrip())
            self.log.write(line); self.log.flush()

    def wait(self, text, start=0, timeout=8):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            found = next((s for s in self.lines[start:] if text in s), None)
            if found: return found
            if self.p.poll() is not None:
                raise AssertionError(self.name+'提前退出: '+'\n'.join(self.lines[-8:]))
            time.sleep(.03)
        raise AssertionError(self.name+'未观察到: '+text+'\n'+'\n'.join(self.lines[-8:]))

    def send(self, command):
        self.p.stdin.write(command+'\n'); self.p.stdin.flush()

    def stop(self):
        if self.p.poll() is None:
            self.p.terminate()
            try: self.p.wait(timeout=5)
            except subprocess.TimeoutExpired: self.p.kill(); self.p.wait(timeout=3)
        self.t.join(timeout=3)
        self.log.close()


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(' '.join(args)+': '+result.stdout+result.stderr)
    return result.stdout


def environment():
    return {'default_route': command('ip', '-4', 'route', 'show', 'default'),
            'namespaces': command('ip', 'netns', 'list'),
            'ovs_bridges': command('ovs-vsctl', 'list-br') if shutil_which('ovs-vsctl') else None}


def shutil_which(name):
    import shutil
    return shutil.which(name)


def frame(src, dst, text, ttl=8):
    data = text.encode()
    header = struct.pack('=HHBBH', dst, src, ttl, len(data), 0)
    crc = binascii.crc_hqx(header+data, 0xffff)
    return struct.pack('=HHBBH', dst, src, ttl, len(data), crc)+data


def make_configs(out):
    target = out/'config'; target.mkdir()
    for node in range(1,5):
        lines = []
        for line in (ROOT/'config'/('node%d.conf'%node)).read_text().splitlines():
            words = line.split('#', 1)[0].split()
            if words and words[0] == 'port':
                pos = 4 if words[2] == 'ext' else 3
                words[pos] = str(int(words[pos])+20000)
                words[pos+2] = str(int(words[pos+2])+20000)
                if node == 1 and words[1] == '3':
                    words[pos+2] = '41003'; words = words[:pos+3]+['type', 'bt']
                if node == 1 and words[1] == '2':
                    words[pos+2] = '41102'; words = words[:pos+3]+['type', 'bt']
                if node == 3 and words[1] == '2':
                    words[pos+2] = '42002'; words = words[:pos+3]+['type', 'bt']
                line = ' '.join(words)
            lines.append(line)
        (target/('node%d.conf'%node)).write_text('\n'.join(lines)+'\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'logs'/datetime.now().strftime('bluetooth-%Y%m%d-%H%M%S'))
    opt = parser.parse_args()
    if os.geteuid() != 0: parser.error('需sudo；只操作本次创建的虚拟HCI和进程')
    lock = open('/run/lock/cnet-bluetooth-test.lock','w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    out = opt.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    processes = []
    result = {'started_at': datetime.now(timezone.utc).isoformat(),
              'transport': 'AF_BLUETOOTH / SOCK_SEQPACKET / BTPROTO_L2CAP',
              'radio': 'EMULATED - physical radio NOT VERIFIED', 'checks': {}, 'status': 'FAIL'}
    before_hci = set(Path('/sys/class/bluetooth').glob('hci*'))
    before = environment()
    # 无真实/已有控制器时才允许临时暂停桌面配对服务，结束后恢复原状态。
    if before_hci: parser.error('已有蓝牙控制器；拒绝影响现有蓝牙会话，请使用专用测试VM')
    bluetooth_active = subprocess.run(['systemctl','is-active','--quiet','bluetooth']).returncode == 0
    service_state = subprocess.run(['systemctl','is-enabled','bluetooth'],capture_output=True,text=True).stdout.strip()
    masked_by_test = False
    signal.signal(signal.SIGTERM,lambda *_: sys.exit(143))

    def start(name, args):
        proc = Process(name, list(map(str,args)), out); processes.append(proc); return proc

    def check(name, fn):
        result['checks'][name] = {'status': 'PASS', 'evidence': fn()}
        print(name+': PASS', flush=True)

    def gateway(name, mode, local, peer, psm, udp, app):
        return start(name, [ROOT/'bin/bt-channel', mode, local, peer, psm, udp, app, 'low'])

    try:
        # 桌面会经 D-Bus 自动重启 bluetoothd，故仅 stop 不够。
        if not service_state.startswith('masked'):
            command('systemctl','mask','--runtime','bluetooth.service')
            masked_by_test = True
        command('systemctl','stop','bluetooth')
        command('modprobe', 'hci_vhci')
        emulator = start('btvirt', ['btvirt', '-l3', '-B'])
        end = time.monotonic()+10
        while time.monotonic()<end:
            created = sorted(set(Path('/sys/class/bluetooth').glob('hci*'))-before_hci)
            if len(created)==3: break
            if emulator.p.poll() is not None: raise RuntimeError('btvirt启动失败')
            time.sleep(.1)
        if len(created)!=3: raise RuntimeError('没有创建3个虚拟HCI')
        addresses = []
        for hci in created:
            if '/virtual/' not in str(hci.resolve()): raise RuntimeError('拒绝配置非虚拟适配器')
            # 只对本次新建的模拟控制器禁用配对；避免桌面 bluetoothd 的配对策略
            # 干扰 low 级别协议测试。真实硬件仍使用 medium + 预先配对。
            command('btmgmt', '-i', hci.name, 'power', 'off')
            command('btmgmt', '-i', hci.name, 'ssp', 'off')
            command('btmgmt', '-i', hci.name, 'power', 'on')
            command('hciconfig', hci.name, 'up')
            command('hciconfig', hci.name, 'piscan')
            addresses.append(re.search(r'BD Address:\s*([0-9A-F:]{17})', command('hciconfig', hci.name)).group(1))
        result['controllers'] = probe()['controllers']
        monitor = start('btmon', ['btmon', '-i', created[0].name, '-w', out/'transport.btsnoop'])
        a,b,c = addresses
        ext_server = gateway('external-server','listen',a,b,'0x1001',41003,21103)
        ext_server.wait('[READY]')
        bad = gateway('unauthorized-peer','connect',c,a,'0x1001',49999,49998)
        check('bluetooth_peer_rejected', lambda: ext_server.wait('[DROP] 非配置蓝牙对端'))
        bad.stop()
        # 等拒绝测试的 ACL 链路释放后再建正常链路，避免 BlueZ 5.72
        # btvirt 多对端 handle 映射限制影响后续两个 PSM 的测试。
        command('hciconfig', created[2].name, 'down')
        monitor.wait('Disconnect Complete')
        for mode in ('listen', 'connect'):
            check('queued_offline_frames_'+mode,
                  lambda m=mode: queued_offline_frames(start, ROOT, frame, a, b, m))
        int_server = gateway('internal-server','listen',b,a,'0x1003',42002,22002)
        int_server.wait('[READY]')
        ext_client = gateway('external-client','connect',b,a,'0x1001',49001,29001)
        ext_client.wait('[BT] connected'); ext_server.wait('[BT] connected')
        int_client = gateway('internal-client','connect',a,b,'0x1003',41102,21102)
        int_client.wait('[BT] connected'); int_server.wait('[BT] connected')
        configs = make_configs(out)
        nodes = {i: start('node%d'%i,[ROOT/'bin/switch',configs/('node%d.conf'%i)]) for i in range(1,5)}
        for i,n in nodes.items(): n.wait('节点%d已启动'%i); n.send('table')
        host_params = {'upper':('0x0A01',29001,49001), 'down3':('0x0B01',29002,23104),
                       'down5':('0x0C01',29003,22104), 'down1':('0x0D01',29004,24103)}
        def host(name, log_name=None):
            addr,udp,port = host_params[name]
            h = start(log_name or name,[ROOT/'bin/host',addr,udp,'127.0.0.1',port]); h.wait('已就绪'); return h
        hosts = {k:host(k) for k in host_params}
        def connectivity():
            evidence=[]
            for source in hosts:
                for dest in hosts:
                    if source == dest: continue
                    token='bt_%s_to_%s'%(source,dest)
                    hosts[source].send('send %s %s'%(host_params[dest][0],token))
                    evidence.append(hosts[dest].wait(token))
            return evidence
        check('four_domain_12_directions',connectivity)
        def crc():
            offset=len(nodes[1].lines)
            hosts['upper'].send('sendbad 0x0C01 reject_bad_crc')
            evidence=nodes[1].wait('CRC校验失败',offset)
            time.sleep(.2)
            assert not any('reject_bad_crc' in x for x in hosts['down5'].lines)
            return evidence
        check('bad_crc_across_bluetooth',crc)

        def node_crc():
            offset=len(hosts['upper'].lines)
            nodes[1].send('send 0x0A01 node_crc_normal')
            normal=hosts['upper'].wait('node_crc_normal',offset)
            nodes[1].send('sendbad 0x0A01 node_crc_rejected')
            dropped=hosts['upper'].wait('CRC校验失败',offset)
            assert not any('[RECV]' in s and 'node_crc_rejected' in s for s in hosts['upper'].lines)
            return {'normal':normal,'corrupted':dropped}
        check('node_sendbad_across_bluetooth',node_crc)

        hosts['upper'].stop()
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as inject:
            inject.bind(('127.0.0.1',29001))
            def source_check():
                offset=len(nodes[1].lines)
                inject.sendto(frame(0x0B02,0x0C01,'reject_source'),('127.0.0.1',49001))
                return nodes[1].wait('源地址校验失败',offset)
            check('source_binding_across_bluetooth',source_check)
            def lengths():
                offset=len(ext_client.lines)
                for payload in (b'x',frame(0x0A01,0x0C01,'length')[:-1],
                                frame(0x0A01,0x0C01,'length')+b'extra',b'x'*600):
                    inject.sendto(payload,('127.0.0.1',49001))
                end=time.monotonic()+5
                while time.monotonic()<end:
                    if sum('本地帧长度非法' in x for x in ext_client.lines[offset:])==4: return '4 malformed datagrams rejected'
                    time.sleep(.03)
                raise AssertionError('长度拒绝数不符')
            check('exact_datagram_length',lengths)
            def balance():
                offset=len(nodes[1].lines)
                for i in range(1,65):
                    token='flow_%02d'%i
                    inject.sendto(frame(0x0A00+i,0x0C01,token),('127.0.0.1',49001))
                    hosts['down5'].wait(token)
                counts={str(p):sum('出端口%d dst=0x0C01'%p in s for s in nodes[1].lines[offset:]) for p in (1,2)}
                assert sum(counts.values())==64 and all(counts.values())
                return {'received':64,'node1_outputs':counts,'hash':'logical source/destination pair, not five-tuple'}
            check('64_address_flows_udp_and_bluetooth',balance)
        # 重新打开日志时使用不同名字，保留首轮证据。
        hosts['upper']=host('upper','upper-reconnected')
        def local_peer():
            offset=len(ext_client.lines)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as other:
                other.sendto(frame(0x0A01,0x0C01,'unauthorized_udp'),('127.0.0.1',49001))
            return ext_client.wait('非配置本地应用端口',offset)
        check('local_udp_peer_rejected',local_peer)
        def recovery():
            offset=len(ext_server.lines)
            ext_client.stop()
            ext_server.wait('disconnected',offset)
            hosts['down5'].send('send 0x0A01 no_replay_while_offline')
            dropped=ext_server.wait('蓝牙信道未连接',offset)
            renewed=gateway('external-client-reconnected','connect',b,a,'0x1001',49001,29001)
            renewed.wait('[BT] connected')
            hosts['upper'].send('send 0x0C01 after_reconnect')
            hosts['down5'].wait('after_reconnect')
            hosts['down5'].send('send 0x0A01 reverse_after_reconnect')
            hosts['upper'].wait('reverse_after_reconnect')
            assert not any('no_replay_while_offline' in x for x in hosts['upper'].lines)
            return {'drop':dropped,'both_directions_recovered':True,'offline_frame_replayed':False}
        check('disconnect_reconnect',recovery)
        for n in nodes.values(): n.send('stats')
        for proc in (ext_server,int_server,int_client): os.kill(proc.p.pid,signal.SIGUSR1)
        result['channel_counters']={proc.name:proc.wait('[STATS]') for proc in (ext_server,int_server,int_client)}
        for entry in result['channel_counters'].values():
            values=dict((k,int(v)) for k,v in re.findall(r'(\w+)=(\d+)',entry))
            assert values['bt_tx']>0 and values['bt_rx']>0
        time.sleep(.2)
        result['node_counters']={str(i):next(s for s in reversed(n.lines) if s.startswith('接收=')) for i,n in nodes.items()}
        monitor.stop()
        assert any('ACL Data' in s for s in monitor.lines)
        result['hci_evidence']='btmon captured ACL Data on virtual controller '+created[0].name
        result['status']='PASS'
    except Exception as error:
        result['error']=str(error)
        print('FAIL: '+str(error),file=sys.stderr,flush=True)
    finally:
        for proc in reversed(processes):
            if not proc.log.closed: proc.stop()
        if masked_by_test: command('systemctl','unmask','--runtime','bluetooth.service')
        if bluetooth_active: command('systemctl', 'start', 'bluetooth')
        time.sleep(.3)
        result['cleanup']={'processes_stopped':all(p.p.poll() is not None for p in processes),
                           'hci_restored':set(Path('/sys/class/bluetooth').glob('hci*'))==before_hci,
                           'network_unchanged':environment()==before,
                           'bluetooth_service_restored':
                               (subprocess.run(['systemctl','is-active','--quiet','bluetooth']).returncode==0)==bluetooth_active
                               and subprocess.run(['systemctl','is-enabled','bluetooth'],capture_output=True,text=True).stdout.strip()==service_state}
        if not all(result['cleanup'].values()): result['status']='FAIL'
        (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('结果: '+str(out/'summary.json'),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    sys.exit(main())
