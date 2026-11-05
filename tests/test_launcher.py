#!/usr/bin/env python3
"""验收 README 使用的 tmux 启动器：就绪、拒绝重复启动、失败回滚。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from test_bluetooth import ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    out=parser.parse_args().output.resolve(); out.mkdir(parents=True,exist_ok=False)
    result={'status':'FAIL','checks':{}}
    with tempfile.TemporaryDirectory(prefix='cnet-launch-') as directory:
        env=os.environ.copy(); env.pop('TMUX',None); env['TMUX_TMPDIR']=directory
        def run(*args):
            return subprocess.run(list(map(str,args)),cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
        def tmux(*args): return run('tmux','-L','cnet',*args)
        try:
            p=run('bash','scripts/run_tmux.sh')
            (out/'startup.log').write_text(p.stdout+p.stderr)
            assert p.returncode==0,p.stdout+p.stderr
            expected={'n1','n2','n3','n4','chUp','ch3','ch5','hA1','hA2','hB1','hC1'}
            assert set(tmux('list-sessions','-F','#{session_name}').stdout.split())==expected
            tmux('send-keys','-t','hA1','send 2001:db8:500::1 readme-launch','Enter')
            end=time.monotonic()+5
            while time.monotonic()<end:
                p=tmux('capture-pane','-p','-t','hC1','-S','-100')
                if '[RECV] 来自2001:db9::1: "readme-launch"' in p.stdout: break
                time.sleep(.05)
            else: raise AssertionError('README command did not deliver')
            result['checks']['eleven_ready_sessions_and_readme_delivery']='PASS'
            p=run('bash','scripts/run_tmux.sh')
            assert p.returncode!=0 and '已有项目会话' in p.stderr
            assert set(tmux('list-sessions','-F','#{session_name}').stdout.split())==expected
            result['checks']['duplicate_launch_preserves_running_sessions']='PASS'
            tmux('kill-server')
            config=out/'invalid-config'; shutil.copytree(ROOT/'config-hier',config)
            path=config/'node3.conf'; path.write_text(path.read_text().replace('ab 32/8','ab 32/0'))
            p=run('bash','scripts/run_tmux.sh',config)
            (out/'rejected-startup.log').write_text(p.stdout+p.stderr)
            assert p.returncode!=0 and 'n3 未就绪' in p.stderr
            assert tmux('list-sessions').returncode!=0
            result['checks']['partial_startup_rolled_back']='PASS'
            result['status']='PASS'
        except Exception as error:
            result['error']=str(error)
        finally:
            tmux('kill-server')
            result['cleanup']={'private_tmux_stopped':tmux('list-sessions').returncode!=0}
            if not all(result['cleanup'].values()): result['status']='FAIL'
    (out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__': raise SystemExit(main())
