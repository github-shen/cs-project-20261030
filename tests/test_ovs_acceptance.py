"""Portable tests for the acceptance harness; these do NOT simulate OVS correctness."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acceptance", ROOT / "scripts/ovs_acceptance.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.h = m.Acceptance(self.output, .02)

    def test_business_counter_excludes_arp_and_default_rule(self):
        dump = ("cookie=0x0, n_packets=999,priority=0 actions=NORMAL\n"
                "cookie=0xaca1, n_packets=3,priority=100,udp actions=NORMAL\n"
                "cookie=0xaca10, n_packets=88,arp actions=NORMAL")
        self.assertEqual(m.packet_count(dump), 3)

    def test_missing_duplicate_and_malformed_counters_fail(self):
        for text in ("", "cookie=0xaca1, actions=NORMAL",
                     "cookie=0xaca1, n_packets=1\ncookie=0xaca1, n_packets=2"):
            with self.subTest(text=text), self.assertRaises(RuntimeError):
                m.packet_count(text)

    def test_config_preserves_topology_and_routes(self):
        for i in range(1, 5):
            original = (ROOT / f"config-ovs/node{i}.conf").read_text(encoding="utf-8")
            copy = m.config_copy(original)
            self.assertEqual(copy.replace("10.203.0.", "10.1.0."), original)
            self.assertFalse(any("10.1.0." in line for line in copy.splitlines()
                                 if line.startswith("port ")))

    def test_receive_requires_source_marker_and_recv_prefix(self):
        good = '[RECV] 来自0x0A01: "unique"\n'
        self.assertTrue(m.received(good, 0x0A01, "unique"))
        for bad in (good.replace("A01", "A02"), good.replace("unique", "older"),
                    good.replace("[RECV]", "[FWD]")):
            self.assertFalse(m.received(bad, 0x0A01, "unique"))

    def test_missing_delivery_times_out_and_records_failure(self):
        self.h.send = Mock()
        (self.output / "h2.log").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.h.check("delivery", lambda: self.h.delivery(1, 2))
        self.assertEqual(self.h.checks[0]["status"], "FAIL")

    def test_counter_not_growing_times_out(self):
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            self.h.until(lambda: m.packet_count("cookie=0xaca1, n_packets=0") > 0,
                         "counter did not grow")

    def test_process_early_exit_is_not_reported_as_timeout(self):
        self.h.children["node"] = Mock(poll=Mock(return_value=7), returncode=7)
        with self.assertRaisesRegex(RuntimeError, "exited unexpectedly: 7"):
            self.h.until(lambda: False, "startup")

    def test_command_failure_is_logged_and_raised(self):
        result = subprocess.CompletedProcess(["fake"], 3, "", "failure evidence")
        with patch.object(m.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "command failed"):
                self.h.run("fake", "argument with space")
        self.assertIn("failure evidence", (self.output / "commands.log").read_text())

    def test_partial_setup_cleanup_only_owned_resources(self):
        self.h.bridge_owned = True
        self.h.namespaces = [self.h.ns(1)]
        self.h.links = [self.h.bridge + "p1"]
        self.h.run = Mock()
        self.h.cleanup()
        commands = [c.args for c in self.h.run.call_args_list]
        self.assertEqual(len(commands), 3)
        self.assertIn(("ip", "netns", "delete", self.h.ns(1)), commands)
        for command in commands:
            self.assertNotIn("br-int", command)
            self.assertNotIn("ns1", command)
            self.assertNotIn("pkill", command)

    def test_cleanup_continues_after_one_resource_failure(self):
        self.h.bridge_owned = True
        self.h.namespaces = [self.h.ns(1)]
        self.h.run = Mock(side_effect=[RuntimeError("bridge busy"), ""])
        self.h.cleanup()
        self.assertEqual(self.h.run.call_count, 2)
        self.assertEqual(self.h.cleanup_errors, ["bridge busy"])

    def test_empty_cleanup_does_not_delete_anything(self):
        self.h.run = Mock()
        self.h.cleanup()
        self.h.run.assert_not_called()

    def test_cleanup_preserves_unrelated_real_process(self):
        command = [sys.executable, "-c", "import time; time.sleep(30)"]
        unrelated = subprocess.Popen(command)
        owned = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            self.h.children["owned"] = owned
            self.h.cleanup()
            self.assertIsNotNone(owned.poll())
            self.assertIsNone(unrelated.poll())
        finally:
            for proc in (owned, unrelated):
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=5)

    def test_main_failure_still_writes_report_and_cleans(self):
        output = self.output / "report"
        with patch.object(m.sys, "platform", "linux"), \
             patch.object(m.os, "geteuid", return_value=0, create=True), \
             patch.object(m.shutil, "which", return_value="/fake"), \
             patch.object(m.os, "access", return_value=True), \
             patch.object(m.Acceptance, "execute", side_effect=RuntimeError("injected")), \
             patch.object(m.Acceptance, "cleanup") as cleanup:
            self.assertEqual(m.main(["--output", str(output)]), 1)
        import json
        report = json.loads((output / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["error"], "injected")
        cleanup.assert_called_once()

    def test_cleanup_failure_prevents_pass(self):
        def bad_cleanup(instance):
            instance.cleanup_errors.append("injected cleanup failure")
        with patch.object(m.sys, "platform", "linux"), \
             patch.object(m.os, "geteuid", return_value=0, create=True), \
             patch.object(m.shutil, "which", return_value="/fake"), \
             patch.object(m.os, "access", return_value=True), \
             patch.object(m.Acceptance, "execute"), \
             patch.object(m.Acceptance, "cleanup", bad_cleanup):
            self.assertEqual(m.main(["--output", str(self.output / "cleanup-report")]), 1)


if __name__ == "__main__":
    unittest.main()
