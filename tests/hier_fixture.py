"""共享进程夹具；预期拓扑和地址语义由独立验收用例断言。"""
import json
from pathlib import Path
import sys
from test_bluetooth import ROOT, Process
from test_topology_integration import runtime_config

sys.path.insert(0,str(ROOT/'scripts'))
from hier_config import convert


class Fixture:
    def __init__(self,out,a=32,b=8):
        self.out=out; self.processes=[]
        base=out/'legacy'; runtime_config(base)
        self.config=out/'config'; self.data=convert(base,self.config,a,b)
        self.entries=self.data['channels']; self.nodes={}; self.channels={}; self.hosts={}

    def start(self,name,args):
        p=Process(name,list(map(str,args)),self.out); self.processes.append(p); return p

    def launch(self):
        for n in range(1,5):
            self.nodes[n]=self.start('n'+str(n),[ROOT/'bin/switch',self.config/f'node{n}.conf'])
            self.nodes[n].wait('已启动')
        for c in self.entries:
            net=int(c['network'],16)
            self.channels[net]=self.start(c['session'],[sys.executable,ROOT/'scripts/channel.py',
                                     self.config/'channels.json','--network',c['network']])
            self.channels[net].wait('[READY]')
            for h in c['hosts']:
                self.hosts[h['session']]=self.start(h['session'],[ROOT/'bin/host',h['address'],h['peer'][1],
                                                   '127.0.0.1',c['listen'][1]])
                self.hosts[h['session']].wait('已就绪')

    def address(self,name):
        return next(h['address'] for c in self.entries for h in c['hosts'] if h['session']==name)

    def via(self,net,node):
        p=self.channels[net]; offset=len(p.lines); p.send('via '+str(node)); p.wait('[GATEWAY] node='+str(node),offset)

    def status(self,sid,pid,state):
        for p in self.nodes.values():
            offset=len(p.lines); p.send(f'ept {sid} {pid} {state}'); p.wait(f'[STATE] EPID={sid}/{pid} {state}',offset)

    def stop(self):
        for p in reversed(self.processes):
            if not p.log.closed: p.stop()
        return all(p.p.poll() is not None for p in self.processes)
