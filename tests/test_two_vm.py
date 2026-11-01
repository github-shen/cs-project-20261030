#!/usr/bin/env python3
"""SSH协调双Ubuntu VM的四交换节点测试。控制机需paramiko；不接触真实蓝牙硬件。"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shlex
import sys
import threading
import time
import paramiko


class Agent:
    def __init__(self,config,role,peer,run):
        self.client=paramiko.SSHClient()
        self.client.load_host_keys(os.path.expanduser(config['known_hosts']))
        self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        self.client.connect(config['host'],username=config['user'],
                            key_filename=os.path.expanduser(config['key']),
                            look_for_keys=False,allow_agent=False,timeout=15)
        self.role=role; self.stderr=[]
        password=os.environ[config['password_env']] if config.get('password_env') else None
        args=['unshare','--mount','--propagation','private','python3','tests/two_vm_node.py',role,
              '--local-ip',config['host'],'--peer-ip',peer,'--output','logs/'+run]
        sudo=['sudo','-k','-S','-p',''] if password else ['sudo','-n']
        command='cd '+shlex.quote(config['path'])+' && exec '+shlex.join(sudo+args)
        self.stdin,self.stdout,stderr=self.client.exec_command(command,timeout=60)
        if password: self.stdin.write(password+'\n'); self.stdin.flush()
        self.reader=threading.Thread(target=lambda:self.stderr.extend(stderr.readlines()),daemon=True)
        self.reader.start()
        try: self.ready=self.read()
        except BaseException:
            self.stdin.channel.shutdown_write(); self.client.close(); raise

    def read(self):
        line=self.stdout.readline()
        if not line: raise RuntimeError('VM '+self.role+' worker退出: '+''.join(self.stderr[-10:]))
        value=json.loads(line)
        if value.get('ok') is False: raise AssertionError(value['error'])
        return value

    def call(self,action,**kwargs):
        self.stdin.write(json.dumps(dict(action=action,**kwargs))+'\n'); self.stdin.flush()
        return self.read().get('result')

    def finish(self):
        self.stdin.write('{"action":"finish"}\n'); self.stdin.flush()
        try:
            result=self.read()
            self.stdout.channel.recv_exit_status()
            return result['cleanup']
        finally: self.client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('config',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--run-name',default=datetime.now().strftime('two-vm-%Y%m%d-%H%M%S'))
    o=p.parse_args()
    if not all(c.isalnum() or c in '-_' for c in o.run_name): p.error('run-name仅字母/数字/-/_')
    cfg=json.loads(o.config.read_text(encoding='utf-8'))
    o.output.mkdir(parents=True,exist_ok=False)
    result={'started_at':datetime.now(timezone.utc).isoformat(),'status':'FAIL',
            'topology':{'a':['S1','S2','Down3'],'b':['S3','S4','Upper','Down5','Down1']},
            'radio':'EMULATED - HCI over private IP; physical radio NOT VERIFIED', 'checks':{}}
    agents={}
    def check(name,evidence):
        result['checks'][name]={'status':'PASS','evidence':evidence}; print(name+': PASS',flush=True)
    def wait(agent,process,text,offset=0): return agent.call('wait',process=process,text=text,offset=offset)
    def send(agent,process,text): return agent.call('send',process=process,text=text)
    try:
        a=agents['a']=Agent(cfg['a'],'a',cfg['b']['host'],o.run_name)
        b=agents['b']=Agent(cfg['b'],'b',cfg['a']['host'],o.run_name)
        a.call('setup',peer=b.ready['address']); b.call('setup',peer=a.ready['address'])
        for agent in (a,b):
            for channel in ('ext','int'): wait(agent,channel,'[BT] connected')
        check('two_kernel_l2cap_connections',True)
        hosts={'upper':(b,'0x0A01'),'down3':(a,'0x0B01'),'down5':(b,'0x0C01'),'down1':(b,'0x0D01')}
        evidence=[]
        for src,(sa,_) in hosts.items():
            for dst,(da,addr) in hosts.items():
                if src==dst: continue
                token='cross_%s_%s'%(src,dst)
                send(sa,src,'send %s %s'%(addr,token)); evidence.append(wait(da,dst,token))
        check('four_domain_12_directions',evidence)
        send(b,'upper','sendbad 0x0C01 cross_bad_crc')
        check('crc_rejected',wait(a,'node1','CRC校验失败'))
        assert not any('cross_bad_crc' in s for s in b.call('lines',process='down5'))
        offset=len(b.call('lines',process='upper'))
        send(a,'node1','send 0x0A01 cross_node_normal')
        normal=wait(b,'upper','cross_node_normal',offset)
        send(a,'node1','sendbad 0x0A01 cross_node_bad_crc')
        dropped=wait(b,'upper','CRC校验失败',offset)
        assert not any('[RECV]' in s and 'cross_node_bad_crc' in s for s in b.call('lines',process='upper'))
        check('node_sendbad_across_vms',{'normal':normal,'corrupted':dropped})
        offset=len(a.call('lines',process='ext'))
        b.call('stop',process='ext'); wait(a,'ext','disconnected',offset)
        send(a,'down3','send 0x0A01 cross_offline_no_replay')
        dropped=wait(a,'ext','蓝牙信道未连接',offset)
        b.call('restart',process='ext',peer=a.ready['address'])
        send(b,'upper','send 0x0B01 cross_reconnected'); wait(a,'down3','cross_reconnected')
        send(a,'down3','send 0x0A01 cross_reverse_reconnected'); wait(b,'upper','cross_reverse_reconnected')
        assert not any('cross_offline_no_replay' in s for s in b.call('lines',process='upper'))
        check('disconnect_and_bidirectional_recovery',{'offline_drop':dropped,'stale_replay':False})
        b.call('stop',process='upper')
        b.call('inject',source=0x0B02,destination=0x0C01,token='cross_spoof_')
        check('source_binding',wait(a,'node1','源地址校验失败'))
        offset=len(a.call('lines',process='node1'))
        b.call('inject',source=0x0A01,destination=0x0C01,token='cross_flow_',count=64)
        for i in range(64): wait(b,'down5','"cross_flow_%d"'%i)
        lines=a.call('lines',process='node1')[offset:]
        counts={str(port):sum('出端口%d dst=0x0C01'%port in s for s in lines) for port in (1,2)}
        assert sum(counts.values())==64 and all(counts.values())
        check('64_address_flows',{'received':64,'node1_outputs':counts,'key':'logical src/dst; not five-tuple'})
        for role,agent in agents.items():
            monitor=agent.call('lines',process='btmon')
            assert any('ACL Data' in s for s in monitor)
            acl=[s for s in monitor if any(tag in s for tag in ('ACL Data','L2CAP:','PSM:','Channel:'))]
            # 只保留传输证据，不提交HCI密钥、主机名等完整配对日志。
            (o.output/('hci-'+role+'.log')).write_text('\n'.join(acl)+'\n',encoding='utf-8')
        counters={}
        for role,agent,names in (('a',a,['ext','int']),('b',b,['ext-reconnected','int'])):
            for name in names:
                offset=len(agent.call('lines',process=name)); agent.call('signal',process=name)
                counters[role+'-'+name]=wait(agent,name,'[STATS]',offset)
        result['channel_counters']=counters
        for role,agent,ids in (('a',a,[1,2]),('b',b,[3,4])):
            for n in ids:
                send(agent,'node%d'%n,'stats'); wait(agent,'node%d'%n,'接收=')
                (o.output/('node%d.log'%n)).write_text('\n'.join(agent.call('lines',process='node%d'%n))+'\n',encoding='utf-8')
        result['status']='PASS'
    except Exception as error:
        result['error']=str(error); print('FAIL: '+str(error),file=sys.stderr,flush=True)
    finally:
        result['cleanup']={}
        for role,agent in reversed(list(agents.items())):
            try: result['cleanup'][role]=agent.finish()
            except Exception as error: result['cleanup'][role]={'error':str(error)}; result['status']='FAIL'
        if len(agents)!=2 or not all(v is True for item in result['cleanup'].values() for v in item.values()):
            result['status']='FAIL'
        (o.output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__': sys.exit(main())
