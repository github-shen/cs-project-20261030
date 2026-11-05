import binascii
import importlib.util
from pathlib import Path
import struct
import unittest
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))

spec=importlib.util.spec_from_file_location('channel',Path(__file__).resolve().parents[1]/'scripts/channel.py')
channel=importlib.util.module_from_spec(spec); spec.loader.exec_module(channel)


def packet(src=0x0A05,dst=0x0C01,body=b'hello',ttl=8):
    raw=struct.pack('=HHBBH',dst,src,ttl,len(body),0)+body
    crc=binascii.crc_hqx(raw,0xffff)
    return raw[:6]+struct.pack('=H',crc)+body


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.ch=channel.Channel({'network':'0x0A','listen':['127.0.0.1',9101],
            'uplinks':[{'node':1,'peer':['127.0.0.1',1103]},{'node':3,'peer':['127.0.0.1',2102]}]})
        self.host=('127.0.0.1',19005)

    def test_learned_host_returns_through_either_attachment(self):
        self.assertEqual(self.ch.destination(packet(),self.host),('127.0.0.1',1103))
        for node in self.ch.nodes.values():
            self.assertEqual(self.ch.destination(packet(src=0x0C01,dst=0x0A05),node),self.host)

    def test_gateway_selection_sends_one_copy(self):
        self.ch.control('via 3')
        self.assertEqual(self.ch.destination(packet(),self.host),('127.0.0.1',2102))
        self.ch.control('via 4')
        self.assertEqual(self.ch.gateway,3)

    def test_bad_frames_cannot_poison_learning(self):
        raw=packet()
        for data in (raw[:-1],raw+b'x',raw[:6]+b'\0\0'+raw[8:],packet(src=0x0B05)):
            with self.subTest(data=data),self.assertRaises(ValueError): self.ch.destination(data,self.host)
        self.assertEqual(self.ch.hosts,{})

    def test_wrong_network_and_unknown_station_do_not_leak_to_other_hosts(self):
        self.ch.destination(packet(),self.host)
        for dest in (0x0B05,0x0A06):
            with self.subTest(dest=dest),self.assertRaises(ValueError):
                self.ch.destination(packet(src=0x0C01,dst=dest),self.ch.nodes[3])


if __name__=='__main__': unittest.main()
