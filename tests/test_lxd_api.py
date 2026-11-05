"""LXD's REST API over its unix socket, answered by a fake LXD (tests.support.FakeLxd)."""

import datetime
import os
import unittest
from unittest import mock

from tests.support import GiB, MiB, FakeHost, FakeLxd, lxd_routes, lxdtop

INSTANCES = "/1.0/instances?recursion=2&all-projects=true"
NIGHTLY = datetime.datetime(2026, 11, 4, 7, 0, tzinfo=datetime.timezone.utc).timestamp()
EVERY_PATH = {INSTANCES, "/1.0", "/1.0/storage-pools?recursion=1", "/1.0/storage-pools/default/resources",
              "/1.0/storage-pools/scratch/resources"}


class FetchTest(unittest.TestCase):
    def test_instances_from_every_project(self):
        with FakeLxd(lxd_routes()) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        found, status, server, pools = api.snapshot()
        self.assertEqual(status, "ok")
        self.assertEqual(found, {
            # The first global IPv4 address; link-local and loopback addresses are skipped.
            # recursion=2 includes snapshots: web's newest is "nightly", 03:00 at UTC-4, so 07:00 UTC.
            "web": dict(name="web", project="default", status="Running", veths=["veth1a2b3c4d"], ipv4="10.10.10.21",
                        type="container", pid=4242, snapshot=NIGHTLY, cpu_max=None, mem_max=None),
            "db": dict(name="db", project="default", status="Running", veths=["veth22220000", "veth22221111"],
                       ipv4="192.168.50.12", type="container", pid=4300, snapshot=None, cpu_max=None, mem_max=None),
            # Outside the default project, the cgroup is named "<project>_<name>".
            "dev_api": dict(name="api", project="dev", status="Running", veths=[], ipv4="", type="container", pid=4400,
                            snapshot=None, cpu_max=None, mem_max=None),
            # A VM's limits come from its config: limits.cpu and limits.memory (a container's are in its cgroup).
            "win": dict(name="win", project="default", status="Running", veths=["tap0f1e2d3c"], ipv4="10.10.10.30",
                        type="virtual-machine", pid=5151, snapshot=None, cpu_max=2, mem_max=4 * GiB),
            "old": dict(name="old", project="default", status="Stopped", veths=[], ipv4="", type="container", pid=0,
                        snapshot=None, cpu_max=None, mem_max=None),
        })
        self.assertEqual(server, dict(version="6.9", kernel="6.8.0-45-generic", os="Ubuntu 24.04",
                                      member="",                        # not a cluster member
                                      zfs_roots=["default"],           # where the ZFS pool keeps its datasets
                                      zpools={"default": "default"}))  # and the zpool that is in
        self.assertEqual(pools, [("default", 128849018880, 966367641600)])   # "scratch" reports no size

    def test_only_get_requests_to_these_paths(self):
        with FakeLxd(lxd_routes()) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            for _ in range(5):
                api.fetch()
        self.assertEqual({method for method, _, _ in lxd.requests}, {"GET"})
        self.assertEqual({target for _, target, _ in lxd.requests}, EVERY_PATH)

    def test_versions_and_pools_once_a_minute(self):
        # Instances every 15 s; versions and pools every fourth time (once a minute).
        with FakeLxd(lxd_routes()) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            counts = []
            for _ in range(5):
                api.fetch()
                counts.append(sum(1 for _, target, _ in lxd.requests if target == "/1.0"))
        self.assertEqual(counts, [1, 1, 1, 1, 2])

    def test_on_a_cluster_only_this_members_instances(self):
        """SECURITY-REVIEW.md N-10: on a cluster, every member's instances used to be listed.

        LXD 5.21 and 6 list the instances of every cluster member, each with its
        member as "location". Those on other members have no cgroup or QEMU
        process here, so they showed as stopped rows and in the "stopped" count.
        """
        routes = lxd_routes()
        routes["/1.0"][1]["metadata"]["environment"].update(server_clustered=True, server_name="m1")
        for inst, member in zip(routes[INSTANCES][1]["metadata"], ("m1", "m2", "m1", "m2", "m2")):
            inst["location"] = member                # web, db, api (project dev), win, old
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        found, status, server, _ = api.snapshot()
        self.assertEqual(status, "ok")
        self.assertEqual(sorted(found), ["dev_api", "web"])
        self.assertEqual(server["member"], "m1")

    def test_standalone_lists_every_instance(self):
        # Not clustered: LXD calls the one member "none", and every instance is on it.
        routes = lxd_routes()
        routes["/1.0"][1]["metadata"]["environment"]["server_name"] = "web"   # a hostname, not a member name
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        self.assertEqual(sorted(api.snapshot()[0]), ["db", "dev_api", "old", "web", "win"])

    def test_where_each_zfs_pool_keeps_its_datasets(self):
        routes = lxd_routes()
        routes["/1.0/storage-pools?recursion=1"] = (200, {"metadata": [
            {"name": "default", "driver": "zfs", "config": {"source": "tank/lxd", "zfs.pool_name": "tank/lxd"}},
            {"name": "fast", "driver": "zfs", "config": {}},            # LXD's default: the pool's own name
            {"name": "scratch", "driver": "dir", "config": {"source": "/var/lib/scratch"}}]})
        routes["/1.0/storage-pools/fast/resources"] = routes["/1.0/storage-pools/default/resources"]
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        self.assertEqual(api.snapshot()[2]["zfs_roots"], ["tank/lxd", "fast"])

    def test_pool_names_are_quoted_in_the_path(self):
        routes = lxd_routes()
        routes["/1.0/storage-pools?recursion=1"] = (200, {"metadata": [{"name": "my pool?#"}]})
        routes["/1.0/storage-pools/my%20pool%3F%23/resources"] = (200, {"metadata": {"space": {"used": 1,
                                                                                            "total": 2}}})
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        self.assertEqual(api.snapshot()[3], [("my pool?#", 1, 2)])


