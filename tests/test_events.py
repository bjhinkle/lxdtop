"""LXD's lifecycle event stream: the websocket handshake, frames, and turning events into feed lines."""

import base64
import json
import socket
import types
import unittest
from unittest import mock

from tests.support import FakeLxd, lifecycle_events, lxdtop, ws_frame


def frames(*events):
    return [ws_frame(1, json.dumps(e).encode()) for e in events]


class StopLoop(Exception):
    """Raised by the patched time.sleep to end LxdEvents._loop after one connection."""


class _Time:
    def __init__(self, now=1_790_000_000.0, sleeps=1):
        self.now, self.sleeps = now, sleeps

    def time(self):
        return self.now

    def sleep(self, secs):
        self.sleeps -= 1
        if self.sleeps < 0:
            raise StopLoop()

    def __getattr__(self, name):
        import time
        return getattr(time, name)


def forget_tracebacks(err):
    """Drop the tracebacks of err and of the exceptions it was raised while handling.

    They hold the frames of lxdtop's event listener, and with them the file it
    made from its socket. That keeps the socket open after close() until the
    garbage collector runs, and Python then warns about an unclosed socket.
    """
    while err is not None:
        err.__traceback__ = None
        err = err.__context__


def listen(lxd, show_commands=False):
    """Run LxdEvents._listen() against a FakeLxd until the stream ends. Returns (events, error)."""
    events = lxdtop.LxdEvents(lxd.path, show_commands=show_commands)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)                     # a broken test fails instead of hanging
    try:
        events._listen(sock)
        error = None
    except Exception as err:                # noqa: BLE001 - the test looks at which one
        error = err
        forget_tracebacks(err)
    finally:
        sock.close()
    return events, error


def run_loop(lxd_path, connections=1):
    """Run LxdEvents._loop() for this many connection attempts.

    Returns the LxdEvents, with `ended` set to the exception that ended _loop()
    itself, if any: in lxdtop, that would end the listener thread for good.
    """
    events = lxdtop.LxdEvents(lxd_path)
    events.ended = None
    with mock.patch.object(lxdtop, "time", _Time(sleeps=connections - 1)):
        try:
            events._loop()
        except StopLoop:
            pass
        except Exception as err:            # noqa: BLE001 - see SECURITY-REVIEW.md L-4
            events.ended = err
            forget_tracebacks(err)
    return events


class HandshakeTest(unittest.TestCase):
    def test_the_upgrade_request(self):
        with FakeLxd() as lxd:
            listen(lxd)
        ((method, target, headers),) = lxd.requests
        self.assertEqual((method, target), ("GET", "/1.0/events?type=lifecycle"))
        self.assertEqual(headers["upgrade"], "websocket")
        self.assertEqual(headers["connection"], "Upgrade")
        self.assertEqual(headers["sec-websocket-version"], "13")
        self.assertEqual(len(base64.b64decode(headers["sec-websocket-key"])), 16)

    def test_status_is_live_once_upgraded(self):
        with FakeLxd(script=[]) as lxd:
            events, error = listen(lxd)
        self.assertEqual(events.status, "live")
        self.assertIsInstance(error, ConnectionError)        # the fake closed the stream

    def test_a_refused_upgrade(self):
        with FakeLxd(events_status=403) as lxd:
            events, error = listen(lxd)
        self.assertIsInstance(error, ConnectionError)
        self.assertEqual(events.status, "connecting")


