#!/usr/bin/env python3
"""用两组隔离veth执行真实demo_nic/link_test脚本；不声称物理Wi-Fi已验证。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time
from test_bluetooth import ROOT, environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    opts = parser.parse_args()
    if os.geteuid() != 0: parser.error('需要root，仅创建和清理本测试的Namespace/veth/tmux')
    out = opts.output.resolve(); out.mkdir(parents=True, exist_ok=False)
    fixture = out/'project'; fixture.mkdir()
    env = os.environ.copy(); env.pop('TMUX', None); env.pop('SSH_CONNECTION', None)
    tmux_dir = out/'tmux'; tmux_dir.mkdir(mode=0o700)
    env['TMUX_TMPDIR'] = str(tmux_dir)
    namespaces = []; links = []; sessions = []
    ns_a, ns_b = ('cnet-review-%d-%s' % (os.getpid(), side) for side in ('a','b'))
    result = {'started_at':datetime.now(timezone.utc).isoformat(), 'status':'FAIL',
              'media':'two veth paths; physical Ethernet/Wi-Fi NOT VERIFIED', 'checks':{}}
    before = environment()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))

    def run(*args, check=True, **kwargs):
        p = subprocess.run(list(map(str,args)), env=env, cwd=fixture, text=True,
                           capture_output=True, timeout=90, **kwargs)
        if check and p.returncode: raise RuntimeError(' '.join(map(str,args))+': '+p.stdout+p.stderr)
        return p

    def ip(ns, *args): return run('ip','-n',ns,*args)
    def tmux(*args, **kwargs): return run('tmux',*args,**kwargs)
    def log(name):
        p=out/(name+'.log')
        return p.read_text(errors='replace') if p.exists() else ''

    def start(name, ns, args, ready):
        command = shlex.join(['ip','netns','exec',ns,'stdbuf','-oL','-eL']+list(map(str,args)))
        # tee同时保留真实日志和终端显示，以覆盖演示脚本capture-pane的取证路径。
        command = command+' 2>&1 | tee '+shlex.quote(str(out/(name+'.log')))
        tmux('new-session','-d','-s',name,'-x','240','-y','80',command)
        sessions.append(name)
        end=time.monotonic()+8
        while time.monotonic()<end:
            if ready in log(name): return
            time.sleep(.05)
        raise AssertionError(name+'未启动: '+log(name))

    def script(name, *args, **kwargs):
        p=run('ip','netns','exec',ns_a,'bash',fixture/'scripts'/name,*args,check=False,**kwargs)
        (out/(name+'.log')).write_text(p.stdout+p.stderr,encoding='utf-8')
        assert p.returncode==0, name+': '+p.stdout+p.stderr
        return p.stdout

    def received(host, token):
        return any('[RECV]' in s and token in s for s in log(host).splitlines())

    def check(name,evidence):
        result['checks'][name]={'status':'PASS','evidence':evidence}; print(name+': PASS',flush=True)

    try:
        for name in ('bin','scripts','config-nic'): (fixture/name).mkdir()
        for name in ('switch','host'): shutil.copy2(ROOT/'bin'/name,fixture/'bin'/name)
        for name in ('demo_nic.sh','link_test.sh'): shutil.copy2(ROOT/'scripts'/name,fixture/'scripts'/name)
        values={'A_ETH':'192.0.2.1','B_ETH':'192.0.2.2','A_WIFI':'198.51.100.1','B_WIFI':'198.51.100.2'}
        for node in range(1,5):
            text=(ROOT/'config-nic-template'/('node%d.conf'%node)).read_text()
            for key,value in values.items(): text=text.replace('@'+key+'@',value)
            text=text.replace('ens33','nrf-eth').replace('ens37','nrf-wifi')
            (fixture/'config-nic'/('node%d.conf'%node)).write_text(text)
        (fixture/'config-nic/ips.env').write_text(''.join(k+'='+v+'\n' for k,v in values.items())+
                                                'ETH_DEV=nrf-eth\nWIFI_DEV=nrf-wifi\n')
        for ns in (ns_a,ns_b):
            run('ip','netns','add',ns); namespaces.append(ns); ip(ns,'link','set','lo','up')
        for index,medium in enumerate(('eth','wifi')):
            a,b=('nr%d%d%s'%(os.getpid(),index,side) for side in ('a','b'))
            run('ip','link','add',a,'type','veth','peer','name',b); links.append(a)
            run('ip','link','set',a,'netns',ns_a); run('ip','link','set',b,'netns',ns_b)
            for ns,old,side in ((ns_a,a,'A'),(ns_b,b,'B')):
                dev='nrf-'+medium
                ip(ns,'link','set',old,'name',dev)
                ip(ns,'address','add',values[side+'_'+medium.upper()]+'/30','dev',dev)
                ip(ns,'link','set',dev,'up')

        # 管理接口保护只在测试Namespace中验证，不触碰宿主默认路由。
        ip(ns_a,'route','add','default','via',values['B_WIFI'],'dev','nrf-wifi')
        protected=run('ip','netns','exec',ns_a,'bash',fixture/'scripts/link_test.sh',input='YES\n',check=False)
        (out/'management-guard.log').write_text(protected.stdout+protected.stderr)
        assert protected.returncode != 0 and '承载默认路由' in protected.stderr
        assert 'UP' in ip(ns_a,'-o','link','show','nrf-wifi').stdout
        ip(ns_a,'route','del','default')
        check('management_interface_guard','default-route interface refused, remained UP')

        for node in range(1,5):
            start('n%d'%node, ns_a if node<3 else ns_b,
                  [fixture/'bin/switch',fixture/'config-nic'/('node%d.conf'%node)],'已启动')
        for name,ns,args in (
                ('h0x0A01',ns_a,['0x0A01',9001,'127.0.0.1',1103]),
                ('h0x0A02',ns_a,['0x0A02',9005,'127.0.0.1',1103]),
                ('h0x0B01',ns_b,['0x0B01',9002,values['A_WIFI'],3104,'bind',values['B_WIFI']]),
                ('h0x0C01',ns_a,['0x0C01',9003,values['B_ETH'],2104,'bind',values['A_ETH']]),
                ('h0x0D01',ns_a,['0x0D01',9004,values['B_ETH'],4103,'bind',values['A_ETH']]),
                ('spoof',ns_a,['0x0C05',9006,'127.0.0.1',1103])):
            start(name,ns,[fixture/'bin/host']+args,'已就绪')

        # 独立tmux服务器；既执行原演示，也核对真实目的终端的日志。
        script('demo_nic.sh','A')
        for host,token,src in (('h0x0C01','nic-eth-1','0x0A02'),('h0x0C01','nic-wifi-1','0x0A01'),
                               ('h0x0B01','nic-wifi-2','0x0A01'),('h0x0D01','nic-eth-2','0x0A01')):
            assert any('[RECV]' in line and token in line and src in line for line in log(host).splitlines())
        outputs=re.findall(r'出端口([12]) dst=0x0C01',log('n1'))
        assert outputs==['1','2','1','2'], outputs
        check('demo_nic_source_and_path',{'source_0A02':'port1 / wired path','source_0A01':'port2 / Wi-Fi-labeled path',
                                          'received':4,'node1_outputs_including_stats':outputs})
        text=script('link_test.sh',input='YES\n')
        assert any(s.strip().startswith('[REROUTE] 端口2') for s in text.splitlines()), '演示未显示实际重路由日志'
        assert any(s.strip().startswith('[DROP] 端口4发送失败') for s in text.splitlines()), '演示未显示单路径丢弃日志'
        run_id=re.search(r'本次标识：(\d+)',text).group(1)
        flows=[('h0x0C01','lt-eth-1'),('h0x0C01','lt-wifi-1'),('h0x0B01','lt-wifi-2'),('h0x0D01','lt-eth-2')]
        observed={}
        for phase in ('baseline','down','restored'):
            observed[phase]=[received(host,'%s-%s-%s'%(token,phase,run_id)) for host,token in flows]
        assert observed=={'baseline':[True]*4,'down':[True,True,False,True],'restored':[True]*4},observed
        reroutes=[s for s in log('n1').splitlines() if '[REROUTE]' in s and '等价端口1' in s]
        assert len(reroutes)==1,reroutes
        assert '端口4发送失败' in log('n2')
        assert 'UP' in ip(ns_a,'-o','link','show','nrf-wifi').stdout
        check('link_failure_and_restore',{'flows':['wired C','Wi-Fi primary C','Wi-Fi only B','wired D'],
                                          'received':observed,'reroute':reroutes,'interface_restored':True})
        result['status']='PASS'
    except Exception as error:
        result['error']=str(error); print('FAIL: '+str(error),flush=True)
    finally:
        # TMUX_TMPDIR指向本次独有目录，只关闭本测试创建的服务器。
        if sessions: tmux('kill-server',check=False)
        for ns in reversed(namespaces):
            pids=run('ip','netns','pids',ns,check=False).stdout.split()
            for pid in pids:
                try: os.kill(int(pid),signal.SIGKILL)
                except ProcessLookupError: pass
            run('ip','netns','del',ns,check=False)
        for link in links:
            # 仅处理创建后、移动Namespace前失败时可能留下的本次veth。
            if run('ip','link','show',link,check=False).returncode==0: run('ip','link','del',link)
        after=environment()
        result['cleanup']={'environment_unchanged':before==after,
                           'private_tmux_stopped':tmux('list-sessions',check=False).returncode!=0}
        if not all(result['cleanup'].values()): result['status']='FAIL'
        (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
