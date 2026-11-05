#!/usr/bin/env python3
"""在虚拟 HCI 的真实 L2CAP 上验证 IPv6/EPID 最大帧及双向恢复。"""
import json
import socket
import sys
import time
from hier_fixture import Fixture
from test_bluetooth import ROOT, main
from hier_protocol import encode


def exercise(out,start,check,gateway,ext_server,ext_client):
    target=out/'hier'; target.mkdir()
    lab=Fixture(target)
    # 复用公共 HCI 夹具的两个 PSM；应用端口必须与 bt-channel 严格匹配。
    endpoints={(1,3):(21103,41003),(1,2):(21102,41102),(4,3):(24003,44003)}
    for n in range(1,5):
        path=lab.config/f'node{n}.conf'; lines=[]
        for line in path.read_text().splitlines():
            w=line.split()
            if w and w[0]=='port':
                pid=int(w[1]); pos=4 if w[2]=='ext' else 3
                if (n,pid) in endpoints:
                    local,peer=endpoints[n,pid]; w[pos]=str(local); w[pos+2]=str(peer)
                    if 'type' in w: w[w.index('type')+1]='bt'
                    else: w += ['type','bt']
                elif n==3 and pid==2: w[pos+2]='29001'
                line=' '.join(w)
            lines.append(line)
        path.write_text('\n'.join(lines)+'\n')
    upper=next(c for c in lab.entries if int(c['network'],16)==65535)
    upper['listen'][1]=29001
    next(p for p in upper['uplinks'] if p['node']==1)['peer'][1]=49001
    (lab.config/'channels.json').write_text(json.dumps(lab.data))
    # 生命周期统一由公共夹具管理，异常时也关闭全部进程。
    lab.start=start; lab.launch()

    def connectivity():
        evidence=[]
        for src in ('hA1','hB1','hC1'):
            for dst in ('hA1','hB1','hC1'):
                if src==dst: continue
                marker='hier_bt_'+src+'_'+dst
                lab.hosts[src].send(f'send {lab.address(dst)} {marker}')
                evidence.append(lab.hosts[dst].wait(marker))
        for src,dst in ((1,4),(4,1)):
            marker=f'internal_bt_{src}_{dst}'
            lab.nodes[src].send('send '+lab.data['node_addresses'][str(dst)]+' '+marker)
            evidence.append(lab.nodes[dst].wait(marker))
        return evidence
    check('hierarchical_external_6_and_internal_both_directions',connectivity)
    lab.status(3,3,'down'); lab.status(3,2,'down'); lab.via(5,4)

    def maximum():
        evidence=[]
        for src,dst,letter in (('hA1','hC1','x'),('hC1','hA1','y')):
            marker=letter*199
            lab.hosts[src].send(f'send {lab.address(dst)} {marker}')
            evidence.append(lab.hosts[dst].wait(marker))
        return {'external_bytes':245,'internal_bytes':249,'delivery':evidence}
    check('maximum_ipv6_and_epid_frames',maximum)

    def crc():
        offset=len(lab.nodes[4].lines)
        lab.nodes[1].send('sendbad '+lab.address('hC1')+' reject_internal_bt_crc')
        internal=lab.nodes[4].wait('CRC校验失败',offset)
        offset=len(lab.channels[65535].lines)
        lab.nodes[1].send('sendbad '+lab.address('hA1')+' reject_external_bt_crc')
        external=lab.channels[65535].wait('CRC校验失败',offset)
        return {'internal':internal,'external':external}
    check('crc_preserved_across_both_l2cap_links',crc)
    lab.channels[65535].stop()
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as inject:
        inject.bind(('127.0.0.1',29001))
        def malformed():
            good=encode(lab.address('hA1'),lab.address('hC1'),'length')
            offset=len(ext_client.lines)
            for data in (b'x',good[:-1],good+b'extra',b'x'*600): inject.sendto(data,('127.0.0.1',49001))
            end=time.monotonic()+5
            while time.monotonic()<end:
                if sum('本地帧长度非法' in s for s in ext_client.lines[offset:])==4: return '4 malformed IPv6 datagrams rejected'
                time.sleep(.03)
            raise AssertionError('IPv6 长度拒绝数不符')
        check('hierarchical_exact_lengths',malformed)
        def source():
            offset=len(lab.nodes[1].lines)
            inject.sendto(encode(lab.address('hB1'),lab.address('hC1'),'wrong_prefix'),('127.0.0.1',49001))
            return lab.nodes[1].wait('源地址校验失败',offset)
        check('ab_source_validation_after_bluetooth',source)
    lab.channels[65535]=start('chUp-reopened',[sys.executable,ROOT/'scripts/channel.py',lab.config/'channels.json','--network','0xFFFF'])
    lab.channels[65535].wait('[READY]')

    def recovery():
        offset=len(ext_server.lines); ext_client.stop(); ext_server.wait('disconnected',offset)
        lab.nodes[1].send('send '+lab.address('hA1')+' hier_no_replay')
        dropped=ext_server.wait('蓝牙信道未连接',offset)
        args=ext_client.args
        renewed=start('external-client-reconnected',args)
        renewed.wait('[BT] connected')
        lab.hosts['hA1'].send('send '+lab.address('hC1')+' hier_reconnect_forward')
        forward=lab.hosts['hC1'].wait('hier_reconnect_forward')
        lab.hosts['hC1'].send('send '+lab.address('hA1')+' hier_reconnect_reverse')
        reverse=lab.hosts['hA1'].wait('hier_reconnect_reverse')
        assert not any('hier_no_replay' in s for s in lab.hosts['hA1'].lines)
        return {'offline_drop':dropped,'forward':forward,'reverse':reverse,'replayed':False}
    check('hierarchical_disconnect_reconnect',recovery)
    return lab.nodes


if __name__=='__main__': sys.exit(main(hierarchical=True))
