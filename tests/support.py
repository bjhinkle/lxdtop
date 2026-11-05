"""Helpers shared by the tests.

lxdtop is one file with no .py extension, so it can't be imported by name.
load_lxdtop() runs the file as a module called "lxdtop" instead. Its main()
only runs when the file is executed directly, so loading it starts nothing.

FakeHost stands in for the machine lxdtop watches: a temporary directory laid
out like /proc and /sys, filled from tests/fixtures/ or by the test itself.
FakeHost.installed() points lxdtop's file access (open, glob, os.listdir,
os.path.exists, os.statvfs) and its clock at that directory, inside the lxdtop
module only. lxdtop's own read() and parsing code then run unchanged on the
fixture files, and nothing reads the real /proc or /sys.
"""

import collections
import contextlib
import glob
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import types
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LXDTOP_PATH = os.path.join(ROOT, "lxdtop")
REMOTE_PATH = os.path.join(ROOT, "contrib", "lxdtop-remote")
FIXTURES = os.path.join(ROOT, "tests", "fixtures")
GiB = 1024 ** 3
MiB = 1024 ** 2


def load_lxdtop(name="lxdtop"):
    """Load the lxdtop script as a module. Each call returns a fresh copy."""
    loader = importlib.machinery.SourceFileLoader(name, LXDTOP_PATH)
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True          # no __pycache__ beside the script
    try:
        loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = dont_write
    return module


lxdtop = load_lxdtop()


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


def fixture_json(name):
    return json.loads(fixture(name))


@contextlib.contextmanager
def glyphs(table):
    """Draw with this glyph table (lxdtop.GLYPHS_UNICODE or GLYPHS_ASCII) for a while."""
    saved = lxdtop.G
    lxdtop.G = table
    try:
        yield
    finally:
        lxdtop.G = saved


def pid_stat(pid, comm, utime=0, stime=0, start=0, state="S"):
    """A /proc/<pid>/stat line in the kernel's format: 52 fields, the name in parentheses."""
    fields = [state, 1, pid, pid, 0, -1, 4194560, 1000, 0, 0, 0, utime, stime, 0, 0, 20, 0, 1, 0, start,
              123456789, 2048, 18446744073709551615] + [0] * 27
    return f"{pid} ({comm}) " + " ".join(str(f) for f in fields) + "\n"


def cpu_line(name, user=0, nice=0, system=0, idle=0, iowait=0, irq=0, softirq=0, steal=0):
    return f"{name} {user} {nice} {system} {idle} {iowait} {irq} {softirq} {steal} 0 0\n"


