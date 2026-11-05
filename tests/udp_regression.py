"""Linux UDP regression: run make, then python3 tests/udp_regression.py."""
from binascii import crc_hqx
from contextlib import ExitStack
from pathlib import Path
import os
import socket
import struct
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def frame(dst, body, ttl=8):
    header = struct.pack("=HHBB", dst, 0x0A01, ttl, len(body))
    crc = crc_hqx(header + b"\0\0" + body, 0xFFFF)
    return header + struct.pack("=H", crc) + body


class UDPRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sock.close)
        self.sock.bind(("127.0.0.1", 0))
        # 配置只接受非零端口；同时预留以取得三个不同的可用端口。
        with ExitStack() as stack:
            self.ports = []
            for _ in range(3):
                probe = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                probe.bind(("127.0.0.1", 0))
                self.ports.append(probe.getsockname()[1])
        self.port = self.ports[0]

    def start(self, name, *args):
        self.log = self.path / "output.log"
        with self.log.open("wb") as output:
            self.proc = subprocess.Popen(
                ["stdbuf", "-oL", str(ROOT / "bin" / name), *map(str, args)],
                stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
            )
        self.addCleanup(self.stop)
        self.wait("命令:")

    def stop(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        finally:
            self.proc.stdin.close()

    def wait(self, marker, offset=0):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            text = self.log.read_text(encoding="utf-8")
            if marker in text[offset:]:
                return text[offset:]
            if self.proc.poll() is not None:
                break
            time.sleep(0.02)
        self.fail(f"Missing {marker!r}: {self.log.read_text(encoding='utf-8')}")

    def send(self, data):
        self.sock.sendto(data, ("127.0.0.1", self.port))

    def start_switch(self):
        config = self.path / "node.conf"
        config.write_text(
            f"id 1\nport 1 ext 0x0A {self.port} 127.0.0.1 {self.sock.getsockname()[1]}\n"
            f"port 2 int {self.ports[1]} 127.0.0.1 {self.sock.getsockname()[1]}\n"
            f"port 3 int {self.ports[2]} 127.0.0.1 {self.sock.getsockname()[1]}\n"
            "ift 0102 2,3\n", encoding="ascii",
        )
        self.start("switch", config)

    def check_lengths(self, dst):
        for label, mutate in (
            (b"short", lambda b: b[:-1]),
            (b"tail", lambda b: b + b"x"),
            (b"oversize", lambda b: b + b"x" * 500),
        ):
            offset = len(self.log.read_text(encoding="utf-8"))
            self.send(mutate(frame(dst, label)))
            self.assertNotIn("[RECV]", self.wait("帧长度非法或截断", offset))
        self.send(frame(dst, b"valid-marker"))
        text = self.wait('"valid-marker"')
        self.assertIn("[RECV]", text)
        for label in ('"short"', '"tail"', '"oversize"'):
            self.assertNotIn(label, text)

    def test_host_lengths(self):
        self.start("host", "0x0B01", self.port, "127.0.0.1", self.sock.getsockname()[1])
        self.check_lengths(0x0B01)

    def test_switch_lengths(self):
        self.start_switch()
        self.check_lengths(0x0101)


if __name__ == "__main__":
    if os.name != "posix":
        raise SystemExit("Run this regression on Linux after make.")
    unittest.main()
