"""LXD's REST API over its unix socket, answered by a fake LXD (tests.support.FakeLxd)."""

import datetime
import os
import unittest
from unittest import mock

from tests.support import FakeLxd, lxd_routes, lxdtop

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
            "web": dict(name="web", status="Running", veths=["veth1a2b3c4d"], ipv4="10.10.10.21",
                        type="container", pid=4242, snapshot=NIGHTLY),
            "db": dict(name="db", status="Running", veths=["veth22220000", "veth22221111"],
                       ipv4="192.168.50.12", type="container", pid=4300, snapshot=None),
            # Outside the default project, the cgroup is named "<project>_<name>".
            "dev_api": dict(name="api", status="Running", veths=[], ipv4="", type="container", pid=4400,
                            snapshot=None),
            "win": dict(name="win", status="Running", veths=["tap0f1e2d3c"], ipv4="10.10.10.30",
                        type="virtual-machine", pid=5151, snapshot=None),
            "old": dict(name="old", status="Stopped", veths=[], ipv4="", type="container", pid=0, snapshot=None),
        })
        self.assertEqual(server, dict(version="6.9", kernel="6.8.0-45-generic", os="Ubuntu 24.04",
                                      member="",                        # not a cluster member
                                      zfs_roots=["default"]))          # where the ZFS pool keeps its datasets
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