class _OsView:
    """The os module as lxdtop sees it while a FakeHost is installed."""

    def __init__(self, host):
        self._host = host
        self.path = _OsPathView(host)

    def listdir(self, path):
        return os.listdir(self._host.path(path))

    def statvfs(self, path):
        if path != "/":
            raise FileNotFoundError(path)
        total, used = self._host.disk
        return types.SimpleNamespace(f_frsize=4096, f_blocks=total // 4096, f_bfree=(total - used) // 4096)

    def cpu_count(self):
        return self._host.cpus

    def __getattr__(self, name):
        return getattr(os, name)


class _OsPathView:
    def __init__(self, host):
        self._host = host

    def exists(self, path):
        return os.path.exists(self._host.path(path))

    def __getattr__(self, name):
        return getattr(os.path, name)


class _TimeView:
    """The time module as lxdtop sees it: monotonic() is the fake host's clock."""

    def __init__(self, host):
        self._host = host

    def monotonic(self):
        return self._host.clock

    def __getattr__(self, name):
        import time
        return getattr(time, name)


class FakeHost:
    """A temporary directory that looks like the parts of /proc and /sys lxdtop reads."""

    def __init__(self, testcase=None):
        self.root = tempfile.mkdtemp(prefix="lxdtop-fakehost-")
        if testcase is not None:
            testcase.addCleanup(self.cleanup)
        self.cpus = 4
        self.disk = (100 * GiB, 40 * GiB)    # root filesystem: total, used
        self.clock = 1000.0                  # what time.monotonic() returns inside lxdtop
        for d in ("/proc", "/sys/fs/cgroup", "/sys/class/net", "/sys/class/hwmon"):
            self.mkdir(d)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def path(self, path):
        return self.root + path

    def mkdir(self, path):
        os.makedirs(self.path(path), exist_ok=True)

    def write(self, path, content):
        full = self.path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb" if isinstance(content, bytes) else "w") as f:
            f.write(content)

    def remove(self, path):
        full = self.path(path)
        if os.path.isdir(full):
            shutil.rmtree(full)
        elif os.path.exists(full):
            os.remove(full)

    def copy_fixture(self, name, path):
        self.write(path, fixture(name))

    # The files lxdtop reads, in the kernel's formats.

    def set_basics(self, uptime=123456.78, load="0.52 0.48 0.40 2/1234 5678"):
        self.write("/proc/uptime", f"{uptime} 456789.01\n")
        self.write("/proc/loadavg", load + "\n")
        self.copy_fixture("proc-meminfo.txt", "/proc/meminfo")

    def set_cpu_times(self, whole, threads):
        """whole and each thread: (user, system, idle), the other columns zero."""
        lines = [cpu_line("cpu ", user=whole[0], system=whole[1], idle=whole[2])]
        lines += [cpu_line(f"cpu{i}", user=u, system=s, idle=i_) for i, (u, s, i_) in enumerate(threads)]
        self.write("/proc/stat", "".join(lines) + "intr 0\nctxt 0\n")

    def add_container(self, cg, usage_usec, memory=512 * MiB, inactive_file=128 * MiB, pids=10,
                      io=(0, 0)):
        base = f"/sys/fs/cgroup/lxc.payload.{cg}"
        self.write(base + "/cpu.stat", f"usage_usec {usage_usec}\nuser_usec {usage_usec}\nsystem_usec 0\n")
        self.write(base + "/memory.current", f"{memory}\n")
        self.write(base + "/memory.stat", f"anon 1\nfile 2\ninactive_file {inactive_file}\nactive_file 0\n")
        self.write(base + "/pids.current", f"{pids}\n")
        self.write(base + "/io.stat", f"8:0 rbytes={io[0]} wbytes={io[1]} rios=1 wios=1 dbytes=0 dios=0\n")

    def add_process(self, pid, comm, cgroup="/user.slice/user-1000.slice/session-1.scope",
                    utime=0, stime=0, start=0):
        self.write(f"/proc/{pid}/stat", pid_stat(pid, comm, utime, stime, start))
        self.write(f"/proc/{pid}/cgroup", f"0::{cgroup}\n")

    def add_nic(self, name, rx, tx, physical=False, state="up"):
        base = f"/sys/class/net/{name}"
        self.write(base + "/statistics/rx_bytes", f"{rx}\n")
        self.write(base + "/statistics/tx_bytes", f"{tx}\n")
        self.write(base + "/operstate", state + "\n")
        if physical:
            self.mkdir(base + "/device")

    def add_objset(self, pool, objset, dataset, nread, nwritten):
        self.write(f"/proc/spl/kstat/zfs/{pool}/objset-{objset}",
                   "49 1 0x01 7 2160 5214446052 1101724096685\n"
                   "name                            type data\n"
                   f"dataset_name                    7    {dataset}\n"
                   f"writes                          4    10\n"
                   f"nwritten                        4    {nwritten}\n"
                   f"reads                           4    10\n"
                   f"nread                           4    {nread}\n")

    @contextlib.contextmanager
    def installed(self):
        """Point lxdtop's file access and clock at this fake host while the block runs."""
        root, real_open = self.root, open

        def fake_open(path, *args, **kwargs):
            return real_open(root + path, *args, **kwargs)

        def fake_glob(pattern):
            return sorted(p[len(root):] for p in glob.glob(glob.escape(root) + pattern))

        with mock.patch.object(lxdtop, "open", fake_open, create=True), \
                mock.patch.object(lxdtop, "glob", types.SimpleNamespace(glob=fake_glob)), \
                mock.patch.object(lxdtop, "os", _OsView(self)), \
                mock.patch.object(lxdtop, "time", _TimeView(self)):
            yield self


# --- A Monitor with chosen contents, for drawing tests --------------------------

HOST_READING = dict(
    cpu=37.5, threads=[12.0, 80.0, 45.0, 3.0], temp=54.25, mhz=2800.0,
    mem_total=64 * GiB, mem_free=20 * GiB, arc=8 * GiB, mem_used=36 * GiB,
    swap_total=8 * GiB, swap_used=1 * GiB, root_total=100 * GiB, root_used=40 * GiB,
    load=["0.52", "0.48", "0.40"], uptime=123456.0)


def row(name, cg=None, **fields):
    """A table row like Monitor.sample_instances() makes, with every reading filled in."""
    values = dict(mem=384 * MiB, tasks=12, top=("nginx", 25.0), up=7200.0, cpu=33.3,
                  rd=2 * MiB, wr=512 * 1024, net_in=1.25e6, net_out=125e3)
    values.update(fields)
    inst = dict(name=name, status=values.pop("state", "Running"), ipv4=values.pop("ip", "10.10.10.21"))
    return lxdtop.new_row(cg or name, inst, **values)


def monitor(rows=(), host=None, events=None, **attrs):
    """A Monitor holding the given rows and host reading, as if it had sampled them."""
    fake = FakeHost()
    try:
        with fake.installed():
            mon = lxdtop.Monitor(None, events)    # finds no sensors in the empty fake host
    finally:
        fake.cleanup()
    mon.ncpu = 4
    mon.rows = list(rows)
    mon.host = dict(HOST_READING if host is None else host)
    mon.server = dict(version="6.9", os="Ubuntu 24.04", kernel="6.8.0-45-generic")
    mon.pools = [("default", 120 * GiB, 900 * GiB)]
    mon.api_status = "ok"
    mon.nic_rates = {"eno1": (1.25e6, 2.5e5)}
    mon.net_in.extend([1.0e6, 1.5e6, 1.25e6])
    mon.net_out.extend([2.0e5, 3.0e5, 2.5e5])
    for r in mon.rows:
        if r["live"]:
            mon.cpu_history[r["cg"]] = collections.deque([r["cpu"]] * 30, maxlen=lxdtop.HISTORY)
    for _ in range(30):
        sample = {r["cg"]: r["cpu"] for r in mon.rows if r["live"] and r["cpu"]}
        sample[lxdtop.HOST] = 20.0
        mon.stack.append(sample)
    for name, value in attrs.items():
        setattr(mon, name, value)
    return mon


def ui(sort="cpu", interval=2):
    view = lxdtop.new_ui(interval)
    view.sort = sort
    return view


def render(mon, width=160, height=45, sort="cpu", interval=2):
    """Draw one frame as text, the way --dump does."""
    canvas = lxdtop.TextCanvas(height, width)
    with mock.patch.object(lxdtop.socket, "gethostname", return_value="lxdhost"):
        lxdtop.draw(canvas, mon, ui(sort, interval))
    return canvas


# --- A stand-in for LXD's unix socket -----------------------------------------

def ws_frame(opcode, payload=b"", final=True, mask=None, length=None):
    """One websocket frame as a server sends it (unmasked unless a mask is given)."""
    n = len(payload) if length is None else length
    head = bytes([(0x80 if final else 0) | opcode])
    mask_bit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mask_bit | n])
    elif n < 65536:
        head += bytes([mask_bit | 126]) + n.to_bytes(2, "big")
    else:
        head += bytes([mask_bit | 127]) + n.to_bytes(8, "big")
    if mask:
        head += mask
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return head + payload


