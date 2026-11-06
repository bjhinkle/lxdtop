"""Instances changing state: a container that stops, and a paused (frozen) VM.

The fake host runs web, db and dev's api as containers, and win as a VM whose QEMU process is
5151; LXD's answers come from the fixtures, where old is stopped.
"""

import unittest
from unittest import mock

from tests.support import FakeHost, FakeLxd, glyphs, lxd_routes, lxdtop

INSTANCES = "/1.0/instances?recursion=2&all-projects=true"


class Host(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(self)
        self.host.set_basics()
        self.host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        for cg in ("web", "db", "dev_api"):
            self.host.add_container(cg, 1_000_000)
        self.host.copy_fixture("pid-stat-qemu.txt", "/proc/5151/stat")
        self.host.copy_fixture("pid-status-qemu.txt", "/proc/5151/status")
        self.routes = lxd_routes()

    def lxd_says(self, routes, name, status):
        for inst in routes[INSTANCES][1]["metadata"]:
            if inst["name"] == name:
                inst["status"] = status

    def row(self, mon, name):
        return next(row for row in mon.rows if row["name"] == name)

    def draw(self, mon, width=160, height=45):
        canvas = lxdtop.TextCanvas(height, width)
        with glyphs(lxdtop.GLYPHS_UNICODE), mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
            lxdtop.draw(canvas, mon, lxdtop.new_ui(2))
        return canvas


class StopTest(Host):
    """#16: a container that stopped showed as a grey "running" row, and counted as running, until LXD's
    next answer, up to 15 seconds later (API_EVERY)."""

    def test_lxd_is_asked_again_at_once_and_the_row_moves_to_stopped(self):
        with FakeLxd(self.routes) as lxd, self.host.installed():
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            mon = lxdtop.Monitor(api, None)
            for _ in range(2):
                mon.sample()
                self.host.clock += 2
            self.assertFalse(api.wake.is_set())                  # web has its readings
            self.host.remove("/sys/fs/cgroup/lxc.payload.web")   # web stops, and LXD knows it
            self.lxd_says(lxd.routes, "web", "Stopped")
            mon.sample()                                          # lxdtop's last answer from LXD says Running
            self.assertEqual((self.row(mon, "web")["live"], self.row(mon, "web")["state"]), (False, "Running"))
            self.assertTrue(api.wake.is_set())                   # so the API thread asks LXD now
            api.wake.clear()
            api.fetch()                                           # as that thread does when woken
            self.host.clock += 2
            mon.sample()
        self.assertEqual(self.row(mon, "web")["state"], "Stopped")
        self.assertIn("3 running · 2 stopped", self.draw(mon).text().splitlines()[0])   # web and old stopped
        self.assertEqual(lxdtop.API_EVERY, 15)                   # the regular refresh is as it was

    def test_it_asks_once_and_only_for_one_that_had_readings(self):
        self.lxd_says(self.routes, "web", "Running")
        self.host.remove("/sys/fs/cgroup/lxc.payload.db")       # db: LXD says running, but never a reading here
        with FakeLxd(self.routes) as lxd, self.host.installed():
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            mon = lxdtop.Monitor(api, None)
            for _ in range(2):
                mon.sample()
                self.host.clock += 2
            self.assertFalse(api.wake.is_set())
            self.host.remove("/sys/fs/cgroup/lxc.payload.web")   # web's readings stop; LXD still says running
            mon.sample()
            self.assertTrue(api.wake.is_set())
            api.wake.clear()
            api.fetch()
            self.host.clock += 2
            mon.sample()                                          # still running, as LXD says: not asked again
            self.assertFalse(api.wake.is_set())


class FrozenVmTest(Host):
    """#16: a paused VM counted as stopped and showed no readings, though its QEMU process was still there.
    LXD calls a paused instance Frozen. A frozen container has its cgroup, so it shows its readings with a
    yellow dot and counts as running: a frozen VM now does the same."""

    def readings(self, frozen=("win",)):
        for name in frozen:
            self.lxd_says(self.routes, name, "Frozen")
        with FakeLxd(self.routes) as lxd, self.host.installed():
            api = lxdtop.LxdApi(lxd.path)
            api.fetch()
            mon = lxdtop.Monitor(api, None)
            for _ in range(2):
                mon.sample()
                self.host.clock += 2
        return mon

    def test_its_process_is_read_as_a_running_vms(self):
        win = self.row(self.readings(), "win")
        self.assertEqual((win["live"], win["state"], win["vm"]), (True, "Frozen", True))
        self.assertIsNotNone(win["mem"])
        self.assertIsNotNone(win["up"])
        self.assertIsNotNone(win["cpu"])

    def test_it_shows_and_counts_as_a_frozen_container_does(self):
        mon = self.readings(frozen=("win", "web"))
        canvas = self.draw(mon)
        lines = canvas.text().splitlines()
        self.assertIn("4 running · 1 stopped", lines[0])
        for name in ("web", "win"):
            y = next(y for y, line in enumerate(lines) if f" {name} " in line and "│" in line)
            x = lines[y].index(f" {name} ") - 1
            self.assertEqual(canvas.cells[y][x][1], "mid", name)     # the dot: yellow, as for any frozen instance
            self.assertNotIn("frozen", lines[y], name)               # its readings, not its state, as for running
        doc = lxdtop.reading_json(mon, 2)
        self.assertEqual(doc["instance_counts"], dict(running=4, stopped=1))
        win = next(i for i in doc["instances"] if i["name"] == "win")
        self.assertEqual(win["state"], "Frozen")
        self.assertIsNotNone(win["mem_bytes"])

    def test_its_detail_view_shows_its_state_in_its_dots_color(self):
        """#16: the detail view showed a frozen instance's state in green, as a running one's, where its dot
        in the table is yellow. Now it's yellow too, for a container and a VM alike."""
        mon = self.readings(frozen=("win", "web"))
        for name, cg, color in (("web", "web", "mid"), ("win", "win", "mid"), ("db", "db", "lo")):
            with self.subTest(name):
                ui = lxdtop.new_ui(2)
                ui.view, ui.detail = "detail", cg
                canvas = lxdtop.TextCanvas(45, 160)
                with glyphs(lxdtop.GLYPHS_UNICODE), \
                        mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
                    lxdtop.draw(canvas, mon, ui)
                line = canvas.text().splitlines()[2]
                state = "frozen" if color == "mid" else "running"
                x = line.index(state)
                self.assertEqual(line[:x], "│ state       ")
                self.assertEqual({style for _, style in canvas.cells[2][x:x + len(state)]}, {color})

    def test_one_it_cannot_read_still_counts_as_frozen_and_says_why(self):
        self.host.remove("/proc/5151")
        mon = self.readings()
        self.assertIn("4 running · 1 stopped", self.draw(mon).text().splitlines()[0])
        line = next(line for line in self.draw(mon).text().splitlines() if " win " in line)
        self.assertRegex(line, "win +frozen   no readings: QEMU process 5151 not found")


if __name__ == "__main__":
    unittest.main()
