#!/usr/bin/env python3
"""配置拒绝测试：防止低安全级别误启用及无效参数进入网络调用。"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from bluetooth import arguments


class Configuration(unittest.TestCase):
    def setUp(self):
        self.config=json.loads((ROOT/'config-bt/server.example.json').read_text())

    def test_secure_default_and_explicit_low(self):
        self.config.pop('security')
        self.assertEqual(arguments(self.config)[-1],'medium')
        self.config['security']='low'
        with self.assertRaises(ValueError): arguments(self.config)
        self.assertEqual(arguments(self.config,True)[-1],'low')

    def test_reject_invalid_json_values(self):
        for value in ([],None,5):
            with self.assertRaises(ValueError): arguments(value)
        for key,value in (('local_address',None),('local_address','invalid'),
                          ('udp_port',True),('udp_port',[]),('unexpected',1)):
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                arguments(dict(self.config,**{key:value}))

    def test_native_validation_before_socket(self):
        binary=ROOT/'bin/bt-channel'
        if not binary.exists(): self.skipTest('先执行make bluetooth，需Linux')
        valid=['listen','02:00:00:00:00:01','02:00:00:00:00:02','0x1001','19103','1103']
        for index,value in ((0,'unknown'),(1,'bad-address'),(2,valid[1]),
                            (3,'0x1002'),(3,'0x1101'),(4,'70000'),(5,'19103')):
            args=valid.copy(); args[index]=value
            with self.subTest(index=index,value=value):
                result=subprocess.run([str(binary)]+args,capture_output=True,timeout=3)
                self.assertEqual(result.returncode,2,result.stderr.decode())


if __name__=='__main__': unittest.main(verbosity=2)
