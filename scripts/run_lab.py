#!/usr/bin/env python3
"""在项目专用 tmux 服务器中启动题图的节点、共享信道和终端。"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path, nargs='?', default=Path('config-hier'))
    parser.add_argument('--role', choices=['A', 'B'])
    parser.add_argument('--sudo', action='store_true', help='节点使用 sudo -n，需先 sudo -v')
    parser.add_argument('--namespaces', action='store_true', help='使用已准备的 ns1～4、ns11～13')
    args = parser.parse_args()
    folder = args.config.resolve()
    channels = json.loads((folder/'channels.json').read_text(encoding='utf-8'))['channels']
    commands = []
    def add(name, command, ns=None, privileged=False):
        if args.namespaces:
            command = ['sudo', '-n', 'ip', 'netns', 'exec', 'ns'+str(ns), *command]
        elif args.sudo and privileged:
            command = ['sudo', '-n', *command]
        ready = '已启动' if name.startswith('n') else ('已就绪' if name.startswith('h') else '[READY]')
        commands.append((name, list(map(str, command)), ready))
    for n in range(1, 5):
        if not args.role or args.role == ('A' if n < 3 else 'B'):
            add('n'+str(n), [ROOT/'bin/switch', folder/f'node{n}.conf'], n, True)
    for c in channels:
        if args.role and c['role'] != args.role:
            continue
        ns = {'upper':11,'down3':12,'down5':13}[c['name']]
        add(c.get('session','ch'+c['network'][2:]), [sys.executable, ROOT/'scripts/channel.py',
                                  folder/'channels.json', '--network', c['network']], ns)
        for host in c['hosts']:
            add(host.get('session','h'+host['address']), [ROOT/'bin/host', host['address'], host['peer'][1],
                                     '127.0.0.1', c['listen'][1]], ns)
    for name, _, _ in commands:
        if subprocess.run(['tmux', '-L', 'cnet', 'has-session', '-t', name],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            parser.exit(1, f'已有项目会话 {name}；先检查或执行 scripts/stop_all.sh\n')
    owned = []
    try:
        for name, command, ready in commands:
            subprocess.run(['tmux', '-L', 'cnet', 'new-session', '-d', '-s', name,
                            '-c', str(ROOT), shlex.join(command)], check=True)
            owned.append(name)
            end=time.monotonic()+8
            while True:
                pane=subprocess.run(['tmux','-L','cnet','capture-pane','-p','-t',name,'-S','-100'],
                                    capture_output=True,text=True)
                if pane.returncode or time.monotonic()>end:
                    raise RuntimeError(f'{name} 未就绪；检查端口占用、配置和权限。\n{pane.stdout}{pane.stderr}')
                if ready in pane.stdout: break
                time.sleep(.05)
    except (OSError, subprocess.CalledProcessError, RuntimeError):
        for name in owned:
            subprocess.run(['tmux', '-L', 'cnet', 'kill-session', '-t', name], check=False)
        raise
    subprocess.run(['tmux', '-L', 'cnet', 'list-sessions'], check=True)
    print('观察: tmux -L cnet attach -t n1；停止: bash scripts/stop_all.sh')


if __name__ == '__main__':
    main()