class FailureTest(unittest.TestCase):
    def fetch(self, routes):
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        return api

    def test_an_error_reply_keeps_the_last_good_answer(self):
        with FakeLxd(lxd_routes()) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            lxd.routes[INSTANCES] = (403, {"type": "error", "error": "not authorized", "error_code": 403})
            api.fetch()
        found, status, _, _ = api.snapshot()
        self.assertEqual(status, "error (ValueError)")
        self.assertIn("web", found)

    def test_a_reply_that_is_not_json(self):
        api = self.fetch(lxd_routes(**{INSTANCES: (200, b"<html>bad gateway</html>")}))
        self.assertEqual(api.snapshot()[1], "error (JSONDecodeError)")

    def test_a_reply_in_an_unexpected_shape(self):
        """SECURITY-REVIEW.md L-9: JSON in a shape LXD never sends used to escape fetch().

        fetch() caught only OSError, ValueError, KeyError and HTTPException,
        so an AttributeError or TypeError got out: at startup it ended
        lxdtop, and in the background thread it ended the refreshes for good.
        """
        cases = {"metadata a string": ({"metadata": "x"}, "AttributeError"),
                 "an instance that is a string": ({"metadata": ["x"]}, "AttributeError"),
                 "state a string": ({"metadata": [{"name": "web", "state": "Running"}]}, "AttributeError"),
                 "network a list": ({"metadata": [{"name": "web", "state": {"network": ["eth0"]}}]},
                                    "AttributeError"),
                 "a name that is a list": ({"metadata": [{"name": ["web"]}]}, "TypeError")}
        for label, (body, error) in cases.items():
            with self.subTest(label), FakeLxd(lxd_routes()) as lxd:
                api = lxdtop.LxdApi(lxd.path)
                api.fetch()                                     # a good answer first
                lxd.routes[INSTANCES] = (200, body)
                api.fetch()
                found, status, _, _ = api.snapshot()
                self.assertEqual(status, f"error ({error})")
                self.assertIn("web", found)                     # the last good answer is kept

    def test_a_pool_lxd_cannot_read_is_left_out(self):
        """SECURITY-REVIEW.md N-9: one unreadable storage pool used to stop every refresh.

        LXD 5.21 and 6 answer a pool's /resources with an error when they can't
        read it, for example a ZFS pool that wasn't imported. fetch() threw the
        whole answer away, every time, so names, addresses, VMs and stopped
        instances froze, or never showed if lxdtop started that way.
        """
        broken = (500, {"type": "error", "error_code": 500,
                        "error": "Failed to run: zfs get -H -p -o value available default: exit status 1 "
                                 "(cannot open 'default': no such pool)"})
        api = self.fetch(lxd_routes(**{"/1.0/storage-pools/default/resources": broken}))
        found, status, server, pools = api.snapshot()
        self.assertEqual(status, "ok")
        self.assertIn("web", found)
        self.assertEqual(pools, [])                     # "default" is left out; "scratch" reports no size
        self.assertEqual(server["version"], "6.9")
        self.assertEqual(server["zfs_roots"], ["default"])   # its datasets still count for the DISK columns

    def test_an_instance_without_a_name(self):
        api = self.fetch(lxd_routes(**{INSTANCES: (200, {"metadata": [{"status": "Running"}]})}))
        self.assertEqual(api.snapshot()[1], "error (KeyError)")

    def test_no_socket(self):
        api = lxdtop.LxdApi("/nonexistent/lxd/unix.socket")
        api.fetch()
        self.assertEqual(api.snapshot()[1], "error (FileNotFoundError)")

    def test_no_access_to_the_socket(self):
        api = lxdtop.LxdApi("/var/snap/lxd/common/lxd/unix.socket")
        with mock.patch.object(lxdtop.UnixHTTPConnection, "connect", side_effect=PermissionError(13, "denied")):
            api.fetch()
        self.assertEqual(api.snapshot()[1], "no access")

    def test_snapshot_is_a_copy(self):
        api = self.fetch(lxd_routes())
        found, _, _, pools = api.snapshot()
        found.clear()
        pools.clear()
        self.assertTrue(api.snapshot()[0])
        self.assertTrue(api.snapshot()[3])


