"""lxdtop --dump --json: one reading as one JSON document, read back with json.loads.

The fake host has three containers (web, db, and api in the "dev" project), a VM
(win) and a stopped container (old), as LXD's fixture answers describe them. Between
its two readings, web uses a quarter of a core, is throttled in 4 of its CPU limit's
10 periods, and receives 12,500 bytes a second; the host port eno1 takes in
125,000 bytes a second.
"""

import contextlib
import datetime
import io
import json
import re
import sys
import time
import unittest
from unittest import mock

from tests.support import MiB, FakeHost, FakeLxd, lifecycle_events, lxd_routes, lxdtop

INSTANCES = "/1.0/instances?recursion=2&all-projects=true"
UNITS = ("_bytes", "_pct", "_s", "_bits_per_s", "_bytes_per_s", "_c", "_mhz", "_cores")


class JsonDumpTest(unittest.TestCase):
    def setUp(self):
        saved = lxdtop.G
        self.addCleanup(setattr, lxdtop, "G", saved)
        self.host = host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        host.copy_fixture("zfs-arcstats.txt", lxdtop.ZFS_ARCSTATS)
        host.write("/proc/spl/kstat/zfs/default/state", "ONLINE\n")
        host.add_nic("eno1", 1_000_000, 2_000_000, physical=True)
        host.add_nic("veth1a2b3c4d", 0, 0)
        host.add_nic("tap0f1e2d3c", 0, 0)
        host.add_container("web", 10_000_000, memory=512 * MiB)
        web = "/sys/fs/cgroup/lxc.payload.web"
        host.write(web + "/cpu.stat", "usage_usec 10000000\nnr_periods 10\nnr_throttled 1\n")
        host.write(web + "/cpu.max", "200000 100000\n")
        host.write(web + "/memory.max", f"{2048 * MiB}\n")
        host.write(web + "/pids.max", "500\n")
        host.write(web + "/memory.events", "low 0\nhigh 0\nmax 0\noom 0\noom_kill 2\n")
        host.write(web + "/memory.pressure", "some avg10=3.10 avg60=1.00 avg300=0.50 total=1\n")
        host.add_process(4242, "nginx", cgroup="/lxc.payload.web/system.slice/nginx.service", start=500)
        host.add_container("db", 20_000_000, memory=1024 * MiB)
        host.add_container("dev_api", 1_000, memory=64 * MiB, inactive_file=16 * MiB)
        host.copy_fixture("pid-stat-qemu.txt", "/proc/5151/stat")
        host.copy_fixture("pid-status-qemu.txt", "/proc/5151/status")
        self.routes = lxd_routes()
        self.events = []                       # what LXD announces while lxdtop reads (see main())
        self.between = []                      # more changes to make before the second reading

    def sleep(self, secs):
        """Between readings: time passes, and the host's counters move on."""
        host = self.host
        host.clock += secs
        t = host.clock - 1000.0                                   # seconds since the first reading
        host.set_cpu_times((100 + int(t * 25), 0, 900 + int(t * 225)), [(25, 0, 225)] * 4)    # 10% busy
        host.write("/sys/fs/cgroup/lxc.payload.web/cpu.stat",
                   f"usage_usec {10_000_000 + int(t * 250_000)}\nnr_periods {10 + int(t * 5)}\n"
                   f"nr_throttled {1 + int(t * 2)}\n")
        host.write("/sys/fs/cgroup/lxc.payload.db/cpu.stat", f"usage_usec {20_000_000 + int(t * 50_000)}\n")
        host.add_nic("eno1", 1_000_000 + int(t * 125_000), 2_000_000 + int(t * 25_000), physical=True)
        host.add_nic("veth1a2b3c4d", int(t * 2_500), int(t * 12_500))   # host side: in and out swap
        for change in self.between:
            change()

    def main(self, *args, api=True):
        out = io.StringIO()
        events = self.events

        def listen(feed):                      # instead of the websocket: these events arrive
            for event in events:
                feed._record(event)
            feed.status = "live"

        with contextlib.ExitStack() as stack:
            if api:
                lxd = stack.enter_context(FakeLxd(self.routes))
                args += ("--socket", lxd.path)
            stack.enter_context(self.host.installed())
            stack.enter_context(mock.patch.object(lxdtop.LxdApi, "start"))
            stack.enter_context(mock.patch.object(lxdtop.LxdEvents, "start", listen))
            stack.enter_context(mock.patch.object(sys, "argv", ["lxdtop", *args]))
            stack.enter_context(mock.patch.object(lxdtop.time, "sleep", self.sleep))
            stack.enter_context(mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"))
            stack.enter_context(contextlib.redirect_stdout(out))
            lxdtop.main()
        return out.getvalue()

    def reading(self, *args, api=True):
        text = self.main("--dump", "--json", *args, *(() if api else ("--no-api",)), api=api)
        return json.loads(text)

    def instance(self, doc, name):
        return next(inst for inst in doc["instances"] if inst["name"] == name)

    def test_one_document_with_its_version_schema_and_time(self):
        text = self.main("--dump", "--json")
        doc = json.loads(text)                          # one document: json.loads takes all of it
        self.assertEqual(doc["schema"], 1)
        self.assertEqual(doc["lxdtop_version"], lxdtop.VERSION)
        self.assertRegex(doc["taken_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        taken = datetime.datetime.strptime(doc["taken_at"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertLess(abs(taken.replace(tzinfo=datetime.timezone.utc).timestamp() - time.time()), 60)
        self.assertEqual(doc["interval_s"], 2)
        self.assertEqual(doc["lxd_api"], "ok")
        self.assertEqual(list(doc), ["schema", "lxdtop_version", "taken_at", "interval_s", "lxd_api", "host",
                                     "network", "pools", "warnings", "events", "instance_counts", "instances"])

    def test_the_host(self):
        host = self.reading()["host"]
        self.assertEqual({k: host[k] for k in ("name", "lxd_version", "os", "kernel", "cluster_member")},
                         dict(name="lxdhost", lxd_version="6.9", os="Ubuntu 24.04", kernel="6.8.0-45-generic",
                              cluster_member=None))
        self.assertEqual(host["uptime_s"], 123456.8)
        cpu = host["cpu"]
        self.assertEqual(cpu["busy_pct"], 10.0)                    # 50 of 500 ticks busy across the host
        self.assertEqual(cpu["threads_busy_pct"], [0.0] * 4)
        self.assertEqual(cpu["outside_instances_pct"], 10.0)      # 40% of one core in all, 30 of it web and db
        self.assertEqual((cpu["load_1m"], cpu["load_5m"], cpu["load_15m"]), (0.52, 0.48, 0.4))
        self.assertEqual((cpu["temp_c"], cpu["clock_mhz"]), (None, None))       # the fake host has no sensors
        memory = host["memory"]
        self.assertEqual(memory["zfs_arc_bytes"], 8589934592)                   # split out of "used"
        self.assertEqual(memory["used_bytes"],
                         memory["total_bytes"] - memory["available_bytes"] - memory["zfs_arc_bytes"])
        self.assertEqual(host["root_fs"], dict(used_bytes=40 * 1024 ** 3, total_bytes=100 * 1024 ** 3))

    def test_cluster_member(self):
        self.routes["/1.0"][1]["metadata"]["environment"].update(server_clustered=True, server_name="m1")
        for inst in self.routes[INSTANCES][1]["metadata"]:
            inst["location"] = "m1"
        self.assertEqual(self.reading()["host"]["cluster_member"], "m1")

    def test_network_ports_in_bits_per_second(self):
        network = self.reading()["network"]
        self.assertEqual(network["ports"], [dict(name="eno1", in_bits_per_s=1_000_000.0, out_bits_per_s=200_000.0)])
        self.assertEqual((network["in_bits_per_s"], network["out_bits_per_s"]), (1_000_000.0, 200_000.0))
        self.assertEqual(network["peak_bits_per_s"], 1_000_000.0)

    def test_pools_and_their_health(self):
        self.host.write("/proc/spl/kstat/zfs/tank/state", "DEGRADED\n")      # a ZFS pool LXD doesn't list
        doc = self.reading()
        self.assertEqual(doc["pools"], [
            dict(name="default", used_bytes=128849018880, total_bytes=966367641600, health="ONLINE"),
            dict(name="tank", used_bytes=None, total_bytes=None, health="DEGRADED")])
        self.assertEqual(doc["warnings"][0], dict(severity=3, kind="pool", instance=None, pool="tank",
                                                  text="tank DEGRADED"))

    def test_warnings_most_serious_first(self):
        self.between.append(lambda: self.host.write("/sys/fs/cgroup/lxc.payload.web/memory.events", "oom_kill 3\n"))
        warnings = self.reading()["warnings"]
        self.assertEqual([(w["severity"], w["kind"], w["instance"], w["text"]) for w in warnings], [
            (4, "memory_kill", "web", "web: memory kill 0s ago"),
            (1, "throttled", "web", "web: throttled 40% @2c")])

    def test_the_event_feed(self):
        self.events = lifecycle_events()
        events = self.reading()["events"]
        self.assertEqual(events["status"], "live")
        feed = events["feed"]
        self.assertEqual([(e["action"], e["instance"], e["detail"], e["user"], e["count"]) for e in feed][-3:], [
            ("instance-exec", "db", None, "backup", 1),            # its command line stays hidden
            ("instance-snapshot-created", "web", "before-upgrade", "bj", 1),
            ("instance-started", "web", None, "bj", 1)])
        for event in feed:
            self.assertRegex(event["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_exec_command_lines_only_with_show_commands(self):
        self.events = lifecycle_events()
        self.assertNotIn("S3cret", self.main("--dump", "--json"))
        doc = self.reading("--show-commands")
        execs = [e for e in doc["events"]["feed"] if e["action"] == "instance-exec"]
        self.assertEqual(execs[0]["detail"], "mysql -uroot -pS3cret! -e SHOW DATABASES")

    def test_nothing_from_instance_config(self):
        web = next(i for i in self.routes[INSTANCES][1]["metadata"] if i["name"] == "web")
        web["config"] = {"environment.DB_PASSWORD": "hunter2", "user.note": "rack 7"}
        web["expanded_config"] = dict(web["config"], **{"limits.memory": "2GiB"})
        web["devices"] = {"data": {"type": "disk", "source": "/srv/private", "path": "/data"}}
        text = self.main("--dump", "--json")
        for value in ("hunter2", "rack 7", "/srv/private", "DB_PASSWORD", "limits.memory"):
            self.assertNotIn(value, text)

    def test_nothing_from_a_vms_config_but_its_limits(self):
        win = next(i for i in self.routes[INSTANCES][1]["metadata"] if i["name"] == "win")
        win["expanded_config"].update({"cloud-init.user-data": "#cloud-config\npassword: hunter2",
                                       "environment.API_TOKEN": "s3cret", "user.note": "rack 7"})
        for args in (("--dump", "--json"), ("--dump", "--size", "240x60"), ("--dump", "--size", "80x24")):
            text = self.main(*args)
            for value in ("hunter2", "s3cret", "rack 7", "API_TOKEN", "cloud-init", "limits.", "Windows",
                          "5f0e3a52"):
                self.assertNotIn(value, text, args)
        win = self.instance(self.reading(), "win")
        self.assertEqual((win["cpu_limit_cores"], win["mem_limit_bytes"]), (2, 4 * 1024 ** 3))

    def test_instances_in_the_tables_order(self):
        doc = self.reading()
        self.assertEqual([(i["name"], i["type"], i["state"]) for i in doc["instances"]], [
            ("web", "container", "Running"), ("db", "container", "Running"), ("api", "container", "Running"),
            ("win", "virtual-machine", "Running"), ("old", "container", "Stopped")])
        self.assertEqual(doc["instance_counts"], dict(running=4, stopped=1))

    def test_a_container(self):
        web = self.instance(self.reading(), "web")
        self.assertEqual(web, {
            "name": "web", "type": "container", "state": "Running", "ipv4": "10.10.10.21",
            "uptime_s": 123451.8, "cpu_pct": 25.0, "cpu_history_pct": [None, 25.0],
            "busiest_process": {"name": "nginx", "cpu_pct": 0.0}, "mem_bytes": 384 * MiB,
            "disk_read_bytes_per_s": 0.0, "disk_write_bytes_per_s": 0.0, "disk_source": "io.stat",
            "net_in_bits_per_s": 100_000.0, "net_out_bits_per_s": 20_000.0,
            "tasks": 10, "tasks_limit": 500, "tasks_of_limit_pct": 2.0,
            "cpu_limit_cores": 2.0, "cpu_throttled_pct": 40.0,
            "mem_limit_bytes": 2048 * MiB, "mem_of_limit_pct": 18.75,
            "waiting_on": "mem", "waiting_pct": 3.1,
            "mem_kills": 0, "mem_kills_total": 2, "last_mem_kill_ago_s": None,
            "newest_snapshot_at": "2026-11-04T07:00:00Z",
            "newest_snapshot_age_s": web["newest_snapshot_age_s"]})
        taken = datetime.datetime(2026, 11, 4, 7, tzinfo=datetime.timezone.utc).timestamp()
        self.assertAlmostEqual(web["newest_snapshot_age_s"], time.time() - taken, delta=60)

    def test_a_vm(self):
        win = self.instance(self.reading(), "win")
        self.assertEqual((win["type"], win["mem_bytes"], win["ipv4"]),
                         ("virtual-machine", 2 * 1024 ** 3, "10.10.10.30"))       # memory: QEMU's VmRSS
        self.assertIsNotNone(win["uptime_s"])
        self.assertEqual(win["cpu_history_pct"], [None, 0.0])
        # Its limits are LXD's limits.cpu and limits.memory: 2 and 4GiB in the fixture.
        self.assertEqual((win["cpu_limit_cores"], win["mem_limit_bytes"], win["mem_of_limit_pct"]),
                         (2, 4 * 1024 ** 3, 50.0))
        for key in ("busiest_process", "tasks", "tasks_limit", "tasks_of_limit_pct", "cpu_throttled_pct",
                    "waiting_on", "mem_kills", "mem_kills_total", "disk_read_bytes_per_s"):
            self.assertIsNone(win[key], key)                      # in the guest, or a cgroup it doesn't have

    def test_a_vm_without_its_limits_set_has_lxds_defaults(self):
        win = next(i for i in self.routes[INSTANCES][1]["metadata"] if i["name"] == "win")
        del win["expanded_config"]["limits.cpu"], win["expanded_config"]["limits.memory"]
        win = self.instance(self.reading(), "win")
        self.assertEqual((win["cpu_limit_cores"], win["mem_limit_bytes"], win["mem_of_limit_pct"]),
                         (1, 1024 ** 3, 200.0))                   # one CPU and 1 GiB, as LXD gives it

    def test_a_stopped_vm_has_no_limits(self):
        win = next(i for i in self.routes[INSTANCES][1]["metadata"] if i["name"] == "win")
        win["status"], win["state"]["pid"] = "Stopped", 0
        win = self.instance(self.reading(), "win")
        self.assertEqual((win["state"], win["cpu_limit_cores"], win["mem_limit_bytes"]), ("Stopped", None, None))

    def test_a_stopped_instance_has_nulls_not_zeros(self):
        old = self.instance(self.reading(), "old")
        self.assertEqual((old["type"], old["state"], old["cpu_history_pct"]), ("container", "Stopped", []))
        readings = {key: value for key, value in old.items() if key not in ("name", "type", "state", "cpu_history_pct")}
        self.assertEqual(set(readings.values()), {None}, readings)

    def test_what_it_could_not_read_is_null(self):
        self.host.remove(lxdtop.ZFS_ARCSTATS)                   # no ZFS
        self.host.remove("/sys/fs/cgroup/lxc.payload.db/io.stat")
        doc = self.reading()
        self.assertIsNone(doc["host"]["memory"]["zfs_arc_bytes"])
        db = self.instance(doc, "db")
        self.assertEqual((db["disk_read_bytes_per_s"], db["disk_source"]), (None, None))
        self.assertEqual((db["net_in_bits_per_s"], db["net_out_bits_per_s"]), (None, None))   # no host-side NIC

    def test_a_file_it_could_not_read_is_null_not_zero(self):
        self.host.remove("/proc/loadavg")
        self.host.write("/proc/meminfo", "MemTotal:       65536000 kB\nMemAvailable:   32768000 kB\n")   # no swap lines
        self.host.remove("/sys/fs/cgroup/lxc.payload.web/memory.current")
        self.host.remove("/proc/5151/status")                     # the VM's QEMU process: its VmRSS
        doc = self.reading()
        cpu, memory = doc["host"]["cpu"], doc["host"]["memory"]
        self.assertEqual((cpu["load_1m"], cpu["load_5m"], cpu["load_15m"]), (None, None, None))
        self.assertEqual((memory["swap_total_bytes"], memory["swap_used_bytes"]), (None, None))
        self.assertEqual((memory["total_bytes"], memory["available_bytes"]), (65536000 * 1024, 32768000 * 1024))
        web, win = self.instance(doc, "web"), self.instance(doc, "win")
        self.assertEqual((web["mem_bytes"], web["mem_of_limit_pct"]), (None, None))
        self.assertEqual(web["tasks"], 10)                        # the rest of web is read as before
        self.assertIsNone(win["mem_bytes"])
        self.assertEqual(win["cpu_history_pct"], [None, 0.0])

    def test_without_proc_meminfo_every_memory_field_is_null(self):
        self.host.remove("/proc/meminfo")
        memory = self.reading()["host"]["memory"]
        self.assertEqual(memory, dict(total_bytes=None, used_bytes=None, zfs_arc_bytes=8589934592, available_bytes=None,
                                      swap_total_bytes=None, swap_used_bytes=None))

    def test_no_api(self):
        doc = self.reading(api=False)
        self.assertEqual(doc["lxd_api"], "off")
        self.assertEqual(doc["events"], dict(status="off", feed=[]))
        self.assertEqual((doc["host"]["lxd_version"], doc["host"]["os"], doc["host"]["cluster_member"]),
                         (None, None, None))
        self.assertEqual(doc["pools"], [dict(name="default", used_bytes=None, total_bytes=None, health="ONLINE")])
        self.assertEqual([i["name"] for i in doc["instances"]], ["web", "db", "dev_api"])     # cgroups only
        for inst in doc["instances"]:
            self.assertEqual((inst["type"], inst["ipv4"], inst["net_in_bits_per_s"], inst["newest_snapshot_at"]),
                             ("container", None, None, None), inst["name"])
        self.assertEqual(doc["instance_counts"], dict(running=3, stopped=0))

    def test_keys_are_snake_case_and_numbers_stay_numbers(self):
        self.events = lifecycle_events()
        doc = self.reading()

        def walk(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    self.assertRegex(key, r"^[a-z][a-z0-9_]*$", path)
                    if key.endswith(UNITS) and not isinstance(item, list):
                        self.assertTrue(item is None or type(item) in (int, float), f"{path}.{key}: {item!r}")
                    walk(item, f"{path}.{key}")
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    walk(item, f"{path}[{i}]")
        walk(doc, "$")
        self.assertIsInstance(doc["host"]["memory"]["total_bytes"], int)
        self.assertIsInstance(self.instance(doc, "web")["tasks"], int)

    def test_samples_and_interval_work_as_with_the_text_dump(self):
        start = self.host.clock
        doc = self.reading("--samples", "3", "--interval", "5")
        self.assertEqual(self.host.clock - start, 10)                # two waits between three readings
        self.assertEqual(doc["interval_s"], 5)
        self.assertEqual(self.instance(doc, "web")["cpu_history_pct"], [None, 25.0, 25.0])

    def test_json_without_dump_is_a_usage_error(self):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["lxdtop", "--json"]), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as stop:
                lxdtop.main()
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("--json works only with --dump", err.getvalue())


class UntrustedTextTest(unittest.TestCase):
    """Names and process names come from inside containers. json.dumps escapes every control
    character (and, with ensure_ascii, everything outside ASCII), so none reaches a terminal."""

    def test_escape_sequences_in_an_instance_name_and_a_process_name(self):
        name, process = "evil\x1b[2J\x1b]0;owned\x07", "\x1b[31mred\x9b2J"
        host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        host.add_container(name, 1_000_000)
        host.add_process(77, process, cgroup=f"/lxc.payload.{name}/init.scope")
        out = io.StringIO()
        with host.installed(), mock.patch.object(sys, "argv", ["lxdtop", "--dump", "--json", "--no-api"]), \
                mock.patch.object(lxdtop.time, "sleep", lambda s: setattr(host, "clock", host.clock + s)), \
                contextlib.redirect_stdout(out):
            lxdtop.main()
        text = out.getvalue()
        self.assertTrue(text.isascii())
        self.assertFalse(re.search(r"[\x00-\x09\x0b-\x1f\x7f]", text))    # nothing but the newlines of the layout
        (inst,) = json.loads(text)["instances"]
        self.assertEqual(inst["name"], name)                               # and json.loads gives them back
        self.assertEqual(inst["busiest_process"]["name"], process)


if __name__ == "__main__":
    unittest.main()