class FrameTest(unittest.TestCase):
    def test_events_become_feed_lines_newest_first(self):
        with FakeLxd(script=frames(*lifecycle_events())) as lxd:
            events, _ = listen(lxd)
        feed, status = events.snapshot()
        self.assertEqual([(e["action"], e["name"], e["detail"], e["user"]) for e in feed], [
            ("instance-stopped", "old", "", "bj"),
            ("instance-file-pushed", "web", "/etc/nginx/nginx.conf", "bj"),
            ("instance-exec", "db", "", "backup"),                   # the command line stays hidden
            ("instance-snapshot-created", "web", "before-upgrade", "bj"),
            ("instance-started", "web", "", "bj"),
        ])
        self.assertNotIn("S3cret", repr(feed))

    def test_exec_commands_show_only_when_asked(self):
        with FakeLxd(script=frames(*lifecycle_events())) as lxd:
            events, _ = listen(lxd, show_commands=True)
        exec_line = next(e for e in events.feed if e["action"] == "instance-exec")
        self.assertEqual(exec_line["detail"], "mysql -uroot -pS3cret! -e SHOW DATABASES")

    def test_a_message_in_several_frames(self):
        data = json.dumps(lifecycle_events()[0]).encode()
        script = [ws_frame(1, data[:10], final=False), ws_frame(0, data[10:30], final=False),
                  ws_frame(9, b"ping between pieces"), FakeLxd.READ_FRAME, ws_frame(0, data[30:])]
        with FakeLxd(script=script) as lxd:
            events, _ = listen(lxd)
        self.assertEqual([e["action"] for e in events.feed], ["instance-started"])
        self.assertEqual(lxd.received, [(True, 0xA, True, b"ping between pieces")])

    def test_long_messages(self):
        long_path = "/srv/" + "x" * 300                        # 16-bit length
        huge_path = "/srv/" + "y" * 70000                      # 64-bit length
        script = frames(*[dict(metadata=dict(action="instance-file-pushed", source="/1.0/instances/web",
                                             context=dict(path=p))) for p in (long_path, huge_path)])
        with FakeLxd(script=script) as lxd:
            events, _ = listen(lxd)
        self.assertEqual([e["detail"] for e in events.feed], [huge_path, long_path])

    def test_masked_frames_from_the_server(self):
        data = json.dumps(lifecycle_events()[0]).encode()
        with FakeLxd(script=[ws_frame(1, data, mask=b"\x01\x02\x03\x04")]) as lxd:
            events, _ = listen(lxd)
        self.assertEqual(events.feed[0]["name"], "web")

    def test_ping_gets_a_masked_pong(self):
        with FakeLxd(script=[ws_frame(9, b"keepalive"), FakeLxd.READ_FRAME]) as lxd:
            listen(lxd)
        self.assertEqual(lxd.received, [(True, 0xA, True, b"keepalive")])

    def test_pongs_are_the_only_thing_sent_after_the_handshake(self):
        script = frames(*lifecycle_events()[:2]) + [ws_frame(9, b"one"), FakeLxd.READ_FRAME] + \
            frames(*lifecycle_events()[2:]) + [ws_frame(9, b"two"), FakeLxd.READ_FRAME, FakeLxd.DRAIN]
        with FakeLxd(script=script) as lxd:
            listen(lxd)
        self.assertEqual(lxd.received, [(True, 0xA, True, b"one"), (True, 0xA, True, b"two")])
        self.assertEqual(lxd.extra, b"")

    def test_a_close_frame_ends_the_stream(self):
        with FakeLxd(script=[ws_frame(8, b"\x03\xe8"), ws_frame(1, b"{}")]) as lxd:
            events, error = listen(lxd)
        self.assertEqual(str(error), "event stream closed")
        self.assertEqual(events.feed, type(events.feed)())   # the frame after the close isn't read

    def test_the_stream_ending_mid_frame(self):
        data = ws_frame(1, json.dumps(lifecycle_events()[0]).encode())
        for cut in (1, 2, 5, len(data) - 1):
            with self.subTest(cut=cut), FakeLxd(script=[data[:cut]]) as lxd:
                events, error = listen(lxd)
                self.assertIsInstance(error, (ConnectionError, ValueError))
                self.assertEqual(len(events.feed), 0)

    def test_a_message_that_is_not_json(self):
        with FakeLxd(script=[ws_frame(1, b"{not json")]) as lxd:
            _, error = listen(lxd)
        self.assertIsInstance(error, ValueError)


class LoopTest(unittest.TestCase):
    """_loop() reconnects every 5 seconds and shows why in the panel's corner."""

    def test_no_socket(self):
        self.assertEqual(run_loop("/nonexistent/unix.socket").status, "reconnecting")

    def test_no_access(self):
        with mock.patch.object(socket.socket, "connect", side_effect=PermissionError(13, "denied")):
            self.assertEqual(run_loop("/var/snap/lxd/common/lxd/unix.socket").status, "no access")

    def test_refused_closed_or_garbled_streams_reconnect(self):
        for kwargs in (dict(events_status=403), dict(script=[]), dict(script=[ws_frame(1, b"{not json")]),
                       dict(script=[ws_frame(8)])):
            with self.subTest(**{k: repr(v)[:40] for k, v in kwargs.items()}), FakeLxd(**kwargs) as lxd:
                self.assertEqual(run_loop(lxd.path).status, "reconnecting")

    def test_the_feed_survives_a_reconnect(self):
        with FakeLxd(script=frames(lifecycle_events()[0])) as lxd:
            events = lxdtop.LxdEvents(lxd.path)
            with mock.patch.object(lxdtop, "time", _Time(sleeps=1)):
                try:
                    events._loop()
                except StopLoop:
                    pass
        self.assertEqual(len(lxd.requests), 2)                     # connected twice
        # The same event on each connection: one line, counted twice.
        self.assertEqual([(e["action"], e["count"]) for e in events.feed], [("instance-started", 2)])

    def test_unexpected_json_shapes_reconnect(self):
        """SECURITY-REVIEW.md L-4: these used to end the listener thread for good.

        Valid JSON that isn't an event object raised AttributeError or
        TypeError, and deeply nested JSON RecursionError. _loop() caught none
        of them, so the panel kept saying "live" and no more events came. LXD
        itself always sends event objects, so this needs something other than
        LXD on the socket.
        """
        for payload in (b"[]", b'{"metadata": "x"}', b'{"metadata": {"source": null}}',
                        b"[" * 100000 + b"]" * 100000):
            with self.subTest(payload=payload[:30]), FakeLxd(script=[ws_frame(1, payload)]) as lxd:
                events = run_loop(lxd.path)
                self.assertIsNone(events.ended)                # the listener is still running
                self.assertEqual(events.status, "reconnecting")

    def test_an_impossible_frame_length_reconnects(self):
        """SECURITY-REVIEW.md L-4: a length of 2**63 raised OverflowError, and 2**40 MemoryError."""
        for length in (1 << 63, 1 << 40):
            with self.subTest(length=length), \
                    FakeLxd(script=[bytes([0x81, 127]) + length.to_bytes(8, "big")]) as lxd:
                events = run_loop(lxd.path)
                self.assertIsNone(events.ended)
                self.assertEqual(events.status, "reconnecting")


