"""Instances in LXD projects: two of the same name, in different projects, can be told apart.

The fake host has web and db in the default project, a stopped container (old), and an
instance called api in each of two projects, default and dev. Between its two readings,
dev's api is the busiest, and held back by its CPU limit in half its periods.
"""

import copy
import unittest
from unittest import mock

from tests.support import FakeHost, FakeLxd, glyphs, lxd_routes, lxdtop

INSTANCES = "/1.0/instances?recursion=2&all-projects=true"


def event(action, name, project="default", user="", **context):
    """A lifecycle event as LXD 5.21 and 6 send it: outside the default project, its source ends in ?project=."""
    source = f"/1.0/instances/{name}" + ("" if project == "default" else f"?project={project}")
    return {"type": "lifecycle", "project": project, "metadata": {
        "action": action, "source": source, "context": context, "requestor": {"username": user},
        "name": name, "project": project}}


STARTED = event("instance-started", "api", user="alice")                 # the default project's api
EXEC = event("instance-exec", "api", project="dev", user="bob")           # dev's
PUSHED = [event("instance-file-pushed", "web", user="ci", path=f"/srv/file{i}") for i in range(60)]


class ProjectsTest(unittest.TestCase):
    def setUp(self):
        self.host = host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        for cg in ("web", "db", "api", "dev_api"):
            host.add_container(cg, 1_000_000)
        host.write("/sys/fs/cgroup/lxc.payload.dev_api/cpu.max", "200000 100000\n")      # 2 cores
        host.write("/sys/fs/cgroup/lxc.payload.dev_api/cpu.stat", "usage_usec 1000000\nnr_periods 0\nnr_throttled 0\n")
        self.routes = lxd_routes()
        answer = self.routes[INSTANCES][1]["metadata"]
        answer[:] = [inst for inst in answer if inst["name"] != "win"]      # no VM here
        dev_api = next(inst for inst in answer if inst["name"] == "api")
        api = copy.deepcopy(dev_api)
        api["project"], api["state"]["pid"] = "default", 4500
        answer.append(api)

    def readings(self, events=(), drop=()):
        """A Monitor after two readings 2 s apart, with LXD's answers (less the instances in drop) and events."""
        self.routes[INSTANCES][1]["metadata"] = [inst for inst in self.routes[INSTANCES][1]["metadata"]
                                                 if (inst.get("project"), inst["name"]) not in drop]
        gone = {name if project == "default" else f"{project}_{name}" for project, name in drop}
        for cg in gone:
            self.host.remove(f"/sys/fs/cgroup/lxc.payload.{cg}")
        with FakeLxd(self.routes) as lxd, self.host.installed():
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            feed = lxdtop.LxdEvents("/unused")
            for each in events:
                feed._record(each)
            mon = lxdtop.Monitor(api, feed)
            mon.sample()
            self.host.clock += 2
            self.host.set_cpu_times((500, 0, 1300), [(125, 0, 325)] * 4)
            for cg, used, more in (("web", 100_000, ""), ("db", 100_000, ""), ("api", 200_000, ""),
                                   ("dev_api", 3_000_000, "\nnr_periods 20\nnr_throttled 10")):
                if cg not in gone:
                    self.host.write(f"/sys/fs/cgroup/lxc.payload.{cg}/cpu.stat",
                                    f"usage_usec {1_000_000 + used}{more}\n")
            mon.sample()
        return mon

    def draw(self, mon, detail=None, width=160, height=45):
        ui = lxdtop.new_ui(2)
        if detail:
            ui.view, ui.detail = "detail", detail
        canvas = lxdtop.TextCanvas(height, width)
        with glyphs(lxdtop.GLYPHS_UNICODE), mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
            lxdtop.draw(canvas, mon, ui)
        return canvas.text()

    def names(self, text):
        """The table's names, in its order: what follows each row's dot."""
        return [line.split("●")[1].split()[0] for line in text.splitlines() if "● " in line and "│" in line]

    def test_the_table_the_warning_and_the_legend_tell_them_apart(self):
        """#9: the table showed two rows called api, and the warning line couldn't say whose it was."""
        text = self.draw(self.readings())
        self.assertEqual(self.names(text), ["dev/api", "api", "db", "web", "old"])   # busiest first, then stopped
        self.assertIn("dev/api: throttled 50% @2c", text)
        legend = next(line for line in text.splitlines() if "host itself" in line)
        self.assertIn("■ dev/api  ■ api  ", legend)

    def test_the_events_panel_tells_them_apart(self):
        text = self.draw(self.readings([STARTED, EXEC]))
        self.assertRegex(text, r"exec +dev/api +bob")
        self.assertRegex(text, r"started +api +alice")

    def test_each_detail_view_has_its_own_instances_events(self):
        """#15: the detail view took the events of every instance with its name, in any project."""
        mon = self.readings([STARTED, EXEC])
        dev = self.draw(mon, detail="dev_api")
        self.assertIn("┐dev/api┌", dev)
        self.assertRegex(dev, r"events +\d\d:\d\d:\d\d  exec +bob")
        self.assertNotIn("alice", dev)
        default = self.draw(mon, detail="api")
        self.assertIn("┐api┌", default)
        self.assertRegex(default, r"events +\d\d:\d\d:\d\d  started +alice")
        self.assertNotIn("bob", default)

    def test_an_instances_own_lines_outlast_the_shared_feed(self):
        """#15: on a busy host, 50 newer lines about other instances pushed an instance's out of its detail view."""
        mon = self.readings([STARTED, EXEC] + PUSHED)
        self.assertEqual(len(mon.events.snapshot()[0]), 50)                  # the feed has only web's now
        self.assertRegex(self.draw(mon, detail="api"), r"events +\d\d:\d\d:\d\d  started +alice")
        self.assertRegex(self.draw(mon, detail="dev_api"), r"events +\d\d:\d\d:\d\d  exec +bob")
        web = self.draw(mon, detail="web", height=80)                        # room for more than 50
        self.assertEqual(web.count("file push"), lxdtop.INSTANCE_EVENTS)    # its latest 50 of 60

    def test_the_json_tells_them_apart(self):
        doc = lxdtop.reading_json(self.readings([STARTED, EXEC]), 2)
        self.assertEqual([(i["name"], i.get("project")) for i in doc["instances"]],
                         [("api", "dev"), ("api", "default"), ("db", "default"), ("web", "default"),
                          ("old", "default")])
        self.assertEqual([(w["instance"], w.get("project"), w["text"]) for w in doc["warnings"]],
                         [("api", "dev", "dev/api: throttled 50% @2c")])
        self.assertEqual([(e["action"], e["instance"], e.get("project"), e["user"]) for e in doc["events"]["feed"]],
                         [("instance-exec", "api", "dev", "bob"), ("instance-started", "api", "default", "alice")])

    def test_a_legend_names_an_instance_as_the_table_does(self):
        """#18: the legend named dev's api by its cgroup, dev_api, where the table and the events said api."""
        text = self.draw(self.readings(drop=[("default", "api")]))
        self.assertEqual(self.names(text), ["api", "db", "web", "old"])     # no other api: just its name
        legend = next(line for line in text.splitlines() if "host itself" in line)
        self.assertIn("■ api  ", legend)
        self.assertNotIn("dev_api", text)

    def test_with_one_project_names_stay_as_they_are(self):
        """Whichever project it is: here, the dev project's api is the only instance outside the default one."""
        mon = self.readings(drop=[("default", "api")])
        self.assertEqual(sorted((row["project"], row["label"]) for row in mon.rows),
                         [("default", "db"), ("default", "old"), ("default", "web"), ("dev", "api")])