class WatchedConfig(dict):
    """An instance's config that keeps a list of the keys asked for, and fails if anything walks through it."""

    def __init__(self, *args):
        super().__init__(*args)
        self.asked = []

    def get(self, key, default=None):
        self.asked.append(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.asked.append(key)
        return super().__getitem__(key)

    def walked(self, *args):
        raise AssertionError("something went through the whole config")

    __iter__ = __contains__ = keys = values = items = copy = __repr__ = __str__ = walked


class VmLimitsTest(unittest.TestCase):
    """A VM's CPU and memory limits, from limits.cpu and limits.memory, worked out as LXD 5.21 and 6 do."""

    def test_cpus_a_number_or_the_ones_it_is_pinned_to(self):
        for value, cores in (("2", 2), ("16", 16), ("0-3", 4), ("1,3", 2), ("0-3,8,10-11", 7), ("5", 5), ("7-7", 1)):
            with self.subTest(value):
                self.assertEqual(lxdtop.vm_limits({"limits.cpu": value})[0], cores)

    def test_memory_in_lxds_units(self):
        for value, size in (("4GiB", 4 * GiB), ("512MiB", 512 * MiB), ("8KiB", 8192), ("1TiB", 1024 * GiB),
                            ("2GB", 2 * 10 ** 9), ("1500MB", 1500 * 10 ** 6), ("64kB", 64000), ("100B", 100),
                            ("1073741824", GiB), ("4096 bytes", 4096)):
            with self.subTest(value):
                self.assertEqual(lxdtop.vm_limits({"limits.memory": value})[1], size)

    def test_memory_as_a_share_of_the_hosts(self):
        host = FakeHost(self)
        host.copy_fixture("proc-meminfo.txt", "/proc/meminfo")            # MemTotal: 65536000 kB
        with host.installed():
            self.assertEqual(lxdtop.vm_limits({"limits.memory": "50%"})[1], 65536000 * 1024 // 100 * 50)
            self.assertIsNone(lxdtop.vm_limits({"limits.memory": "half%"})[1])
        with FakeHost(self).installed():                                   # no /proc/meminfo
            self.assertIsNone(lxdtop.vm_limits({"limits.memory": "50%"})[1])

    def test_lxds_defaults_where_they_are_not_set(self):
        # LXD gives such a VM one CPU and 1 GiB (QEMUDefaultCPUCores and QEMUDefaultMemSize).
        self.assertEqual(lxdtop.vm_limits({}), (1, GiB))
        self.assertEqual(lxdtop.vm_limits({"limits.cpu": "", "limits.memory": ""}), (1, GiB))

    def test_a_value_it_cannot_read_is_no_limit_it_knows(self):
        for value in ("two", "1.5", "0-", "-3", "3-1", "0x2", "1,,3"):
            with self.subTest(cpu=value):
                self.assertIsNone(lxdtop.vm_limits({"limits.cpu": value})[0])
        for value in ("4G", "4gib", "4 GiB", "1.5GiB", "GiB", "-1GiB", "%", "-5%"):
            with self.subTest(memory=value):
                self.assertIsNone(lxdtop.vm_limits({"limits.memory": value})[1])

    def test_values_that_are_not_text(self):
        # LXD's config values are always strings; anything else still can't stop lxdtop reading the rest.
        self.assertEqual(lxdtop.vm_limits({"limits.cpu": 4, "limits.memory": None}), (4, GiB))
        self.assertEqual(lxdtop.vm_limits({"limits.cpu": ["0-3"], "limits.memory": 4.5}), (None, None))

    def test_no_config_from_lxd_means_no_limits_rather_than_its_defaults(self):
        self.assertEqual(lxdtop.vm_limits(None), (None, None))

    def test_only_the_two_limits_are_read(self):
        config = WatchedConfig({"limits.cpu": "2", "limits.memory": "4GiB", "cloud-init.user-data": "password: hunter2",
                                "environment.API_TOKEN": "s3cret"})
        self.assertEqual(lxdtop.vm_limits(config), (2, 4 * GiB))
        self.assertEqual(sorted(config.asked), ["limits.cpu", "limits.memory"])

    def test_from_lxds_answer_only_a_vms_and_nothing_else_in_its_config(self):
        routes = lxd_routes()
        listed = {inst["name"]: inst for inst in routes[INSTANCES][1]["metadata"]}
        for name in ("win", "web"):
            listed[name]["config"] = {"cloud-init.user-data": "#cloud-config\npassword: hunter2"}
            listed[name]["expanded_config"] = {"limits.cpu": "0-5", "limits.memory": "6GiB",
                                               "environment.API_TOKEN": "s3cret", "user.note": "rack 7"}
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        found = api.snapshot()[0]
        self.assertEqual((found["win"]["cpu_max"], found["win"]["mem_max"]), (6, 6 * GiB))
        self.assertEqual((found["web"]["cpu_max"], found["web"]["mem_max"]), (None, None))   # a container: its cgroup
        for secret in ("hunter2", "s3cret", "rack 7", "cloud-init", "API_TOKEN", "limits."):
            self.assertNotIn(secret, repr(found))


class SocketPathTest(unittest.TestCase):
    def test_lxd_dir_comes_first(self):
        from tests.support import load_lxdtop
        with mock.patch.dict(os.environ, {"LXD_DIR": "/srv/lxd"}):
            module = load_lxdtop("lxdtop_with_lxd_dir")
        self.assertEqual(module.LXD_SOCKETS, ["/srv/lxd/unix.socket", "/var/snap/lxd/common/lxd/unix.socket",
                                              "/var/lib/lxd/unix.socket"])

    def test_default_sockets(self):
        from tests.support import load_lxdtop
        with mock.patch.dict(os.environ):
            os.environ.pop("LXD_DIR", None)
            module = load_lxdtop("lxdtop_without_lxd_dir")
        self.assertEqual(module.LXD_SOCKETS, ["/var/snap/lxd/common/lxd/unix.socket", "/var/lib/lxd/unix.socket"])


if __name__ == "__main__":
    unittest.main()
