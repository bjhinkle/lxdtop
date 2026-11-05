"""The screen: an arrow key whose code comes split, and the busiest process's CPU."""

import curses
import unittest
from unittest import mock

from tests.support import FakeHost, lxdtop, monitor, row

ESC, UP, DOWN = 27, curses.KEY_UP, curses.KEY_DOWN
TICKS = lxdtop.CLOCK_TICKS


class Clock:
    """time for handle_key(): monotonic() is now, which a test moves on."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class SplitArrowKeyTest(unittest.TestCase):
    """#15: an arrow key sends Esc, "[" (or "O"), then A or B. curses waits 25 ms after Esc for the rest,
    so on a slow link the rest can come late: the key read as Esc, then "[" and "A"."""

    def setUp(self):
        self.mon = monitor([row(name, cpu=float(50 - i)) for i, name in enumerate(("web", "db", "api"))])
        self.ui = lxdtop.new_ui(2)
        self.clock = Clock()
        patcher = mock.patch.object(lxdtop, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def press(self, *keys, gap=0.03):
        """Each key gap seconds after the last, as curses gives them; what handle_key() said for each."""
        said = []
        for key in keys:
            said.append(lxdtop.handle_key(ord(key) if isinstance(key, str) else key, self.mon, self.ui))
            self.clock.now += gap
        return said

    def state(self):
        return dict(view=self.ui.view, filter=self.ui.filter, selected=self.ui.selected)

    def test_up_with_a_filter_on(self):
        self.press("/", "a", "p", "i", 10)                    # filter on "api", kept
        self.press(ESC, "[", "A")                             # Up, split after its Esc
        self.assertEqual(self.state(), dict(view="main", filter="api", selected="api"))   # not cleared

    def test_down_and_up_move_the_highlight(self):
        self.press(DOWN, DOWN)                                # web, then db
        self.assertEqual(self.press(ESC, "[", "B"), [True, False, True])    # Esc acts, then is undone
        self.assertEqual(self.ui.selected, "api")             # Down, as if it came whole
        self.press(ESC, "O", "A")                             # the other form: Esc O A
        self.assertEqual(self.ui.selected, "db")

    def test_on_the_detail_view_and_while_typing(self):
        self.press(DOWN, 10)                                  # web's details
        self.press(ESC, "[", "B")
        self.assertEqual((self.ui.view, self.ui.detail), ("detail", "web"))    # Down does nothing there
        self.press(ESC, "/", "w")                             # back to the table; type a filter
        self.press(ESC, "[", "A")
        self.assertEqual(self.state(), dict(view="typing", filter="w", selected="web"))

    def test_esc_on_its_own_still_acts_at_once(self):
        self.press(DOWN)
        self.assertEqual(self.press(ESC), [True])
        self.assertIsNone(self.ui.selected)                   # no waiting for what may come after
        self.assertEqual(self.press("s"), [True])             # another key after it acts as itself
        self.assertEqual(self.ui.sort, "mem")

    def test_too_late_to_be_the_same_key(self):
        self.press(DOWN)
        self.press(ESC, gap=lxdtop.SPLIT_KEY_SECS + 0.01)
        self.press("[", "A")
        self.assertIsNone(self.ui.selected)                   # Esc stands; "[" and "A" do nothing

    def test_other_keys_after_esc_and_bracket(self):
        self.press(DOWN)
        self.press(ESC, "[", "5", "~")                        # Page Up, split: lxdtop has no use for it
        self.assertIsNone(self.ui.selected)


class BusiestProcessTest(unittest.TestCase):
    """BJ's screenshot: java at 7.9% inside wazuh at 1.1%. The process's CPU was averaged with its
    reading before, while the container's is the last interval's alone."""

    def readings(self, *intervals):
        """A container, wazuh, whose only busy process is java: for each 2 s interval, the CPU the cgroup
        counted and the clock ticks java used. The busiest process after the last."""
        host = FakeHost(self)
        host.set_basics()
        host.set_cpu_times((100, 0, 900), [(25, 0, 225)] * 4)
        host.add_container("wazuh", 1_000_000)
        usec, ticks = 1_000_000, 0
        host.add_process(4242, "java", "/lxc.payload.wazuh/system.slice/wazuh.service", utime=ticks, start=500)
        with host.installed():
            mon = lxdtop.Monitor(None, None)
            mon.sample()
            for cgroup_usec, java_ticks in intervals:
                host.clock += 2
                usec, ticks = usec + cgroup_usec, ticks + java_ticks
                host.write("/sys/fs/cgroup/lxc.payload.wazuh/cpu.stat", f"usage_usec {usec}\n")
                host.add_process(4242, "java", "/lxc.payload.wazuh/system.slice/wazuh.service", utime=ticks,
                                 start=500)
                mon.sample()
        (wazuh,) = mon.rows
        return wazuh["cpu"], wazuh["top"], lxdtop.reading_json(mon, 2)["instances"][0]["busiest_process"]

    def test_never_more_than_its_container(self):
        cpu, top, json_top = self.readings((300_000, 30 * TICKS // 100), (20_000, 2 * TICKS // 100))   # 15%, then 1%
        self.assertEqual(cpu, 1.0)
        self.assertEqual(top, ("java", 1.0))                  # the last 2 s, as wazuh's: not (15 + 1) / 2
        self.assertEqual(json_top, dict(name="java", cpu_pct=1.0))

    def test_its_clock_ticks_cannot_put_it_over(self):
        cpu, top, _ = self.readings((20_000, 3 * TICKS // 100))       # the cgroup counted 1%, the ticks 1.5%
        self.assertEqual((cpu, top), (1.0, ("java", 1.0)))


if __name__ == "__main__":
    unittest.main()