class RecordTest(unittest.TestCase):
    def test_an_instances_own_lines(self):
        events = lxdtop.LxdEvents("/unused")
        for each in [STARTED, EXEC, STARTED] + PUSHED:
            events._record(each)
        self.assertEqual([(e["action"], e["user"], e["count"]) for e in events.lines_of("default", "api")],
                         [("instance-started", "alice", 2)])                 # a repeat joins its line, as in the feed
        self.assertEqual([e["user"] for e in events.lines_of("dev", "api")], ["bob"])
        self.assertEqual(len(events.lines_of("default", "web")), lxdtop.INSTANCE_EVENTS)
        self.assertEqual(events.lines_of("default", "nothing"), [])

    def test_only_an_instances_events_are_its_own(self):
        """A profile or a network can have an instance's name: its events are not the instance's."""
        events = lxdtop.LxdEvents("/unused")
        events._record({"type": "lifecycle", "metadata": {"action": "profile-updated",
                                                           "source": "/1.0/profiles/api", "name": "api"}})
        self.assertEqual(events.lines_of("default", "api"), [])
        self.assertEqual([e["name"] for e in events.snapshot()[0]], ["api"])  # still in the feed

    def test_the_instances_with_the_latest_events_are_kept(self):
        events = lxdtop.LxdEvents("/unused")
        for i in range(lxdtop.EVENT_INSTANCES + 1):
            events._record(event("instance-started", f"c{i}"))
        self.assertEqual(events.lines_of("default", "c0"), [])               # the longest quiet goes first
        self.assertEqual(len(events.lines_of("default", "c1")), 1)
        self.assertEqual(len(events.by_instance), lxdtop.EVENT_INSTANCES)


if __name__ == "__main__":
    unittest.main()
