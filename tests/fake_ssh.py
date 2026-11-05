"""A stand-in for OpenSSH's ssh client, for testing contrib/lxdtop-remote without a network.

test_remote.py puts this on the PATH as "ssh". It reads its arguments the way
OpenSSH does: getopt with ssh's own option letters, stopping at "--" or at the
first argument that isn't an option (the destination); after the destination
it reads options again, unless "--" came first. It appends what it saw to
$FAKE_SSH_LOG, one JSON line per run, then acts out what lxdtop-remote relies on:

  ssh -G <host>           print "user" and "hostname" lines, as ssh -G does
  ssh -o ProxyCommand=X   run X with sh -c, as ssh does before it connects,
                          then give up as ssh would. OpenSSH 9.6 and later
                          first refuse destinations containing shell
                          characters; older versions don't, and neither does
                          this stand-in.
  ssh <dest> <words...>   join the words with spaces, as ssh does, and run the
                          result with the login shell's -c in $FAKE_SSH_REMOTE_HOME,
                          which stands in for the home directory on the host. The
                          login shell is $FAKE_SSH_LOGIN_SHELL (default sh), and
                          $FAKE_SSH_REMOTE_PATH, if set, is the host's PATH. With
                          FAKE_SSH_DRY set, only record it.

Two things that can happen to what ssh sends the host on its standard input:

  FAKE_SSH_FLIP_BYTE=N    byte N arrives changed.
  FAKE_SSH_DROP_AFTER=N   the connection drops after N bytes. The host's sshd
                          then closes the command's pipes: its input ends there,
                          and nothing it prints comes back, but it keeps running,
                          because sshd doesn't stop a command that has no
                          terminal. This waits for it, so a test sees what it
                          left behind, then fails as ssh does, with status 255.
  FAKE_SSH_STALL_AFTER=N  the connection goes quiet after N bytes, as one that
                          dropped without a word: nothing more reaches the host,
                          and nothing it prints comes back, but its input stays
                          open, because sshd hasn't noticed. This waits for the
                          command to end, for FAKE_SSH_STALL_WAIT seconds at most
                          (default 20), then stops it. It writes down in
                          $FAKE_SSH_LOG.host whether the command ended by itself,
                          what it printed, and whether anything still reads its
                          input, then fails as ssh does when it gives up, with
                          status 255. With FAKE_SSH_RESUME=PATH, the connection
                          only pauses: the rest arrives once PATH exists, and the
                          command's output comes back as usual.
"""

import getopt
import json
import os
import subprocess
import sys
import tempfile
import time

OPTIONS = "1246ab:c:e:fgi:kl:m:no:p:qstvxAB:CD:E:F:GI:J:KL:MNO:P:Q:R:S:TVw:W:XYy"   # from OpenSSH's ssh.c


def parse(argv):
    try:
        opts, rest = getopt.getopt(argv, OPTIONS)
        terminated = len(rest) < len(argv) and argv[len(argv) - len(rest) - 1] == "--"
        destination, words = (rest[0], rest[1:]) if rest else (None, [])
        if destination is not None and words and not terminated:
            more, words = getopt.getopt(words, OPTIONS)
            opts += more
    except getopt.GetoptError as err:
        return None, None, None, str(err)
    return opts, destination, words, None


def main():
    opts, destination, words, error = parse(sys.argv[1:])
    with open(os.environ["FAKE_SSH_LOG"], "a") as log:
        log.write(json.dumps(dict(argv=sys.argv[1:], options=opts, destination=destination, words=words,
                                  error=error)) + "\n")
    if error or destination is None:
        print(f"usage: ssh [options] destination [command] ({error or 'no destination'})", file=sys.stderr)
        return 255
    user, _, host = destination.rpartition("@")
    if ("-G", "") in opts:
        configured = os.environ.get("FAKE_SSH_CONFIGURED", "").split()
        user = user or (os.environ.get("FAKE_SSH_CONFIG_USER", "admin") if host in configured
                        else os.environ.get("FAKE_SSH_DEFAULT_USER", "tester"))
        print(f"user {user}\nhostname {host.lower()}")
        return 0
    for name, value in opts:
        key, _, command = value.partition("=")
        if name == "-o" and key.lower() == "proxycommand":
            subprocess.run(["sh", "-c", command])
            print("Connection closed by UNKNOWN port 65535", file=sys.stderr)
            return 255
    command = " ".join(words)
    if os.environ.get("FAKE_SSH_DRY") or not command:
        return 0
    home = os.environ["FAKE_SSH_REMOTE_HOME"]
    env = dict(os.environ, HOME=home, PATH=os.environ.get("FAKE_SSH_REMOTE_PATH", os.environ.get("PATH", "")))
    host = [os.environ.get("FAKE_SSH_LOGIN_SHELL", "sh"), "-c", command]
    flip, drop = os.environ.get("FAKE_SSH_FLIP_BYTE"), os.environ.get("FAKE_SSH_DROP_AFTER")
    stall = os.environ.get("FAKE_SSH_STALL_AFTER")
    if flip is None and drop is None and stall is None:
        return subprocess.run(host, cwd=home, env=env).returncode
    data = bytearray(sys.stdin.buffer.read())
    if flip is not None:
        data[int(flip)] ^= 1
    if stall is not None:
        return stalled(host, home, env, data, int(stall), destination)
    if drop is None:
        return subprocess.run(host, cwd=home, env=env, input=data).returncode
    pipe = subprocess.PIPE
    proc = subprocess.Popen(host, cwd=home, env=env, stdin=pipe, stdout=pipe, stderr=pipe)
    try:
        proc.stdin.write(data[:int(drop)])
        proc.stdin.close()
    except BrokenPipeError:                 # the command stopped reading
        pass
    proc.stdout.close()
    proc.stderr.close()
    proc.wait()
    print("client_loop: send disconnect: Broken pipe", file=sys.stderr)
    return 255


def stalled(host, home, env, data, after, destination):
    """The connection goes quiet after this many bytes: see FAKE_SSH_STALL_AFTER above."""
    resume = os.environ.get("FAKE_SSH_RESUME")
    if resume:
        proc = subprocess.Popen(host, cwd=home, env=env, stdin=subprocess.PIPE, bufsize=0)
        proc.stdin.write(data[:after])
        for _ in range(6000):                   # a minute at most
            if os.path.exists(resume):
                break
            time.sleep(0.01)
        proc.stdin.write(data[after:])
        proc.stdin.close()
        return proc.wait()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(host, cwd=home, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err, bufsize=0)
        proc.stdin.write(data[:after])
        try:
            proc.wait(timeout=float(os.environ.get("FAKE_SSH_STALL_WAIT", "20")))
            ended = True
        except subprocess.TimeoutExpired:
            ended = False
            proc.kill()
            proc.wait()
        try:
            proc.stdin.write(b"x")              # fails if nothing reads the input any more
            read = True
        except BrokenPipeError:
            read = False
        try:
            proc.stdin.close()                  # an input still read ends now
        except BrokenPipeError:
            pass
        out.seek(0)
        err.seek(0)
        with open(os.environ["FAKE_SSH_LOG"] + ".host", "w") as log:
            json.dump(dict(ended=ended, input_read=read, stdout=out.read().decode(), stderr=err.read().decode()), log)
    print(f"Read from remote host {destination.rpartition('@')[2]}: Operation timed out", file=sys.stderr)
    return 255


if __name__ == "__main__":
    sys.exit(main())
