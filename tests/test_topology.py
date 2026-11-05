"""直接用原题图的节点/端口关系验收配置，不以 README 或配置生成器为预期值。"""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
LINKS = {(1,1):(2,4), (1,2):(4,3), (2,2):(4,2), (2,3):(3,1), (3,4):(4,4)}
INTERNAL = dict(LINKS)
INTERNAL.update({b:a for a,b in LINKS.items()})
EXTERNAL = {(1,3):10,(3,2):10,(2,1):11,(4,1):11,(3,3):12,(4,5):12}
MODES = ('config','config-ovs','config-lan','config-nic-template','config-app')


def parse(folder):
    ports, routes = {}, {}
    for node in range(1,5):
        routes[node] = {}
        for line in (folder/f'node{node}.conf').read_text(encoding='utf-8').splitlines():
            w = line.split('#',1)[0].split()
            if not w: continue
            if w[0]=='id':
                if int(w[1]) != node: raise AssertionError('节点 ID 不匹配')
            elif w[0]=='port':
                index=4 if w[2]=='ext' else 3
                key=(node,int(w[1]))
                if key in ports: raise AssertionError('重复端口')
                ports[key]={'kind':w[2],'net':int(w[3],16) if index==4 else None,
                            'udp':int(w[index]),'peer':(w[index+1],int(w[index+2])),
                            'options':dict(zip(w[index+3::2],w[index+4::2]))}
            elif w[0]=='ift': routes[node][int(w[1],16)]=list(map(int,w[2].split(',')))
    return ports,routes


def verify(folder):
    ports,routes=parse(folder)
    assert set(ports)==set(INTERNAL)|set(EXTERNAL), f'{folder}: 端口集合偏离题图'
    for key,p in ports.items():
        if key in INTERNAL:
            other=ports[INTERNAL[key]]
            assert p['kind']=='int' and p['peer'][1]==other['udp'], (key,p)
            assert p['peer'][0]==other['options'].get('bind','127.0.0.1'), (key,'对端 IP 错误')
        else:
            assert p['kind']=='ext' and p['net']==EXTERNAL[key], (key,p)
    channels=json.loads((folder/'channels.json').read_text(encoding='utf-8'))['channels']
    assert len(channels)==3 and {int(c['network'],16) for c in channels}=={10,11,12}
    for c in channels:
        net=int(c['network'],16)
        assert {(x['node'],x['port']) for x in c['uplinks']}=={k for k,v in EXTERNAL.items() if v==net}
        for u in c['uplinks']:
            p=ports[u['node'],u['port']]
            assert tuple(u['peer'])==(p['options'].get('bind','127.0.0.1'),p['udp'])
            assert p['peer'][1]==c['listen'][1]
        assert all(int(h['address'],16)>>8==net for h in c['hosts'])
    def walk(node,dest,seen):
        assert node not in seen, ('转发表形成环路',node,dest,seen)
        net=dest>>8
        if (net==1 and dest&255==node) or any(n==node and v==net for (n,p),v in EXTERNAL.items()): return
        key=dest if net==1 else net<<8
        assert key in routes[node], ('缺少路由',node,dest)
        for port in routes[node][key]:
            assert (node,port) in INTERNAL, ('下一跳非内部端口',node,port)
            walk(INTERNAL[node,port][0],dest,seen|{node})
    for n in range(1,5):
        assert all(key>>8 in (1,10,11,12) for key in routes[n]), '遗留第四外部网络'
        for dest in (0x0101,0x0102,0x0103,0x0104,0x0A01,0x0B01,0x0C01): walk(n,dest,set())


class TopologyTests(unittest.TestCase):
    def test_all_deployments_match_figure_and_have_loop_free_routes(self):
        for mode in MODES:
            with self.subTest(mode=mode): verify(ROOT/mode)

    def test_hierarchical_tables_match_lecture(self):
        folder=ROOT/'config-hier'; ports,_=parse(folder)
        expected={(1,3):65535,(3,2):65535,(2,1):3,(4,1):3,(3,3):5,(4,5):5}
        self.assertEqual(set(ports),set(INTERNAL)|set(expected))
        for key,other in INTERNAL.items(): self.assertEqual(ports[key]['peer'][1],ports[other]['udp'])
        for key,logical in expected.items(): self.assertEqual(ports[key]['net'],logical)
        ept={(65535,1,3,622,0),(65535,3,2,155,0),(3,2,1,155,0),
             (3,4,1,45,0),(5,4,5,100,0),(5,3,3,45,0)}
        for n in range(1,5):
            rows=[x.split() for x in (folder/f'node{n}.conf').read_text(encoding='utf-8').splitlines()]
            self.assertEqual({tuple(map(int,x[1:])) for x in rows if x and x[0]=='ept'},ept)
            self.assertIn(['ab','32/8'],rows)
            self.assertEqual({int(x[1]) for x in rows if x and x[0]=='route'},set(range(1,5))-{n})


if __name__=='__main__': unittest.main()
