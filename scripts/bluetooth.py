#!/usr/bin/env python3
"""蓝牙设备只读检测 / 显式配置启动；不改IP、默认路由或自动配对。"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def probe():
    controllers = []
    for device in sorted(Path('/sys/class/bluetooth').glob('hci[0-9]*')):
        result = subprocess.run(['hciconfig', device.name], capture_output=True,
                                text=True, timeout=5, check=True)
        match = re.search(r'BD Address:\s*([0-9A-F:]{17})', result.stdout)
        controllers.append({'name': device.name,
                            'address': match.group(1) if match else None,
                            'kind': 'virtual' if '/virtual/' in str(device.resolve()) else 'hardware',
                            'up': 'UP RUNNING' in result.stdout})
    return {'system': platform.platform(), 'controllers': controllers,
            'btvirt': shutil.which('btvirt'), 'vhci': Path('/dev/vhci').exists(),
            'physical_radio_test': 'NOT VERIFIED'}


def arguments(config, allow_insecure=False):
    if not isinstance(config, dict):
        raise ValueError('配置根节点必须为JSON对象')
    allowed = {'mode', 'local_address', 'peer_address', 'psm', 'udp_port', 'app_port', 'security'}
    if set(config) - allowed:
        raise ValueError('未知配置键: ' + ', '.join(sorted(set(config) - allowed)))
    if config.get('mode') not in ('listen', 'connect'):
        raise ValueError('mode 必须为 listen 或 connect')
    for key in ('local_address', 'peer_address'):
        value = config.get(key, '')
        if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', value):
            raise ValueError(key + ' 必须显式填写蓝牙地址')
    security = config.get('security', 'medium')
    if security not in ('medium', 'high', 'low'):
        raise ValueError('security 必须为 medium/high/low')
    if security == 'low' and not allow_insecure:
        raise ValueError('low 仅供隔离实验；需显式 --allow-insecure')
    args = [str(ROOT/'bin/bt-channel'), config['mode'], config['local_address'],
            config['peer_address']]
    for key in ('psm', 'udp_port', 'app_port'):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError(key + ' 必须为整数或整数字符串')
        int(str(value), 0)
        args.append(str(value))
    return args + [security]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('probe', help='只读检测蓝牙控制器，区分虚拟设备')
    run = sub.add_parser('run', help='按配置启动一端蓝牙信道')
    run.add_argument('config', type=Path)
    run.add_argument('--allow-insecure', action='store_true')
    opts = parser.parse_args()
    try:
        if opts.command == 'probe':
            print(json.dumps(probe(), ensure_ascii=False, indent=2))
            return
        config = json.loads(opts.config.read_text(encoding='utf-8'))
        args = arguments(config, opts.allow_insecure)
        # exec 保留PID和信号语义，调用方可准确停止自己的信道进程。
        os.execv(args[0], args)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, '蓝牙操作失败: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
