"""On a cluster member: each ZFS pool's own config for this member, and more time for LXD's answers.

There's no real cluster to test on: these use fake LXD answers shaped as LXD 5.21 and 6 give them
(lxd/storage_pools.go and lxd/db/storage_pools.go in their source).
"""

import unittest
from unittest import mock

from tests.support import FakeLxd, lxd_routes, lxdtop

INSTANCES = "/1.0/instances?recursion=2&all-projects=true"
OWN_POOL = "/1.0/storage-pools/default?target=m1"


def cluster_routes(own_pool=True):
    """A member, m1, of a cluster whose ZFS pool "default" keeps its datasets in tank/lxd on m1.

    The cluster-wide pool list leaves out the config that can differ between members, which LXD
    calls NodeSpecificStorageConfig: source, size, zfs.pool_name and others. A GET of the pool
    with target=<member> has that member's.
    """
    routes = lxd_routes()
    routes["/1.0"][1]["metadata"]["environment"].update(server_clustered=True, server_name="m1")
    for inst in routes[INSTANCES][1]["metadata"]:
        inst["location"] = "m1"
    pools = routes["/1.0/storage-pools?recursion=1"][1]["metadata"]
    default = next(pool for pool in pools if pool["name"] == "default")
    default["config"].pop("zfs.pool_name", None)
    for key in ("source", "size"):
        default["config"].pop(key, None)
    if own_pool:
        mine = dict(default, config=dict(default["config"], **{"zfs.pool_name": "tank/lxd", "source": "tank/lxd"}))
        routes[OWN_POOL] = (200, {"type": "sync", "metadata": mine})
    return routes


class MemberPoolConfigTest(unittest.TestCase):
    """#8: on a cluster, lxdtop took each ZFS pool's name for its dataset, as zfs.pool_name was missing."""

    def fetch(self, routes):
        with FakeLxd(routes) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        return api, lxd.requests

    def test_this_members_own_config_for_each_zfs_pool(self):
        api, requests = self.fetch(cluster_routes())
        self.assertEqual(api.status, "ok")
        self.assertEqual(api.server["zfs_roots"], ["tank/lxd"])            # not "default"
        self.assertIn(OWN_POOL, [target for _, target, _ in requests])
        self.assertEqual({method for method, _, _ in requests}, {"GET"})

    def test_only_on_a_cluster(self):
        _, requests = self.fetch(lxd_routes())
        self.assertFalse([target for _, target, _ in requests if "target=" in target])

    def test_only_for_zfs_pools(self):
        _, requests = self.fetch(cluster_routes())
        self.assertEqual([target for _, target, _ in requests if "target=" in target], [OWN_POOL])   # not scratch

    def test_if_lxd_cannot_say_the_pools_name_stands_for_its_dataset_as_before(self):
        api, _ = self.fetch(cluster_routes(own_pool=False))                # the GET answers 404
        self.assertEqual((api.status, api.server["zfs_roots"]), ("ok", ["default"]))


class TimeoutTest(unittest.TestCase):
    """#8: listing instances on a cluster asks every member, which a big or slow cluster can take longer for."""

    def fetch(self, routes, delays):
        with mock.patch.object(lxdtop, "API_TIMEOUT", 0.2), mock.patch.object(lxdtop, "CLUSTER_API_TIMEOUT", 3), \
                FakeLxd(routes, delays=delays) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
        return api

    def test_a_cluster_member_waits_longer(self):
        api = self.fetch(cluster_routes(), {INSTANCES: 0.5})
        self.assertEqual(api.status, "ok")
        self.assertIn("win", api.snapshot()[0])
        self.assertEqual(api.timeout, 3)

    def test_a_standalone_host_waits_as_before(self):
        api = self.fetch(lxd_routes(), {INSTANCES: 0.5})
        self.assertEqual(api.status, "error (TimeoutError)")
        self.assertEqual(api.timeout, 0.2)
        self.assertEqual((lxdtop.API_TIMEOUT, lxdtop.CLUSTER_API_TIMEOUT), (5, 30))

    def test_each_request_uses_it(self):
        timeouts = []
        real = lxdtop.UnixHTTPConnection

        def recorded(path, timeout=5):
            timeouts.append(timeout)
            return real(path, timeout)

        with mock.patch.object(lxdtop, "UnixHTTPConnection", recorded), FakeLxd(cluster_routes()) as lxd:
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            api.fetch()
        targets = [target for _, target, _ in lxd.requests]
        self.assertEqual(targets[0], "/1.0")
        # Until LXD has said this is a cluster member, 5 s; after, 30 s, for every request.
        self.assertEqual(timeouts[0], 5)
        self.assertEqual(timeouts[targets.index(INSTANCES):], [30] * (len(targets) - targets.index(INSTANCES)))


if __name__ == "__main__":
    unittest.main()
