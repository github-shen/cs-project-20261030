#!/usr/bin/env python3
"""将已核对题图连线的 UDP/OVS/双网卡配置转换为 a/b 层次交换配置。"""
import argparse
import copy
import ipaddress
import json
from pathlib import Path

EPT=[(65535,1,3,622,0),(65535,3,2,155,0),(3,2,1,155,0),
     (3,4,1,45,0),(5,4,5,100,0),(5,3,3,45,0)]
# SID -> (下一跳本地端口, 跳数, 状态)，按课件表 3.2～3.5。
ROUTES={1:{2:[(1,1,0),(2,2,0)],3:[(2,2,0),(1,2,0)],4:[(2,1,0),(1,2,0)]},
        2:{1:[(4,1,0),(2,2,0)],3:[(3,1,0),(2,2,0)],4:[(2,1,0),(4,2,0),(3,2,0)]},
        3:{1:[(4,2,0),(1,2,0)],2:[(1,1,0),(4,2,0)],4:[(4,1,0),(1,2,0)]},
        4:{1:[(3,1,0),(2,2,0)],2:[(2,1,0),(4,2,0)],3:[(4,1,0),(2,2,0)]}}


def address(prefix,a,b,value,station):
    bits=int(ipaddress.IPv6Address(prefix))
    bits=(bits>>(128-a)<<(128-a)) if a else 0
    return str(ipaddress.IPv6Address(bits | value<<(128-a-b) | station))


def convert(source,target,a=32,b=8,prefix='2001:db8::'):
    if not (1<=a and 3<=b<=16 and a+b<=120):
        raise ValueError('示例生成器要求 a>=1、3<=b<=16、a+b<=120；运行引擎支持 a=0 及字段末尾到 bit127')
    target.mkdir(parents=True,exist_ok=True)
    node_addresses={n:address(prefix,a,b,(1<<b)-1,n) for n in range(1,5)}
    channels=copy.deepcopy(json.loads((source/'channels.json').read_text(encoding='utf-8')))
    # 上行终端前 a 位与本域不同；其它位可由用户按上层地址规划替换。
    upper_prefix=str(ipaddress.IPv6Address(int(ipaddress.IPv6Address(prefix))^(1<<(128-a))))
    mapping={10:65535,11:3,12:5}
    for n in range(1,5):
        lines=[f'# 节点{n}；题图端口 + 全域 EPT + a/b 字段交换',f'id {n}',f'ab {a}/{b}',f'domain {prefix}','']
        lines += [f'node {sid} {ip}' for sid,ip in node_addresses.items()]
        lines.append('')
        for line in (source/f'node{n}.conf').read_text(encoding='utf-8').splitlines():
            w=line.split('#',1)[0].split()
            if not w or w[0]!='port': continue
            if w[2]=='ext': w[3]=f'0x{mapping[int(w[3],16)]:04X}'
            lines.append(' '.join(w))
        lines+=['','# ept Lport SID PID Bandwidth Status；0=可用，1=停用']
        lines += ['ept '+' '.join(map(str,e)) for e in EPT]
        lines+=['','# route SID PID:HOP:Status,...']
        lines += [f'route {sid} '+','.join(':'.join(map(str,p)) for p in paths) for sid,paths in ROUTES[n].items()]
        (target/f'node{n}.conf').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    for c in channels['channels']:
        old=int(c['network'],16); logical=mapping[old]
        c['network']=f'0x{logical:04X}'; c['ab']=[a,b]; c['domain']=prefix
        for i,h in enumerate(c['hosts'],1):
            h['address']=address(upper_prefix,a,b,0,i) if logical==65535 else address(prefix,a,b,logical,i)
            h['session']=('hA'+str(i)) if old==10 else ('hB1' if old==11 else 'hC1')
        c['session']={10:'chUp',11:'ch3',12:'ch5'}[old]
    channels['node_addresses']={str(k):v for k,v in node_addresses.items()}
    (target/'channels.json').write_text(json.dumps(channels,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return channels


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--ab',default='32/8')
    parser.add_argument('--domain',default='2001:db8::')
    opts=parser.parse_args()
    a,b=map(int,opts.ab.split('/'))
    if opts.source.resolve()==opts.output.resolve(): parser.error('源配置和输出目录必须不同')
    if opts.output.exists() and any(opts.output.iterdir()): parser.error('输出目录须为空，避免覆盖本地部署修改')
    convert(opts.source,opts.output,a,b,opts.domain)
    print(opts.output)


if __name__=='__main__': main()
