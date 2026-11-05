#!/usr/bin/env python3
"""Linux 实际 UDP 验收：原图连线、双节点共享信道、回程、分流和重复交付。"""
import argparse
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import re
import socket
import struct
import sys
import time
from test_bluetooth import ROOT, Process, frame
from test_topology import verify

sys.path.insert(0,str(ROOT/'scripts'))
from channel import decode


def runtime_config(target):
    target.mkdir()
    channels=json.loads((ROOT/'config/channels.json').read_text())
    original=set()
    texts={n:(ROOT/f'config/node{n}.conf').read_text() for n in range(1,5)}
    for text in texts.values():
        for line in text.splitlines():
            w=line.split('#')[0].split()
            if w and w[0]=='port':
                i=4 if w[2]=='ext' else 3
                original.update((int(w[i]),int(w[i+2])))
    for c in channels['channels']:
        original.add(c['listen'][1])
        original.update(h['peer'][1] for h in c['hosts'])
    held=[]; mapping={}
    try:
        for old in sorted(original):
            s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); held.append(s)
            s.bind(('127.0.0.1',0)); mapping[old]=s.getsockname()[1]
        for n,text in texts.items():
            rows=[]
            for line in text.splitlines():
                w=line.split('#')[0].split()
                if w and w[0]=='port':
                    i=4 if w[2]=='ext' else 3
                    w[i]=str(mapping[int(w[i])]); w[i+2]=str(mapping[int(w[i+2])]); line=' '.join(w)
                rows.append(line)
            (target/f'node{n}.conf').write_text('\n'.join(rows)+'\n')
        for c in channels['channels']:
            c['listen'][1]=mapping[c['listen'][1]]
            for endpoint in c['uplinks']+c['hosts']: endpoint['peer'][1]=mapping[endpoint['peer'][1]]
        (target/'channels.json').write_text(json.dumps(channels,indent=2)+'\n')
        verify(target)
        return channels['channels']
    finally:
        for s in held: s.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(); out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'status':'FAIL','checks':{},'physical_media_verified':False}
    procs=[]; sequence=itertools.count()
    def start(name,command):
        p=Process(name,list(map(str,command)),out); procs.append(p); return p
    def check(name,fn):
        report['checks'][name]={'status':'PASS','evidence':fn()}; print(name+': PASS',flush=True)
    def token(): return 'topology_'+str(next(sequence))
    def recv(proc,text):
        proc.wait(text)
        lines=[x for x in proc.lines if '[RECV]' in x and text in x]
        assert len(lines)==1, lines
        return lines[0]
    try:
        conf=out/'config'; entries=runtime_config(conf)
        nodes={n:start('n'+str(n),[ROOT/'bin/switch',conf/f'node{n}.conf']) for n in range(1,5)}
        for n,p in nodes.items(): p.wait(f'节点{n}已启动')
        channels={}; hosts={}
        for c in entries:
            net=int(c['network'],16)
            channels[net]=start('ch'+str(net),[sys.executable,ROOT/'scripts/channel.py',conf/'channels.json','--network',c['network']])
            channels[net].wait('[READY]')
            for h in c['hosts']:
                addr=int(h['address'],16)
                hosts[addr]=start('h'+h['address'],[ROOT/'bin/host',h['address'],h['peer'][1],'127.0.0.1',c['listen'][1]])
                hosts[addr].wait('已就绪')
        def via(net,node):
            offset=len(channels[net].lines); channels[net].send('via '+str(node))
            channels[net].wait('[GATEWAY] node='+str(node),offset)
        def internal():
            for a,b in itertools.permutations(nodes,2):
                t=token(); nodes[a].send(f'send 0x01{b:02X} {t}'); recv(nodes[b],t)
            return {'delivered':12}
        check('four_nodes_12_directions',internal)
        def attachments():
            evidence=[]
            for c in entries:
                net=int(c['network'],16); addr=(net<<8)|1
                for u in c['uplinks']:
                    n=u['node']; via(net,n); t=token()
                    hosts[addr].send(f'send 0x01{n:02X} {t}'); recv(nodes[n],t)
                    t=token(); offset=len(nodes[n].lines)
                    nodes[n].send(f'send 0x{addr:04X} {t}'); recv(hosts[addr],t)
                    assert any(f'外部端口{u["port"]}(' in x for x in nodes[n].lines[offset:])
                    evidence.append({'network':c['network'],'node':n,'port':u['port'],'both_directions':True})
            return evidence
        check('all_six_external_attachments',attachments)
        def combinations():
            count=0
            for a,b,c in itertools.product((1,3),(2,4),(3,4)):
                for net,node in ((10,a),(11,b),(12,c)): via(net,node)
                for source,dest in itertools.permutations((0x0A01,0x0B01,0x0C01),2):
                    t=token(); hosts[source].send(f'send 0x{dest:04X} {t}'); recv(hosts[dest],t); count+=1
            return {'gateway_combinations':8,'delivered':count,'duplicate_deliveries':0}
        check('three_domains_all_gateway_combinations',combinations)
        def return_path():
            via(10,1)
            port=entries[0]['listen'][1]; evidence=[]
            for addr in (0x0A10,0x0A11):
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
                    s.bind(('127.0.0.1',0)); s.settimeout(4)
                    t=token(); s.sendto(frame(addr,0x0101,t),('127.0.0.1',port)); recv(nodes[1],t)
                    t=token(); nodes[3].send(f'send 0x{addr:04X} {t}')
                    data,_=s.recvfrom(4096); dst,src=decode(data)
                    assert (dst,src)==(addr,0x0103) and data[4]==7 and data[8:].decode()==t
                    evidence.append({'address':hex(addr),'entry_node':1,'return_node':3,'ttl':7,'crc_valid':True})
            return evidence
        check('multi_host_return_from_other_attachment',return_path)
        def balance():
            via(10,1); choices={}
            for src in (0x0A01,0x0A02):
                paths=[]
                for _ in range(4):
                    offset=len(nodes[1].lines); t=token()
                    hosts[src].send(f'send 0x0C01 {t}'); recv(hosts[0x0C01],t)
                    decisions=[int(re.search(r'出端口(\d+)',x).group(1)) for x in nodes[1].lines[offset:]
                               if '出端口' in x and 'dst=0x0C01' in x]
                    assert len(decisions)==1; paths+=decisions
                assert len(set(paths))==1; choices[hex(src)]=paths
            assert {p[0] for p in choices.values()}=={1,2}
            return choices
        check('stable_flows_use_both_internal_paths',balance)
        report['status']='PASS'
    except Exception as error:
        report['error']=str(error); print('FAIL: '+str(error),flush=True)
    finally:
        for p in reversed(procs): p.stop()
        report['cleanup']={'processes_stopped':all(p.p.poll() is not None for p in procs)}
        if not all(report['cleanup'].values()): report['status']='FAIL'
        (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
