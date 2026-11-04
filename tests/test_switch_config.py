#!/usr/bin/env python3
"""通过真实交换节点的启动结果验证配置兼容性和错误拒绝，不需要 root。"""
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'bin/switch'


@unittest.skipUnless(sys.platform.startswith('linux'), '交换节点需要 Linux')
class SwitchConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BINARY.is_file():
            raise RuntimeError('先执行 make，生成 bin/switch')

    def setUp(self):
        # 配置会打开 UDP socket，使用临时端口避免依赖固定的实验端口。
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.config = ('id 1\n'
                       'port 1 ext 0x0A {port} 127.0.0.1 9001 type eth\n'
                       'ift 0B00 1\n').format(port=self.port)

    def start(self, config, newline='\n'):
        with tempfile.TemporaryDirectory(prefix='switch-config-') as directory:
            path = Path(directory) / 'node.conf'
            path.write_bytes(config.replace('\n', newline).encode())
            return subprocess.run([str(BINARY), str(path)], input='table\nquit\n',
                                  text=True, capture_output=True, timeout=5)

    def rejected(self, config):
        result = self.start(config)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('已启动', result.stdout)
        self.assertTrue(result.stderr.strip(), '配置错误应有诊断信息')

    def test_hex_external_network_compatibility(self):
        for value, network in (('0A', '0x0A'), ('0x0A', '0x0A'),
                               ('10', '0x10'), ('010', '0x10')):
            with self.subTest(value=value):
                result = self.start(self.config.replace('ext 0x0A', 'ext ' + value))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('外部(网络' + network + '/', result.stdout)

    def test_crlf_matches_lf(self):
        for newline in ('\n', '\r\n'):
            with self.subTest(newline=repr(newline)):
                result = self.start(self.config, newline)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('节点1已启动', result.stdout)
                self.assertIn('外部(网络0x0A/', result.stdout)
                self.assertIn('0x0B00', result.stdout)

    def test_reject_invalid_integer_values(self):
        unsigned_max = (1 << (8 * struct.calcsize('L'))) - 1
        for value in ('0', '256', '1tail', '-1', '-' + str(unsigned_max),
                      str(unsigned_max + 1)):
            with self.subTest(field='id', value=value):
                self.rejected(self.config.replace('id 1\n', 'id ' + value + '\n'))
        for value in ('0', '70000', '9x', '-1'):
            with self.subTest(field='local_udp', value=value):
                self.rejected(self.config.replace(str(self.port), value, 1))
            with self.subTest(field='peer_udp', value=value):
                self.rejected(self.config.replace(' 9001 ', ' ' + value + ' '))
        for value in ('0', '256', '1x'):
            with self.subTest(field='port_id', value=value):
                self.rejected(self.config.replace('port 1 ', 'port ' + value + ' '))
        for value in ('100', '0x100', '-1', '0Ax'):
            with self.subTest(field='network', value=value):
                self.rejected(self.config.replace('ext 0x0A', 'ext ' + value))

    def test_require_one_node_id(self):
        self.rejected(self.config.replace('id 1\n', ''))
        self.rejected('id 2\n' + self.config)

    def test_reject_invalid_port_fields(self):
        cases = (
            self.config.replace(' ext ', ' unknown '),
            self.config.replace('127.0.0.1', 'not-an-ip'),
            self.config.replace('type eth', 'unknown value'),
            self.config.replace('type eth', 'type'),
            self.config.replace('type eth', 'type ether'),
            'id 1\nport 1\n',
            'id 1\nport 1 ext 0x0A\n',
        )
        for config in cases:
            with self.subTest(config=config):
                self.rejected(config)

    def test_reject_invalid_routes_and_excess_paths(self):
        for route in ('ift bad-key 1', 'ift 10000 1', 'ift 0B00',
                      'ift 0B00 ,', 'ift 0B00 1,1,1,1,1',
                      'ift 0B00 256', 'ift 0B00 0', 'ift 0B00 -1',
                      'ift 0B00 1x', 'ift 0B00 2'):
            with self.subTest(route=route):
                self.rejected(self.config.replace('ift 0B00 1', route))


if __name__ == '__main__':
    unittest.main(verbosity=2)