class RecordTest(unittest.TestCase):
    """_record(): one feed line per event, with repeats folded into a count."""

    def setUp(self):
        self.events = lxdtop.LxdEvents("/unused")
        self.clock = _Time()
        patcher = mock.patch.object(lxdtop, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, action="instance-exec", source="/1.0/instances/web", user="ci", **context):
        self.events._record(dict(metadata=dict(action=action, source=source, context=context,
                                               requestor=dict(username=user))))

    def test_a_repeat_within_ten_minutes_joins_its_line(self):
        self.record()
        self.clock.now += 60
        self.record(action="instance-started")
        self.clock.now += 60
        self.record()
        feed = self.events.feed
        self.assertEqual([(e["action"], e["count"]) for e in feed], [("instance-exec", 2), ("instance-started", 1)])
        self.assertEqual(feed[0]["t"], self.clock.now)          # moved to the top with the latest time

    def test_after_ten_minutes_it_is_a_new_line(self):
        self.record()
        self.clock.now += lxdtop.REPEAT_WINDOW
        self.record()
        self.assertEqual([e["count"] for e in self.events.feed], [1, 1])

    def test_a_different_user_or_detail_is_a_different_line(self):
        self.record(user="ci")
        self.record(user="bj")
        self.record(action="instance-file-pushed", path="/etc/hosts")
        self.record(action="instance-file-pushed", path="/etc/hostname")
        self.assertEqual(len(self.events.feed), 4)

    def test_fifty_lines_at_most(self):
        for i in range(60):
            self.record(source=f"/1.0/instances/c{i}")
        self.assertEqual(len(self.events.feed), 50)
        self.assertEqual(self.events.feed[0]["name"], "c59")

    def test_names_from_the_source_path(self):
        self.record(source="/1.0/instances/web/snapshots/snap%201")
        self.record(source="/1.0/instances/db/logs/lxc.log")
        self.record(source="")
        self.assertEqual([(e["name"], e["detail"]) for e in self.events.feed],
                         [("", ""), ("db", ""), ("web", "snap 1")])

    def test_a_name_field_wins_over_the_source(self):
        self.events._record(dict(metadata=dict(action="instance-renamed", source="/1.0/instances/new",
                                               name="old")))
        self.assertEqual(self.events.feed[0]["name"], "old")

    def test_events_without_metadata(self):
        self.events._record({})
        self.assertEqual(self.events.feed[0]["action"], "")

    def test_a_null_action_does_not_crash_the_screen(self):
        """SECURITY-REVIEW.md L-5: odd values here used to end lxdtop.

        _record() stored action, name and username as they came, and
        draw_events_panel() then called None.replace(), sliced a number or
        joined one, in the main thread. LXD always sends strings here, so
        this needs something other than LXD on the socket.
        """
        cases = {"action null": dict(action=None), "action a number": dict(action=5),
                 "name a number": dict(name=5), "name a list": dict(name=["web"]),
                 "username a number": dict(requestor=dict(username=7)),
                 "username null": dict(requestor=dict(username=None))}
        for label, fields in cases.items():
            with self.subTest(label):
                events = lxdtop.LxdEvents("/unused")
                events._record(dict(metadata=dict(dict(action="instance-exec", source="/1.0/instances/web"),
                                                  **fields)))
                entry = events.feed[0]
                for key in ("action", "name", "detail", "user"):
                    self.assertIsInstance(entry[key], str, key)
                lxdtop.draw_events_panel(lxdtop.TextCanvas(14, 54), 0, 0, 14, 54,
                                         types.SimpleNamespace(events=events))

if __name__ == "__main__":
    unittest.main()
