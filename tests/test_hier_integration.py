#!/usr/bin/env python3
"""真实 UDP 验证课件的 a/b、全域 EPT、SID 路由与 EPID 封装。"""
import argparse
from datetime import datetime,timezone
import ipaddress
import itertools
import json
from pathlib import Path
import re
import socket
import time
from hier_fixture import Fixture
from hier_protocol import encode,decode


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(); out=args.output.resolve(); out.mkdir(parents=True,exist_ok=False)
    report={'started_at':datetime.now(timezone.utc).isoformat(),'status':'FAIL','checks':{},'physical_media_verified':False}
    fixtures=[]; serial=itertools.count()
    def token(): return f'hier-{next(serial):06d}-end'
    def check(name,action):
        report['checks'][name]={'status':'PASS','evidence':action()}; print(name+': PASS',flush=True)
    def received(p,t):
        p.wait(t); lines=[x for x in p.lines if '[RECV]' in x and t in x]
        assert len(lines)==1,lines
        return lines[0]
    def fixture(path,a=32,b=8):
        path.mkdir(); f=Fixture(path,a,b); fixtures.append(f); f.launch(); return f
    def sendhost(f,src,dst):
        t=token(); f.hosts[src].send('send '+f.address(dst)+' '+t); received(f.hosts[dst],t)
    try:
        f=fixture(out/'aligned')
        def nodes():
            for src,dst in itertools.permutations(range(1,5),2):
                t=token(); f.nodes[src].send('send '+f.data['node_addresses'][str(dst)]+' '+t); received(f.nodes[dst],t)
            return {'delivered':12,'routing_key':'SID; diagnostic destination PID=0'}
        check('internal_sid_routing_12_directions',nodes)
        def attachments():
            evidence=[]
            for c in f.entries:
                net=int(c['network'],16); host=c['hosts'][0]['session']
                for u in c['uplinks']:
                    other=next(x for x in c['uplinks'] if x['node']!=u['node'])
                    f.status(other['node'],other['port'],'down'); f.via(net,u['node'])
                    t=token(); f.hosts[host].send('send '+f.data['node_addresses'][str(u['node'])]+' '+t)
                    received(f.nodes[u['node']],t)
                    t=token(); offset=len(f.nodes[u['node']].lines)
                    f.nodes[u['node']].send('send '+f.address(host)+' '+t); received(f.hosts[host],t)
                    assert any(f'外部端口{u["port"]}(' in x for x in f.nodes[u['node']].lines[offset:])
                    f.status(other['node'],other['port'],'up')
                    evidence.append({'lport':net,'sid':u['node'],'pid':u['port'],'both_directions':True})
            return evidence
        check('six_physical_external_attachments',attachments)
        def combos():
            count=0
            for a,b,c in itertools.product((1,3),(2,4),(3,4)):
                for net,n in ((65535,a),(3,b),(5,c)): f.via(net,n)
                for src,dst in itertools.permutations(('hA1','hB1','hC1'),2): sendhost(f,src,dst); count+=1
            return {'gateway_combinations':8,'delivered':count,'duplicate_deliveries':0}
        check('up_down_and_cross_child_forwarding',combos)
        def return_path():
            f.via(65535,1); f.status(1,3,'down'); entry=f.entries[0]
            evidence=[]
            for last in (16,17):
                src=str(ipaddress.IPv6Address(int(ipaddress.IPv6Address(f.address('hA1')))-1+last))
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                    sock.bind(('127.0.0.1',0)); sock.settimeout(4)
                    t=token(); sock.sendto(encode(src,f.data['node_addresses']['1'],t),tuple(entry['listen']))
                    received(f.nodes[1],t)
                    t=token(); f.nodes[3].send('send '+src+' '+t)
                    p=decode(sock.recv(1024))
                    assert p['destination']==src and p['source']==f.data['node_addresses']['3']
                    assert p['epid'] is None and p['ttl']==15 and p['body']==t.encode()
                    evidence.append({'source':src,'entry':1,'return':3,'external_epid_removed':True,'crc_valid':True})
            f.status(1,3,'up'); return evidence
        check('shared_learning_and_epid_removal',return_path)
        def rejected():
            c=f.entries[1]; u=next(x for x in c['uplinks'] if x['node']==2); target=tuple(u['peer'])
            good=encode(f.address('hB1'),f.data['node_addresses']['2'],token())
            cases=[('bad_crc',good[:-1]+bytes([good[-1]^1]),'CRC校验失败'),
                   ('truncated',good[:-1],'帧长度或格式非法'),('trailing',good+b'x','帧长度或格式非法'),
                   ('wrong_field',encode('2001:db8:200::1',f.data['node_addresses']['2'],token()),'源地址校验失败'),
                   ('wrong_prefix',encode('2001:db9:300::1',f.data['node_addresses']['2'],token()),'源地址校验失败'),
                   ('external_epid',encode(f.address('hB1'),f.data['node_addresses']['2'],token(),(2,0)),'外部输入不允许携带 EPID')]
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                for name,data,error in cases:
                    offset=len(f.nodes[2].lines); sock.sendto(data,target); f.nodes[2].wait(error,offset)
                # 改变选定字段之外的低位，仍应通过源地址校验。
                t=token(); sock.sendto(encode('2001:db8:300::1234',f.data['node_addresses']['2'],t),target)
                received(f.nodes[2],t)
            return [name for name,_,_ in cases]+['outside_field_bits_accepted']
        check('source_field_prefix_crc_and_framing_rejections',rejected)
        def weighted():
            f.via(65535,1); choices={}; port=tuple(f.entries[0]['listen'])
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                for last in range(32,96):
                    src=str(ipaddress.IPv6Address(int(ipaddress.IPv6Address(f.address('hA1')))-1+last))
                    decisions=[]
                    for _ in range(2):
                        t=token(); offset=len(f.nodes[1].lines)
                        sock.sendto(encode(src,f.address('hC1'),t),port); received(f.hosts['hC1'],t)
                        chosen=[re.search(r'EPID=(\d+/\d+)',x).group(1) for x in f.nodes[1].lines[offset:] if '[SELECT] Lport=5 ' in x]
                        assert len(chosen)==1; decisions+=chosen
                    assert decisions[0]==decisions[1]; choices[src]=decisions[0]
            counts={key:list(choices.values()).count(key) for key in ('3/3','4/5')}
            assert sum(counts.values())==64 and all(counts.values())
            f.status(4,5,'down')
            for _ in range(4):
                offset=len(f.nodes[1].lines); sendhost(f,'hA1','hC1')
                assert any('[SELECT] Lport=5 EPID=3/3' in x for x in f.nodes[1].lines[offset:])
            f.status(4,5,'up')
            return {'flows':64,'received':128,'stable':True,'exit_counts':counts,'disabled_exit_excluded':True}
        check('weighted_stable_flows_and_external_status',weighted)
        def backup():
            f.via(65535,1); f.status(3,3,'down')
            offset=len(f.nodes[1].lines); sendhost(f,'hA1','hC1')
            assert any('EPID=4/5 出端口2 ' in x for x in f.nodes[1].lines[offset:])
            f.nodes[1].send('link 2 down'); f.nodes[1].wait('[STATE] port=2 down')
            offset=len(f.nodes[1].lines); sendhost(f,'hA1','hC1')
            assert any('EPID=4/5 出端口1 ' in x for x in f.nodes[1].lines[offset:])
            assert not any('EPID=4/5 出端口2 ' in x for x in f.nodes[1].lines[offset:])
            f.nodes[1].send('link 2 up'); f.nodes[1].wait('[STATE] port=2 up')
            sendhost(f,'hA1','hC1'); f.status(3,3,'up')
            return {'target_epid':'4/5','baseline_port':2,'disabled_port':2,'backup_port':1,'restored':True}
        check('internal_status_and_backup_without_epid_change',backup)
        f.stop()
        g=fixture(out/'nonaligned',29,11)
        def unaligned():
            for src,dst in itertools.permutations(('hA1','hB1','hC1'),2): sendhost(g,src,dst)
            # 与 C 提取函数独立的二进制字符串切片验证生成地址。
            observed={h:int(f'{int(ipaddress.IPv6Address(g.address(h))):0128b}'[29:40],2) for h in ('hB1','hC1')}
            assert observed=={'hB1':3,'hC1':5}
            return {'ab':'29/11','fields':observed,'directions':6}
        check('non_byte_aligned_ab_forwarding',unaligned)
        report['status']='PASS'
    except Exception as error:
        report['error']=str(error); print('FAIL: '+str(error),flush=True)
    finally:
        stopped=[item.stop() for item in fixtures]
        report['cleanup']={'processes_stopped':all(stopped)}
        if not all(report['cleanup'].values()): report['status']='FAIL'
        (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
