#!/usr/bin/env python3
"""Isolated OVS integration acceptance. Standard library only; Linux/root required."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def packet_count(dump, cookie="0xaca1"):
    rows = [line for line in dump.splitlines()
            if re.search(r"\bcookie=" + re.escape(cookie) + r"(?:,|\s)", line)]
    if len(rows) != 1:
        raise RuntimeError("expected exactly one business UDP counter rule")
    match = re.search(r"\bn_packets=(\d+)\b", rows[0])
    if not match:
        raise RuntimeError("missing packet counter")
    return int(match[1])


def config_copy(text):
    """Change only the underlay IPs; keep logical ports and routing entries."""
    return re.sub(r"\b10\.1\.0\.(1[1-3]|[1-4])\b", r"10.203.0.\1", text)


def received(text, src, marker):
    src = f'0x{src:04X}' if isinstance(src, int) else src
    return any(line.startswith("[RECV]") and
               f'来自{src}: "{marker}"' in line
               for line in text.splitlines())


class Acceptance:
    def __init__(self, output, timeout=8, hierarchical=False):
        self.output = output
        self.timeout = timeout
        self.token = uuid.uuid4().hex[:8]
        self.bridge = "av" + self.token
        self.namespaces = []
        self.links = []
        self.bridge_owned = False
        self.children = {}
        self.handles = []
        self.checks = []
        self.cleanup_errors = []
        self.serial = 0
        self.hierarchical = hierarchical
        self.addresses = {}

    def address(self, index, internal=False):
        if self.hierarchical:
            return self.addresses[('n' if internal else 'h') + str(index)]
        return f'0x{(0x0100+index) if internal else (((9+index)<<8)|1):04X}'

    def run(self, *args, check=True):
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", timeout=15)
        with (self.output / "commands.log").open("a", encoding="utf-8") as log:
            log.write(json.dumps(list(args)) + "\n" + result.stdout + result.stderr)
        if check and result.returncode:
            raise RuntimeError(f"command failed ({result.returncode}): {args}: {result.stderr}")
        return result.stdout

    def ns(self, i):
        return f"{self.bridge}n{i}"

    def log(self, name):
        return (self.output / f"{name}.log").read_text(encoding="utf-8", errors="replace")

    def alive(self):
        for name, proc in self.children.items():
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited unexpectedly: {proc.returncode}")

    def until(self, condition, message):
        end = time.monotonic() + self.timeout
        while time.monotonic() < end:
            self.alive()
            if condition():
                return
            time.sleep(.05)
        raise RuntimeError("timeout: " + message)

    def start(self, name, ns, *args):
        handle = (self.output / f"{name}.log").open("w", encoding="utf-8")
        self.handles.append(handle)
        proc = subprocess.Popen(["ip", "netns", "exec", self.ns(ns),
                                 "stdbuf", "-oL", "-eL", *map(str, args)],
                                stdin=subprocess.PIPE, stdout=handle, stderr=handle,
                                text=True, encoding="utf-8", bufsize=1)
        self.children[name] = proc
        # The programs print their command menu only after socket/thread setup.
        self.until(lambda: "命令:" in self.log(name) or "[READY] channel=" in self.log(name), f"{name} startup")

    def send(self, name, command):
        self.alive()
        self.children[name].stdin.write(command + "\n")
        self.children[name].stdin.flush()

    def marker(self, label):
        self.serial += 1
        return f"ovs-{self.token}-{self.serial}-{label}"

    def flows(self, label):
        dump = self.run("ovs-ofctl", "dump-flows", self.bridge)
        (self.output / f"flows-{label}.txt").write_text(dump, encoding="utf-8")
        return packet_count(dump)

    def check(self, name, action):
        try:
            detail = action()
        except Exception as exc:
            self.checks.append({"name": name, "status": "FAIL", "detail": str(exc)})
            raise
        self.checks.append({"name": name, "status": "PASS", "detail": detail})
        print("PASS:", name, flush=True)

    def setup(self):
        config = ROOT / 'config-ovs'
        if self.hierarchical:
            from hier_config import convert
            config = self.output / 'hier-source'
            data = convert(ROOT / 'config-ovs', config)
            self.addresses = {'n'+n:ip for n,ip in data['node_addresses'].items()}
            self.addresses.update({'h'+str(i):c['hosts'][0]['address'] for i,c in enumerate(data['channels'],1)})
        # Unique resources; never touch br-int, ovs1..4, ns1..4 or physical NICs.
        self.run("ovs-vsctl", "--timeout=5", "add-br", self.bridge)
        self.bridge_owned = True
        self.run("ovs-vsctl", "set-fail-mode", self.bridge, "secure")
        self.run("ovs-ofctl", "add-flow", self.bridge,
                 "priority=0,actions=NORMAL")
        self.run("ovs-ofctl", "add-flow", self.bridge,
                 "cookie=0xaca1,priority=100,udp,nw_src=10.203.0.0/24,"
                 "nw_dst=10.203.0.0/24,actions=NORMAL")
        for i in (1, 2, 3, 4, 11, 12, 13):
            ns = self.ns(i)
            self.run("ip", "netns", "add", ns)
            self.namespaces.append(ns)
            a, b = f"{self.bridge}p{i}", f"{self.bridge}e{i}"
            self.run("ip", "link", "add", a, "type", "veth", "peer", "name", b)
            self.links.append(a)
            self.run("ip", "link", "set", b, "netns", ns)
            self.run("ovs-vsctl", "add-port", self.bridge, a)
            self.run("ip", "link", "set", a, "up")
            for iface in ("lo", b):
                self.run("ip", "-n", ns, "link", "set", iface, "up")
            self.run("ip", "-n", ns, "addr", "add", f"10.203.0.{i}/24", "dev", b)
            if i <= 4:
                text = (config / f"node{i}.conf").read_text(encoding="utf-8")
                path = self.output / f"node{i}.conf"
                path.write_text(config_copy(text), encoding="utf-8")
        self.run("ovs-ofctl", "show", self.bridge)
        # Wait for all OVS ports to be usable before launching the application.
        for i in (1, 2, 3, 4, 11, 12, 13):
            self.until(lambda i=i: int(self.run("ovs-vsctl", "get", "Interface",
                       f"{self.bridge}p{i}", "ofport").strip()) > 0, "OVS port")
        for i in range(1, 5):
            self.start(f"n{i}", i, ROOT / "bin/switch", self.output / f"node{i}.conf")
        path = self.output / "channels.json"
        path.write_text(config_copy((config / "channels.json").read_text(encoding="utf-8")), encoding="utf-8")
        for i in range(1, 4):
            network = (65535,3,5)[i-1] if self.hierarchical else 9+i
            self.start(f"ch{i}", 10+i, sys.executable, ROOT / "scripts/channel.py",
                       path, "--network", f"0x{network:X}")
            self.start(f"h{i}", 10+i, ROOT / "bin/host", self.address(i),
                       str(9000+i), "127.0.0.1", str(9100+i))

    def delivery(self, source, target, internal=False):
        marker = self.marker("internal" if internal else "external")
        dest = self.address(target, internal)
        src = self.address(source, internal)
        name = f"n{source}" if internal else f"h{source}"
        receiver = f"n{target}" if internal else f"h{target}"
        self.send(name, f"send {dest} {marker}")
        if internal:
            self.until(lambda: any("[RECV] 交付本节点:" in line and
                       f"src={src}" in line and f'内容="{marker}"' in line
                       for line in self.log(receiver).splitlines()), marker)
        else:
            self.until(lambda: received(self.log(receiver), src, marker), marker)
        return marker

    def rejection(self, kind):
        source = "h1"
        command, error = "sendbad", "CRC校验失败"
        observer = "ch1"
        if kind == "source":
            spoof = '2001:db8:500::5' if self.hierarchical else '0x0C05'
            self.start("spoof", 11, ROOT / "bin/host", spoof, "9099",
                       "10.203.0.1", "1103")
            source, command, error = "spoof", "send", "源地址校验失败"
            observer = "n1"
        target = self.address(2)
        if kind == "route":
            command, error, target = "send", "无路由", ('2001:db8:700::1' if self.hierarchical else '0x0E01')
            observer = "n1"
        offset = len(self.log(observer))
        marker = self.marker(kind)
        self.send(source, f"{command} {target} {marker}")
        self.until(lambda: "[DROP]" in self.log(observer)[offset:] and
                   error in self.log(observer)[offset:], error)
        # Bounded observation, with a positive control before and after the probe.
        end = time.monotonic() + .5
        while time.monotonic() < end:
            self.alive()
            if any(marker in self.log(f"h{i}") for i in range(1, 4)):
                raise RuntimeError("rejected frame was delivered: " + marker)
            time.sleep(.05)
        self.delivery(1, 2)
        return {"marker": marker, "drop": error, "negative_window_seconds": .5}

    def load_balance(self):
        if self.hierarchical:
            choices = {}
            for station in range(1, 17):
                name = f'flow{station}'
                src = f'2001:db9::{station:x}'
                self.start(name, 11, ROOT/'bin/host', src, str(9200+station), '127.0.0.1', '9101')
                selected = []
                for _ in range(2):
                    offset = len(self.log('n1')); marker = self.marker('weighted')
                    self.send(name, f'send {self.address(3)} {marker}')
                    self.until(lambda: received(self.log('h3'),src,marker),marker)
                    decisions = re.findall(r'\[SELECT\] Lport=5 EPID=(\d+/\d+)',self.log('n1')[offset:])
                    if len(decisions)!=1: raise RuntimeError('missing EPT selection')
                    selected += decisions
                if len(set(selected))!=1: raise RuntimeError('same address flow changed external exit')
                choices[src]=selected
            if {x[0] for x in choices.values()}!={'3/3','4/5'}: raise RuntimeError('both physical exits were not used')
            return choices
        self.start("h1b", 11, ROOT / "bin/host", "0x0A02", "9011", "127.0.0.1", "9101")
        choices = {}
        for name, src in (("h1", 0x0A01), ("h1b", 0x0A02)):
            ports = []
            for _ in range(4):
                offset = len(self.log("n1"))
                marker = self.marker("balance")
                self.send(name, f"send 0x0C01 {marker}")
                self.until(lambda: received(self.log("h3"), src, marker), marker)
                matches = re.findall(r"出端口(\d+) dst=0x0C01", self.log("n1")[offset:])
                if len(matches) != 1:
                    raise RuntimeError("expected exactly one first-hop decision")
                ports.append(int(matches[0]))
            if len(set(ports)) != 1:
                raise RuntimeError("same address flow changed path")
            choices[name] = ports
        if len({ports[0] for ports in choices.values()}) != 2:
            raise RuntimeError("two address flows did not use both configured next hops")
        return choices

    def execute(self):
        self.check("setup_and_ready", self.setup)
        before = self.flows("before")
        self.check("OVS_business_delivery", lambda: self.delivery(1, 3, True))
        self.until(lambda: self.flows("after") > before, "business UDP counter increment")
        after = self.flows("after")
        self.checks.append({"name": "OVS_business_counter", "status": "PASS",
                            "detail": {"before": before, "after": after}})
        for internal in (False, True):
            for source in range(1, 5 if internal else 4):
                for target in range(1, 5 if internal else 4):
                    if source != target:
                        label = "internal" if internal else "external"
                        self.check(f"{label}_{source}_to_{target}",
                                   lambda s=source, t=target, v=internal: self.delivery(s, t, v))
        for i, pair in enumerate(((1, 3), (2, 4), (3, 4)), 1):
            for n in pair:
                offset = len(self.log(f"ch{i}"))
                self.send(f"ch{i}", f"via {n}")
                self.until(lambda i=i,n=n: f"[GATEWAY] node={n}" in self.log(f"ch{i}")[offset:], "gateway change")
                self.check(f"external_attachment_{i}_via_{n}", lambda i=i: self.delivery(i, i%3+1))
            self.send(f"ch{i}", f"via {pair[0]}")
            self.until(lambda i=i,pair=pair: self.log(f"ch{i}").rstrip().endswith(f"[GATEWAY] node={pair[0]}"), "gateway restore")
        for kind in ("crc", "source", "route"):
            self.check("reject_" + kind, lambda k=kind: self.rejection(k))
        self.check("flow_stability_and_two_paths", self.load_balance)
        for i in range(1, 5):
            self.send(f"n{i}", "table")
            self.send(f"n{i}", "stats")
            self.until(lambda i=i: "内部转发表" in self.log(f"n{i}") and
                       "重路由=" in self.log(f"n{i}"), f"node {i} table/stats")
        self.checks.append({"name": "table_and_stats", "status": "PASS", "detail": "four nodes"})
        self.flows("final")

    def cleanup(self):
        # Popen handles belong to this run. Never discover victims by name/pkill.
        for proc in self.children.values():
            try:
                if proc.poll() is None:
                    proc.terminate()
            except OSError as exc:
                self.cleanup_errors.append(str(exc))
        for proc in self.children.values():
            try:
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
                if proc.stdin:
                    proc.stdin.close()
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.cleanup_errors.append(str(exc))
        for handle in self.handles:
            handle.close()
        actions = []
        if self.bridge_owned:
            actions.append(("ovs-vsctl", "--timeout=5", "--if-exists", "del-br", self.bridge))
        actions += [("ip", "link", "delete", link) for link in reversed(self.links)]
        actions += [("ip", "netns", "delete", ns) for ns in reversed(self.namespaces)]
        for args in actions:
            try:
                self.run(*args)
            except Exception as exc:
                self.cleanup_errors.append(str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new output directory (must not exist)")
    parser.add_argument("--timeout", type=float, default=8, help="per condition, seconds")
    parser.add_argument("--hierarchical", action="store_true", help="验收课件 a/b、全域 EPT、EPID 模式")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        print("Requires Linux and root; run: sudo python3 scripts/ovs_acceptance.py", file=sys.stderr)
        return 2
    for command in ("ip", "ovs-vsctl", "ovs-ofctl", "stdbuf"):
        if not shutil.which(command):
            print("Missing prerequisite: " + command, file=sys.stderr)
            return 2
    for path in ("bin/switch", "bin/host"):
        if not os.access(ROOT / path, os.X_OK):
            print("Run make first: missing executable " + path, file=sys.stderr)
            return 2
    output = (args.output or ROOT / "logs" / ("ovs-acceptance-" + uuid.uuid4().hex[:12])).resolve()
    output.mkdir(parents=True, exist_ok=False)
    test = Acceptance(output, args.timeout, args.hierarchical)
    failure = None

    def interrupted(signum, frame):
        raise RuntimeError(f"interrupted by signal {signum}")

    old_signals = {s: signal.signal(s, interrupted) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        test.execute()
    except Exception as exc:
        failure = str(exc)
    finally:
        # A second signal must not interrupt cleanup and leak namespaces.
        for s in old_signals:
            signal.signal(s, signal.SIG_IGN)
        try:
            test.cleanup()
        except Exception as exc:
            test.cleanup_errors.append(str(exc))
        for s, handler in old_signals.items():
            signal.signal(s, handler)
        passed = failure is None and not test.cleanup_errors
        result = {"status": "PASS" if passed else "FAIL", "error": failure,
                  "checks": test.checks, "cleanup_errors": test.cleanup_errors,
                  "bridge": test.bridge, "physical_media_verified": False,
                  "mode": "hierarchical" if args.hierarchical else "legacy"}
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(result["status"], output, failure or "", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