def read_ws_frame(conn):
    """Read one frame a client sent: (final, opcode, masked, payload)."""
    def exactly(n):
        data = b""
        while len(data) < n:
            chunk = conn.recv(n - len(data))
            if not chunk:
                raise EOFError("client closed")
            data += chunk
        return data
    b0, b1 = exactly(2)
    size = b1 & 0x7F
    if size == 126:
        size = int.from_bytes(exactly(2), "big")
    elif size == 127:
        size = int.from_bytes(exactly(8), "big")
    mask = exactly(4) if b1 & 0x80 else b""
    payload = exactly(size)
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bool(b0 & 0x80), b0 & 0x0F, bool(mask), payload


class FakeLxd:
    """Answers lxdtop's requests on a unix socket in a temporary directory.

    routes maps a request target ("/1.0", "/1.0/instances?...") to
    (HTTP status, JSON body). A request for /1.0/events gets a websocket
    handshake (or `events_status` if that isn't 101), then each item of
    `script` in turn: bytes are sent as they are, READ_FRAME reads one frame
    from lxdtop into `received`, DRAIN keeps whatever else lxdtop sends in
    the next half second in `extra`, and a number pauses for that many
    seconds. The connection closes at the end of the script.

    Every request line is kept in `requests`, so a test can check exactly
    which methods and paths lxdtop used. `delays` maps a request target to
    the seconds to wait before answering it, as a slow LXD would.
    """

    READ_FRAME = "read frame"
    DRAIN = "drain"

    def __init__(self, routes=None, script=(), events_status=101, hold_open=False, delays=None):
        self.dir = tempfile.mkdtemp(prefix="lxdtop-fakelxd-")
        self.path = os.path.join(self.dir, "unix.socket")
        self.routes = dict(routes or {})
        self.delays = dict(delays or {})
        self.script = list(script)
        self.events_status = events_status
        self.hold_open = hold_open
        self.requests = []          # (method, target, headers)
        self.received = []          # frames lxdtop sent after the handshake
        self.extra = b""            # anything else it sent, if the script drains
        self.closed = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(8)
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.closed.set()
        try:
            # Wake accept() so the thread can finish.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(self.path)
        except OSError:
            pass
        self.thread.join(5)
        self.listener.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _serve(self):
        while not self.closed.is_set():
            conn, _ = self.listener.accept()
            if self.closed.is_set():
                conn.close()
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        with conn:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                head += chunk
            request_line, *header_lines = head.split(b"\r\n\r\n", 1)[0].decode().split("\r\n")
            method, target, _ = request_line.split(" ", 2)
            headers = {k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in header_lines)}
            self.requests.append((method, target, headers))
            if target.startswith("/1.0/events"):
                self._events(conn)
            else:
                time.sleep(self.delays.get(target, 0))
                status, body = self.routes.get(target, (404, {"type": "error", "error": "not found",
                                                              "error_code": 404}))
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                conn.sendall(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                             f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)

    def _events(self, conn):
        if self.events_status != 101:
            body = json.dumps({"type": "error", "error": "not authorized", "error_code": 403}).encode()
            conn.sendall(f"HTTP/1.1 {self.events_status} Forbidden\r\nContent-Length: {len(body)}\r\n"
                         "Connection: close\r\n\r\n".encode() + body)
            return
        conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                     b"Sec-WebSocket-Accept: unchecked\r\n\r\n")
        for item in self.script:
            if item == self.READ_FRAME:
                try:
                    self.received.append(read_ws_frame(conn))
                except (EOFError, OSError):
                    return
            elif item == self.DRAIN:
                conn.settimeout(0.5)
                try:
                    while chunk := conn.recv(4096):
                        self.extra += chunk
                except OSError:
                    pass
                conn.settimeout(None)
            elif isinstance(item, (int, float)):
                self.closed.wait(item)
            else:
                conn.sendall(item)
        if self.hold_open:
            self.closed.wait(30)


def lxd_routes(**overrides):
    """The GET answers a small LXD host gives, from the fixtures."""
    routes = {
        "/1.0": (200, fixture_json("lxd-server.json")),
        "/1.0/instances?recursion=2&all-projects=true": (200, fixture_json("lxd-instances.json")),
        "/1.0/storage-pools?recursion=1": (200, fixture_json("lxd-storage-pools.json")),
        "/1.0/storage-pools/default/resources": (200, fixture_json("lxd-pool-resources.json")),
        "/1.0/storage-pools/scratch/resources": (200, {"type": "sync", "metadata": {"space": {"used": 0,
                                                                                           "total": 0}}}),
    }
    routes.update(overrides)
    return routes


def lifecycle_events():
    """The fixture lifecycle events, as dicts."""
    return [json.loads(line) for line in fixture("lxd-events.jsonl").splitlines() if line.strip()]
