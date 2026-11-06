"""A stand-in for sudo, for testing `lxdtop-remote install --system` and the README's install without root.

The tests put this on the PATH as "sudo". It takes sudo's -n, then a command, and
appends what it was asked to $FAKE_SUDO_LOG, one JSON line per run. Nothing runs as
root here: the command runs as the same user. What stands in for root's power is
$FAKE_SUDO_ROOT/usr/local/bin, the tests' stand-in for /usr/local/bin. The tests
keep it unwritable, and this makes it writable only while the command runs, so
anything that writes there without sudo fails, as it would on a real host.
tests/fake_ssh.py points a command's /usr/local/bin there. HOME is the stand-in
root's own, $FAKE_SUDO_ROOT/root, which doesn't exist, as sudo sets HOME to
root's; the rest of the environment is kept, for the tests' other stand-ins.

How it answers, by $FAKE_SUDO:

  (unset)    sudo needs no password: it runs the command, with -n or without.
  password   sudo wants a password. With -n it refuses, as sudo does. Without,
             it asks on the terminal, takes the answer as right, and runs the
             command, when there is a terminal: tests/fake_ssh.py says so with
             $FAKE_SSH_TTY. Without one, it refuses, as sudo does.
  refuse     this account may not use sudo. With -n it refuses as for a
             password, as sudo does before it knows who asks. Without -n, it
             asks for the password, then says so.

With $FAKE_SUDO_CHANGE set, it adds a line to the file it is given after `sh -c
SCRIPT NAME`, before it runs the command, as if that file changed between the
checks and the install.
"""

import json
import os
import subprocess
import sys


def main():
    args = sys.argv[1:]
    no_password = args[:1] == ["-n"]
    command = args[1:] if no_password else args
    terminal = bool(os.environ.get("FAKE_SSH_TTY"))
    with open(os.environ["FAKE_SUDO_LOG"], "a") as log:
        log.write(json.dumps(dict(argv=args, terminal=terminal)) + "\n")
    answer = os.environ.get("FAKE_SUDO", "")
    if answer in ("password", "refuse"):
        if no_password:
            print("sudo: a password is required", file=sys.stderr)
            return 1
        if not terminal:
            print("sudo: a terminal is required to read the password; either use the -S option to read from "
                  "standard input or configure an askpass helper\nsudo: a password is required", file=sys.stderr)
            return 1
        print("[sudo] password for me: ", file=sys.stderr)
        if answer == "refuse":
            print("me is not in the sudoers file.", file=sys.stderr)
            return 1
    root = os.environ["FAKE_SUDO_ROOT"]
    system = os.path.join(root, "usr", "local", "bin")
    if os.environ.get("FAKE_SUDO_CHANGE") and command[:2] == ["sh", "-c"]:
        with open(command[4], "a") as f:
            f.write("# changed\n")
    os.chmod(system, 0o755)
    try:
        return subprocess.run(command, env=dict(os.environ, HOME=os.path.join(root, "root"))).returncode
    finally:
        os.chmod(system, 0o555)


if __name__ == "__main__":
    sys.exit(main())
