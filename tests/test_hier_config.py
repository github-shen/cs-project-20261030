import subprocess
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


class HierConfigTests(unittest.TestCase):
    def run_config(self,text):
        if not (ROOT/'bin/switch').exists(): self.skipTest('Run make on Linux')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'node.conf'; p.write_text(text,encoding='utf-8')
            return subprocess.run([ROOT/'bin/switch',p],input='quit\n',capture_output=True,text=True,timeout=5)

    def test_valid_and_crlf(self):
        text=(ROOT/'config-hier/node1.conf').read_text(encoding='utf-8')
        for body in (text,text.replace('\n','\r\n')):
            with self.subTest(crlf='\r' in body):
                p=self.run_config(body); self.assertEqual(p.returncode,0,p.stderr)
                self.assertIn('a/b=32/8',p.stdout)

    def test_invalid_fields_tables_and_parameters_rejected(self):
        original=(ROOT/'config-hier/node1.conf').read_text(encoding='utf-8')
        changes=[('ab 32/8','ab 128/1'),('ab 32/8','ab -1/8'),('ab 32/8','ab 32/0'),
                 ('ab 32/8','ab 32/17'),('ab 32/8','ab 121/8'),('ab 32/8','ab 32/8x'),
                 ('ab 32/8','ab 32/8\nab 32/8'),('domain 2001:db8::','domain bad'),
                 ('ept 3 2 1 155 0','ept 256 2 1 155 0'),
                 ('ept 3 2 1 155 0','ept 3 2 1 0 0'),('ept 3 2 1 155 0','ept 3 2 1 155 2'),
                 ('ept 65535 1 3 622 0','ept 65535 1 2 622 0'),
                 ('route 2 1:1:0,2:2:0','route 2 3:1:0'),
                 ('route 2 1:1:0,2:2:0','route 2 1:1:0,1:2:0'),
                 ('route 2 1:1:0,2:2:0','route 2 1:0:0'),
                 ('id 1','id 1\nid 2')]
        for old,new in changes:
            with self.subTest(new=new):
                self.assertIn(old,original)
                p=self.run_config(original.replace(old,new))
                self.assertNotEqual(p.returncode,0)
                self.assertIn('配置错误',p.stderr)


if __name__=='__main__': unittest.main()
