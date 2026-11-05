"""lxdtop only reads: a check of its source, so a change that adds a write has to change this test too.

CONTRIBUTING.md promises that lxdtop sends only GET requests to LXD, listens
to its event stream, reads files under /proc and /sys, and needs nothing but
the standard library. These tests walk lxdtop's syntax tree and fail on
anything that could break that promise: a new import, a file opened for
writing, an HTTP method other than GET, a call that changes files or starts
programs, or a socket other than LXD's unix socket. The wire-level checks
(what lxdtop actually sends) are in test_lxd_api.py and test_events.py.
"""

import ast
import re
import sys
import unittest

from tests.support import LXDTOP_PATH

with open(LXDTOP_PATH, encoding="utf-8") as _f:
    TREE = ast.parse(_f.read(), LXDTOP_PATH)

CALLS = [node for node in ast.walk(TREE) if isinstance(node, ast.Call)]


def dotted(node):
    """"os.path.exists" for the expression os.path.exists, or None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class ReadOnlyTest(unittest.TestCase):
    def test_imports_are_the_standard_library_modules_it_has_today(self):
        imported = set()
        for node in ast.walk(TREE):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertEqual(imported, {"argparse", "base64", "collections", "curses", "glob", "http.client",
                                    "json", "locale", "os", "socket", "threading", "time", "types",
                                    "unicodedata", "urllib.parse"},
                         "lxdtop's imports changed: check the new one is in the standard library and "
                         "doesn't let lxdtop write anything, then update this list")
        if hasattr(sys, "stdlib_module_names"):
            for name in imported:
                self.assertIn(name.split(".")[0], sys.stdlib_module_names)

    def test_files_are_opened_for_reading_only(self):
        opens = [call for call in CALLS if dotted(call.func) == "open"]
        self.assertTrue(opens)
        for call in opens:
            self.assertEqual(len(call.args), 1, f"line {call.lineno}: open() with a mode")
            self.assertNotIn("mode", {kw.arg for kw in call.keywords}, f"line {call.lineno}")

    def test_http_requests_are_get(self):
        requests = [call for call in CALLS if isinstance(call.func, ast.Attribute) and call.func.attr == "request"]
        self.assertTrue(requests)
        for call in requests:
            self.assertIsInstance(call.args[0], ast.Constant, f"line {call.lineno}")
            self.assertEqual(call.args[0].value, "GET", f"line {call.lineno}")

    def test_raw_http_is_the_event_stream_upgrade(self):
        # Any string that starts an HTTP request line: only the event stream's.
        methods = re.compile(r"^(GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS|CONNECT|TRACE) /")
        found = [node.value.split("\r\n")[0] for node in ast.walk(TREE)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str) and methods.match(node.value)]
        self.assertEqual(found, ["GET /1.0/events?type=lifecycle&all-projects=true HTTP/1.1"])

    def test_nothing_writes_files_or_starts_programs(self):
        allowed = {"os.path.join", "os.path.exists", "os.path.basename", "os.listdir", "os.statvfs",
                   "os.cpu_count", "os.sysconf", "os.urandom", "os.environ.get", "os.environ.setdefault"}
        for call in CALLS:
            name = dotted(call.func) or ""
            if name.startswith("os."):
                self.assertIn(name, allowed, f"line {call.lineno}: {name}()")
            self.assertNotIn(name, {"eval", "exec", "compile", "__import__"}, f"line {call.lineno}")

    def test_sockets_are_unix_sockets(self):
        sockets = [call for call in CALLS if dotted(call.func) == "socket.socket"]
        self.assertTrue(sockets)
        for call in sockets:
            self.assertEqual(dotted(call.args[0]), "socket.AF_UNIX", f"line {call.lineno}")

    def test_sendall_only_in_the_event_listener(self):
        listen = next(node for node in ast.walk(TREE) if isinstance(node, ast.FunctionDef) and node.name == "_listen")
        inside = {id(node) for node in ast.walk(listen)}
        sends = [call for call in CALLS if isinstance(call.func, ast.Attribute) and call.func.attr in
                 ("send", "sendall", "sendto", "sendmsg", "write")]
        self.assertEqual(len(sends), 2)                     # the upgrade request, and pongs
        for call in sends:
            self.assertIn(id(call), inside, f"line {call.lineno}")


if __name__ == "__main__":
    unittest.main()
