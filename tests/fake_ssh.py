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
                          result with sh -c in $FAKE_SSH_REMOTE_HOME, which
                          stands in for the login shell on the host. With
                          FAKE_SSH_DRY set, only record it.
"""

import getopt
import json
import os
import subprocess
import sys

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
    return subprocess.run(["sh", "-c", command], cwd=home, env=dict(os.environ, HOME=home)).returncode


if __name__ == "__main__":
    sys.exit(main())
