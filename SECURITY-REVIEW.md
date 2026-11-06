# Security review: lxdtop 1.4.0 and lxdtop-remote

Reviewed: `lxdtop` and `contrib/lxdtop-remote` at commit `cf50ad6` (2026-10-07). **Line numbers below refer to that commit.** Fixes made since then have moved later lines in `lxdtop` down by up to 268 (the ops signals added most of that, after the review was finished), and each finding also names its function. In `lxdtop-remote`, lines after `install` have moved down by up to 41 (the N-7 fix), and earlier ones by 2 or 3.

lxdtop runs with access to LXD's socket, which is equivalent to root on the host, and it draws text that people inside containers choose. So this review asks four things: does it only read, can container-chosen text break out of its place on the terminal, can `lxdtop-remote` be made to run something it shouldn't, and what happens when the event stream is slow, closed or malformed.

## Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| [L-1](#l-1-a-process-name-that-isnt-utf-8-ends-lxdtop) | A process name that isn't valid UTF-8 ends lxdtop | Medium | Fixed in `5eb660b` |
| [L-2](#l-2---dump-prints-escape-sequences-from-containers-unchanged) | `--dump` prints escape sequences from containers unchanged | Medium | Fixed in `8726a25` |
| [L-3](#l-3-in-the-live-view-r-b-and-n-move-the-cursor) | In the live view, `\r`, `\b` and `\n` in a name move the cursor, and format characters pass through | Low | Fixed in `8726a25`; wide characters in `681c31e` |
| [L-4](#l-4-unexpected-input-ends-the-event-listener-for-good) | Unexpected input ends the event listener for good, while the panel still says "live" | Low | Fixed in `85b8719` |
| [L-5](#l-5-a-null-or-non-string-event-field-crashes-lxdtop) | A `null` or non-string event field crashes lxdtop when it is drawn | Low | Fixed in `a93f240` |
| [L-6](#l-6-no-timeouts-on-the-event-socket) | No timeouts on the event socket, so a stalled LXD still looks "live" | Low | Fixed in `a057527` |
| [L-7](#l-7-interface-names-from-lxd-go-into-a-path-unchecked) | Interface names from LXD go into a `/sys` path unchecked | Low | Fixed in `c9c129a` |
| [L-8](#l-8-a-delegated-zfs-dataset-can-take-another-instances-disk-counters) | With `zfs.delegate`, a container can take over another instance's disk counters | Low | Fixed in `fe566a4`, `6bd38d7`, `aebe798` and `3de9844` |
| [L-9](#l-9-unexpected-json-from-the-api-stops-lxdtop-or-its-refreshes) | Unexpected JSON from the API ends lxdtop at startup, or stops its refreshes while the footer says "ok" | Low | Fixed in `daa158d` |
| [R-1](#r-1-a-host-or-username-starting-with---is-read-by-ssh-as-an-option) | `lxdtop-remote`: a host or username starting with `-` is read by ssh as an option | Low | Fixed in `6f8420c` |
| [R-2](#r-2-lxdtop-options-are-parsed-again-by-the-hosts-shell) | `lxdtop-remote`: lxdtop options are parsed again by the host's shell | Low | Fixed in `12e82db` |
| [N-1 to N-10](#notes) | Notes with no security impact | Info | N-1 fixed in `1bba705`, N-7 in `016cba3`, N-8 in `41f69d2`, N-9 in `2013f92`, N-10 in `b6ac6ab` and `9091fb1`. No change for the rest |

Nothing reached High. Five bugs that aren't security issues were also fixed: lxdtop exited at startup when `LANG` named a locale the host doesn't have ([N-8](#n-8-not-security-lxdtop-exited-when-the-locale-wasnt-installed), `41f69d2`), the event feed left out every project but the default one ([N-1](#n-1-not-security-events-came-from-the-default-project-only), `1bba705`), one storage pool LXD couldn't read stopped every refresh of LXD's data ([N-9](#n-9-not-security-one-storage-pool-lxd-couldnt-read-stopped-every-refresh), `2013f92`), on an LXD cluster, lxdtop showed every member's instances and events, not just its own ([N-10](#n-10-not-security-on-an-lxd-cluster-every-members-instances-and-events-showed), `b6ac6ab` and `9091fb1`), and a connection dropped during `lxdtop-remote install` left a broken `~/bin/lxdtop` ([N-7](#n-7-install-overwrote-binlxdtop-in-place), `016cba3`).

**Severity scale.** *High*: someone could change the host, run code as the lxdtop user, or read secrets. *Medium*: an unprivileged process in any container can stop lxdtop or send control sequences to the operator's terminal. *Low*: needs an unusual setup, a non-default option, a second foothold, or input only the operator or root controls, or it only misleads the display. *Info*: no security impact.

**The review is finished, and nothing is left open.** Every finding here is fixed, and the test suite has no expected failures left. No daemon was available here, so the fixes were checked against a real host afterwards: that host (LXD 6.7, standalone, default project only) confirmed the ZFS disk mapping ([L-8](#l-8-a-delegated-zfs-dataset-can-take-another-instances-disk-counters)). The event timeout ([L-6](#l-6-no-timeouts-on-the-event-socket)) is a passive check there: its desk screen's event panel should never flip to `reconnecting`. Events from other projects ([N-1](#n-1-not-security-events-came-from-the-default-project-only)) are not checkable on that host, which has only the default project, and the cluster fix ([N-10](#n-10-not-security-on-an-lxd-cluster-every-members-instances-and-events-showed)) needs an LXD cluster, and none was available.

**Method.** I read all of both files. Each claim below was then checked by running something, either a test in `tests/` or a one-off run described under *How confirmed*, on Ubuntu 24.04 (Linux 6.18, Python 3.10 to 3.13, ncurses 6.5, OpenSSH 9.6p1). CI also runs the suite on Python 3.10, 3.12 and 3.15. No LXD daemon was available, so LXD's side (its validation rules, and what a VM's agent can report) comes from LXD's documentation and is marked where I couldn't check it. For N-1 and N-9, I read LXD's source on its `stable-5.21` and `main` branches, which cover LXD 5.21 and 6.

## 1. Does lxdtop only read?

**Yes.** It sends only GET requests and the event-stream upgrade, opens files only for reading, and never writes a file, starts a process or changes the host.

### Every LXD API call

| # | Method | Path | Line | When |
|---|---|---|---|---|
| 1 | GET | `/1.0/instances?recursion=2&all-projects=true` | 463 | At startup (1524), every 15 s, and once early when a container appears that LXD hasn't described yet (722) |
| 2 | GET | `/1.0` | 481 | Every fourth refresh, so once a minute. Since `b6ac6ab` it comes before 1, which it filters on a cluster ([N-10](#n-10-not-security-on-an-lxd-cluster-every-members-instances-and-events-showed)) |
| 3 | GET | `/1.0/storage-pools?recursion=1` | 485 | With 2 |
| 4 | GET | `/1.0/storage-pools/<pool>/resources` | 486–487 | With 2, once per pool. The name is URL-quoted. A pool LXD can't read is left out since `2013f92` ([N-9](#n-9-not-security-one-storage-pool-lxd-couldnt-read-stopped-every-refresh)) |
| 5 | GET with `Upgrade: websocket` | `/1.0/events?type=lifecycle&all-projects=true` | 557 | At startup, and 5 s after any disconnect (552). `&all-projects=true` since `1bba705` ([N-1](#n-1-not-security-events-came-from-the-default-project-only)) |

Calls 1–4 all go through `LxdApi.get()`, whose only request is `conn.request("GET", path)` (452). Call 5 is written by hand on the unix socket (557–559). After the upgrade, the only thing lxdtop sends is pong frames answering LXD's pings (582–585), which are part of the websocket protocol rather than API calls.

**Flagged: nothing.** There is no POST, PUT, PATCH or DELETE, and no operation, exec, console or file endpoint.

**How confirmed.**
- `tests/test_lxd_api.py`, `test_only_get_requests_to_these_paths`: runs `LxdApi.fetch()` five times against a fake LXD on a unix socket that records every request line. Every request was a GET, to exactly the paths above.
- `tests/test_events.py`, `test_the_upgrade_request` and `test_pongs_are_the_only_thing_sent_after_the_handshake`: the upgrade is `GET /1.0/events?type=lifecycle&all-projects=true`, and with events and pings interleaved, lxdtop sent exactly two masked pongs and nothing else.
- `tests/test_readonly.py` walks lxdtop's syntax tree, so any future change that breaks this has to change that test too. It checks that every `.request()` is a GET, that the only HTTP request line in the source is the events one, that `sendall` appears only in `LxdEvents._listen`, and that every socket is `AF_UNIX`.

### Files, processes, everything else

`read()` (171–178) is the only place lxdtop opens files, and it passes no mode, so files open read-only. lxdtop starts no programs. The only environment change is `TZ`, in its own process, and only if `TIMEZONE` is edited (1514).

**How confirmed.**
- One-off run: `lxdtop --dump` on this machine's real `/proc` and `/sys`, under a Python audit hook that recorded every `open()` with its flags, every socket call, process start and file-system change. It made 98 opens, all read-only (88 under `/proc`, 5 under `/sys`, 5 of Python's own), and nothing else.
- `tests/test_readonly.py` fails if `open()` is given a mode, if any `os.*` call outside a short read-only list appears (`os.remove`, `os.system` and the like), if `eval`/`exec` appear, or if the imports change from today's standard-library set.

## 2. Strings a container controls, on the terminal

| Text | Who chooses it | Read at | Drawn at |
|---|---|---|---|
| Busiest process name | Any process in any container, through `prctl`, `/proc/self/comm` or the name of the file it runs. Up to 15 bytes, any bytes | 397, 401 | 1337 |
| Sub-cgroup names | A container's root | 332. Only the first part, which LXD names, is used (333–335) | Not drawn |
| VM IPv4 address | The VM guest: LXD takes a VM's network state from the lxd-agent inside it | 475 | 1348 |
| Snapshot names, `lxc file` paths, exec command lines (only with `--show-commands`), requestor names | Whoever calls LXD's API, including users confined to a project | 594–609 | 1242–1245 |
| Instance and project names | LXD allows only hostname-style instance names (letters, digits, dashes) | 463–466 | Several |

Before this branch, nothing between those reads and the screen filtered the text: `Canvas.put()` (824) passed it straight to curses, or, for `--dump`, to stdout.

### L-1 A process name that isn't UTF-8 ends lxdtop

**Medium. Fixed in `5eb660b`.**

- **Where.** `read()`, line 175: `open(path)` uses the strict decoder, and the `except` at 177 catches only `OSError`. `UnicodeDecodeError` is a `ValueError`, so it escapes from `ProcessTable.sample()` (397) and `container_of()` (332) and ends lxdtop. `read_int()` happens to catch it (184–186), but these callers don't.
- **Impact.** Any user in any container can stop the monitor by naming a process with invalid UTF-8, for example by writing `b"\xff"` to `/proc/self/comm`. Under `examples/lxdtop-console.service` (`Restart=always`), lxdtop crash-loops every 3 s for as long as that process runs. A container's root can do the same with a sub-cgroup name, which shows up in `/proc/<pid>/cgroup`.
- **How confirmed.** One-off run on the real kernel: a child process wrote `b"\xff\xfeevil"` to its own `/proc/self/comm`. `read("/proc/<pid>/stat")` then raised `UnicodeDecodeError`, and so did `ProcessTable.sample()`, under Python 3.12 and 3.13 with `LANG=C.UTF-8` and in UTF-8 mode. In the suite, `tests/test_untrusted_text.py`, `NotUtf8Test`, has three tests that error on `cf50ad6` and pass after the fix.
- **Smallest fix (applied).** One argument: `open(path, errors="backslashreplace")`. The process now shows as `\xff\xfeevil`, visible rather than hidden or fatal.

### L-2 `--dump` prints escape sequences from containers unchanged

**Medium. Fixed in `8726a25`.**

- **Where.** `TextCanvas._write()` (863) stores each character as given, and `main()` prints the frame (1539).
- **Impact.** A process named `\e]0;owned\a` (10 bytes, which fits) or `\e[8m` (hide everything after it) sends that sequence to whoever runs `lxdtop --dump`, or to whoever later runs `cat` on a saved dump. What a sequence can do depends on the terminal: retitle the window, hide or rewrite output, and on terminals that allow it, write the clipboard (OSC 52). A VM guest can do the same with a much longer string through its reported address (1348), and an API user through snapshot names and file paths.
- **How confirmed.** One-off run: `TextCanvas.put()` of a name containing `\e]0;pwned\a\e[8m`, then `text()`, returned the raw ESC and BEL bytes. In the suite, `ControlCharacterTest.test_a_process_name_in_dump_output` and `test_event_text_and_addresses` fail on `cf50ad6`, for plain and `--color` output alike.
- **Smallest fix (applied).** In `Canvas.put()`, which every string on screen passes through, replace any character that isn't printable with `?`, one for one so columns keep their width:

  ```python
  if not text.isprintable():
      text = "".join(ch if ch.isprintable() else "?" for ch in text)
  ```

  `str.isprintable()` is false for C0 and C1 controls, DEL, and Unicode format and separator characters, and true for every glyph lxdtop draws, including box drawing, blocks and `● ■ · …`. A test checks both glyph tables pass through unchanged.

### L-3 In the live view, `\r`, `\b` and `\n` move the cursor

**Low. Fixed in `8726a25`, the same change as L-2.**

- **Where.** `CursesCanvas._write()` (849) hands text to `addstr`.
- **Impact.** ncurses neutralises most control characters: ESC shows as `^[`, DEL as `^?`, and C1 controls (U+0080 to U+009F) are dropped, so no raw escape sequence reaches the terminal in the live view. But curses acts on `\r` (back to column 0), `\b` and `\n` itself. A process named `\r` plus some text can therefore overwrite the start of its own row, including the dot and the NAME column, so that it appears to be a different container. Unicode format characters such as U+202E (right-to-left override) are written to the terminal as they are.
- **How confirmed.** One-off run under real curses in a pseudo-terminal: `addstr(2, 20, "abc\rXY")` left `XY` in columns 0–1 of row 2; `\b\b` overwrote earlier cells; ESC became `^[`; U+009B and U+009D were not sent; U+202E was. After the fix, the same run through lxdtop's `CursesCanvas` gives `abc?XY` in place, and U+202E is not sent. In the suite, `ControlCharacterTest.test_curses_never_gets_them` and `test_a_carriage_return_in_a_process_name_stays_in_its_column` fail on `cf50ad6`.
- **Smallest fix (applied).** The L-2 change.

Wide characters (CJK, emoji) are printable but take two cells, and combining ones none, so they shifted the rest of a row and misaligned the columns after them. That was cosmetic, with no control effect. Fixed in `681c31e`: `Canvas.put()` shows any character that isn't one cell wide as `?`, after composing what composes (NFC). Ubuntu's console font has no CJK or emoji glyphs, and terminals disagree on emoji widths, so one cell per character is the only rule that lines up everywhere. `test_wide_and_combining_characters_take_one_cell_each` and `test_a_wide_process_name_keeps_the_columns_in_place` fail before it.

## 3. contrib/lxdtop-remote

Where `lxdtop-remote` takes input from: the command line, `~/.config/lxdtop/remote.conf` (which it runs as shell code, line 24), the username prompt (48), and `~/.config/lxdtop/users`. All of them belong to the person running it, so it has no trust boundary inside it. The risk is a host or option string coming from somewhere else, such as a script, an inventory or a pasted list, and being interpreted rather than passed along.

### R-1 A host or username starting with `-` is read by ssh as an option

**Low. Fixed in `6f8420c`.**

- **Where.** Lines 41 and 43 (`ssh -G "$1"`), 63 (`exec ssh "$dest" …`, for `screen`), 70 (`ssh "$dest" …`, for `install`), and 85 (`ssh -t "$dest" …`). `install` (65–67) and `screen` (58) take their host from `$2` unchecked. The main command skips a first argument starting with `-` (72), but a username typed at the prompt goes out as `name@host` (53).
- **Impact.** `lxdtop-remote install '-oProxyCommand=CMD'` makes ssh run CMD on the local machine. OpenSSH 9.6 and later refuse the connection before running it, but only by accident: once the option has been taken, the next argument, the remote command, becomes the host name, and 9.6 rejects host names that contain spaces. OpenSSH before 9.6, including Ubuntu 22.04's 8.9, runs CMD.
- **How confirmed.** With the real OpenSSH 9.6p1: `lxdtop-remote install '-oProxyCommand=touch /tmp/x'` printed `hostname contains invalid characters`, which shows the argument was taken as an option, and `/tmp/x` was not created. In the suite, `tests/fake_ssh.py` parses arguments with getopt and ssh's own option letters, and runs a ProxyCommand the way OpenSSH before 9.6 does. `test_a_host_or_user_starting_with_a_dash_is_not_an_ssh_option` fails on `cf50ad6` for `install`, `screen` and a typed username.
- **Smallest fix (applied).** `--` before the destination in those five calls. Checked that real OpenSSH 9.6 gives the same `ssh -G -- host` and `ssh -G -- user@host` output as without `--`.

### R-2 lxdtop options are parsed again by the host's shell

**Low. Fixed in `12e82db`.**

- **Where.** Line 85: `ssh -t "$dest" "$run…\"\$p\"" "$@"`.
- **Impact.** ssh joins the command words with spaces and hands the result to the login shell on the host, which parses it again. So `--socket "/path with space/unix.socket"` arrived as two words, an apostrophe was a syntax error, `*` expanded in the remote home directory, and `--interval "2;cmd"` ran `cmd` on the host as the logged-in user.
- **How confirmed.** In the suite, `test_options_reach_lxdtop_exactly_as_given` uses the stand-in ssh, which does what OpenSSH does: it joins the words with spaces and runs them with `sh -c` in a fake remote home. It fails on `cf50ad6` with "Unterminated quoted string". In a one-off run before the fix, `;touch` created its file and the socket path was split in two.
- **Smallest fix (applied).** Quote each option in single quotes (`'` becomes `'\''`) before joining, in a two-line loop. Checked under `dash` and `bash --posix` (macOS's `/bin/sh`): `it's`, `$HOME`, `*`, an empty string and `a b` all arrive unchanged.

### ShellCheck

ShellCheck 0.9.0 reported three notes, all on deliberate code. SC1091 is the `. remote.conf` at 24. SC2086 is `set -- $SCREEN_VTS` at 61, splitting `"1 8"` into two numbers. SC2016 is the single-quoted `run=` at 84, which is meant to expand on the host. Each now has an inline directive with its reason (`29f57f7`), and CI runs `shellcheck` at its default severity. Lowering CI to `--severity=warning` instead would also hide new unquoted variables, since ShellCheck counts SC2086 as a note too.

### Added after the review: `install --system` uses sudo on the host

`lxdtop-remote install --system <host>` installs one copy for every account, `/usr/local/bin/lxdtop`, owned by root. It is the only part of `lxdtop-remote` that does anything as root, through sudo on the host, and it is built so that root installs only what was checked:

- **Everything before sudo runs as you.** The copy arrives over the one ssh connection in a new file in your home directory, made by `mktemp`, and gets plain `install`'s checks: its SHA-256 must match your copy's, and its `--version` must print lxdtop's version. Root never runs the copy.
- **Root runs fixed code.** The part sudo runs is text in `lxdtop-remote`, given to `sudo sh -c`. No host, user name, option or setting goes into it as code. It gets two arguments: the checked file's path, made by `mktemp` on the host, and the SHA-256, which `lxdtop-remote` checks is 64 hex digits before it connects.
- **Root checks its own copy.** It copies the checked file to a new file beside `/usr/local/bin/lxdtop`, made by `mktemp` as root, which no other account can change, and checks that file's SHA-256 before it flushes it and renames it into place. A change to the checked file between the checks and sudo is refused, not installed.
- **The password stays with sudo.** The connection that carries the copy has no terminal for sudo to ask on, so where sudo wants a password, a second login with `ssh -t` runs only the sudo part, and sudo asks there. `lxdtop-remote` never sees the password. It passes the second login only the last six characters of the checked file's name, after checking they are letters and digits, and that login removes the file whatever happens.
- **A refusal changes nothing.** If the host has no sudo, sudo refuses, or a check fails, `/usr/local/bin/lxdtop` stays as it was and the new files are removed. A file left by an install stopped outright is removed by a later `--system` install once it's two days old.

How confirmed: `SystemInstallTest` in `tests/test_remote.py`, with dash, bash and BusyBox as the login shell, and a stand-in sudo, `tests/fake_sudo.py`, under which the stand-in `/usr/local/bin` can be written only while sudo runs a command. Not yet run with a real sudo on a real host.

## 4. The event websocket: slow, closed or malformed

`LxdEvents` runs in its own daemon thread (538–539). The screen only copies the feed under a lock (624–626), so a slow or stuck event stream can't freeze the display. What happens in each case (since `da1017c`, the panel's `reconnecting` also names the error, as in `reconnecting (TimeoutError)`, the way the API footer names its errors, so a failure that repeats can't loop quietly):

| Case | What happens | Panel status |
|---|---|---|
| LXD not running, or the socket file is missing | `connect` raises `OSError`; retry every 5 s | reconnecting |
| Not in the `lxd` group | `PermissionError`; retry every 5 s | no access |
| LXD answers anything but 101 | `ConnectionError` (562); retry | reconnecting |
| LXD closes, by close frame (580) or by EOF on a restart or snap refresh (569) | Reconnects after 5 s, and the feed is kept | reconnecting, then live |
| The stream stops mid-frame | A short read gives a JSON error or `ConnectionError`; reconnect | reconnecting |
| A message that isn't JSON | `ValueError`; reconnect | reconnecting |
| Bytes arrive slowly | Waits; sampling and drawing carry on | live |
| Connection open but silent | `TimeoutError` after 60 s with no frame, pings included; reconnect. Before the [L-6](#l-6-no-timeouts-on-the-event-socket) fix, it waited indefinitely | reconnecting |
| Valid JSON that isn't an event object | `AttributeError` or `TypeError`; reconnect. Before the [L-4](#l-4-unexpected-input-ends-the-event-listener-for-good) fix, the thread ended for good | reconnecting |
| Frame length over 16 MiB (`EVENT_FRAME_MAX`), 2^63 included | Refused before anything is read, with `FrameTooLargeError`; reconnect. Before `d3cd9e2`, `OverflowError` or `MemoryError` (L-4) | reconnecting |
| Deeply nested JSON | `RecursionError`; reconnect (L-4) | reconnecting |
| `"action": null`, or a non-string name or username | Stored as text and drawn. Before the [L-5](#l-5-a-null-or-non-string-event-field-crashes-lxdtop) fix, lxdtop exited at the next frame drawn | live |
| Ping payload over 125 bytes | A malformed pong, or `ValueError`; reconnect ([N-3](#n-3-websocket-protocol-details)) | reconnecting |

**How confirmed.** `tests/test_events.py` covers each handled row against a scripted fake LXD: the handshake, fragments with a ping in between, 16- and 64-bit lengths, masked frames, close frames, truncation at four points, bad JSON, refused upgrades, no socket, no access, and keeping the feed across a reconnect. The L-4 and L-5 rows were reproduced with a one-off probe against the same fake server before they were fixed, and their tests now check the behaviour above.

### L-4 Unexpected input ends the event listener for good

**Low. Fixed in `85b8719`.**

- **Where.** `_loop()`, line 548, catches only `(OSError, ValueError, ConnectionError)`. `_record()` assumes the event, its metadata, context and requestor are objects (593, 599–600, 608), and that `source` is a string (594). `stream.read(size)` (577) allocates the whole declared size up front.
- **Impact.** JSON that is valid but not an event object (`[]`, `{"metadata": "x"}`, `"source": null`) raises `AttributeError` or `TypeError`. A 64-bit length ≥ 2^63 raises `OverflowError`, one of 2^40 raises `MemoryError`, and deep nesting raises `RecursionError`. None of these is caught, so the thread ends. Python prints the traceback to stderr, over the curses screen, and the panel keeps saying "live" with no more events. LXD itself always sends event objects with RFC-sized frames, so this needs another program listening on LXD's socket, and putting one there takes root.
- **How confirmed.** One-off probe against the fake server: each input above ended `_listen()` with the exception named, outside `_loop()`'s `except`. `test_unexpected_json_shapes_reconnect` and `test_an_impossible_frame_length_reconnects` cover all six inputs: on `main` before the fix they failed six times, each naming the exception that ended the thread, and they pass with it. With the real thread, fed `[]` on every connection, it now stays alive and reconnects after 5 s.
- **Smallest fix (applied).** One line: 548 is now `except Exception:`, so any failure reconnects after 5 s. Bounding the frame size (for example, refusing `size > 16 * 1024 * 1024` before line 577) would also avoid allocating a huge declared length up front. That isn't done: a length too large to allocate now just costs a reconnect, and filling a merely large one needs a peer that really sends that much.
- **Frame size capped (later, `d3cd9e2`).** A frame longer than `EVENT_FRAME_MAX` (16 MiB, far above any LXD event) is now refused before `stream.read()` makes its buffer: `FrameTooLargeError`, and the panel says `reconnecting (FrameTooLargeError)`. The fix above made a huge length cost only a reconnect on Linux, where asking for 1 TiB fails at once with `MemoryError`. On macOS the buffer is handed out lazily, and under `-X dev` Python's debug allocator fills every new buffer, so `test_an_impossible_frame_length_reconnects` touched all 1 TiB and ran the Mac it ran on out of memory. That test now fails if lxdtop asks to read more than 64 MiB at once, and a frame of exactly 16 MiB still reads.

### L-5 A null or non-string event field crashes lxdtop

**Low. Fixed in `a93f240`.**

- **Where.** `_record()` stores `action`, `name` and `user` as they come (595, 608, 609). `draw_events_panel()` calls `event["action"].replace(...)` (1237), and slices and joins name and user (1242, 1244), in the main thread.
- **Impact.** `{"metadata": {"action": null}}` is stored, then ends lxdtop with `AttributeError` when the panel is drawn. LXD's schema types these fields as strings, so this has the same precondition as L-4.
- **How confirmed.** One-off probe: the event was recorded, then `draw_events_panel()` raised `'NoneType' object has no attribute 'replace'`. `test_a_null_action_does_not_crash_the_screen` now covers a null or numeric action, a numeric or list name, and a numeric or null username: it failed on `main` before the fix (six failures) and passes with it. End to end, `lxdtop --dump` against a fake LXD sending `{"action": null, "name": 5, ...}` exited with `AttributeError` on `main`, and draws the frame with the fix.
- **Smallest fix (applied).** The three values are wrapped in `str(... or "")` at 595, 608 and 609, so a missing or null value is `""` and anything else is shown as text.

### L-6 No timeouts on the event socket

**Low. Fixed in `a057527`.**

- **Where.** The socket made at 543 never gets a timeout, so `connect` (555), the reply `readline()` calls (561, 563) and the frame reads (568–577) can wait forever.
- **Impact.** If LXD hangs with the connection open, the feed keeps showing "connecting" or "live". The display itself is unaffected, and the table footer's `lxd api` status shows an error within one refresh, because API calls do time out after 5 s (421).
- **How confirmed.** By reading the code, and by `test_status_is_live_once_upgraded`: after the 101 reply, with nothing more sent, the status is "live" until the server closes. `test_a_silent_stream_reconnects` fails on `main` before the fix: with the timeout shortened to 0.3 s, lxdtop still waited the full 3 s until the fake LXD closed. With the fix it gives up after 0.3 s. `test_pings_keep_a_quiet_stream_open` checks that the timeout applies to each read: pings 0.6 s apart keep a 1 s timeout from firing over 2.4 s.
- **Smallest fix (applied).** A named constant, `EVENT_TIMEOUT = 60`, and `sock.settimeout(EVENT_TIMEOUT)` where `_loop()` makes the socket (543). That's rather than in `_listen()`, as first suggested, so the tests that call `_listen()` directly keep their own timeout. A timeout raises `TimeoutError`, an `OSError`, so `_loop()` reconnects. LXD pings its event listeners and drops ones that don't answer, which is why lxdtop answers pings. I couldn't measure the interval without a daemon. On that host this is a passive check: the desk screen's event panel should stay "live" on a quiet host, and never flip to "reconnecting".

## 5. Other findings

### L-7 Interface names from LXD go into a path unchecked

**Low. Fixed in `c9c129a`.**

- **Where.** `fetch_instances()` keeps each NIC's `host_name` (471–472), and `read_net()` builds `/sys/class/net/{name}/statistics/rx_bytes` from it (322–323).
- **Impact.** For a container, LXD sets `host_name` itself. For a VM, LXD builds the reported network state from the lxd-agent inside the guest. If LXD passes a guest-chosen `host_name` through, which I couldn't verify without a daemon, a guest could point lxdtop at another interface's counters and so misreport its own traffic. It could also use `../` to read any file whose path ends in `statistics/rx_bytes`. If a second party places a FIFO at such a path (a host user, or a container whose root filesystem lxdtop can reach as root through `/proc/<pid>/root`), `open()` blocks, and the sampling loop, and with it the display, freezes. A NUL or other invalid name is harmless, because `read_int()` catches the error.
- **How confirmed.** One-off runs against the fake host: `read_net(["../../../srv/x"])` returned the counters stored at `/srv/x/statistics/`, and `read_int()` on a FIFO at such a path was still waiting after 3 s. `test_interface_names_stay_inside_sys_class_net` fails on `main` before the fix and passes with it. It also checks that a bad name beside a good one still counts the good one, and that a numeric name gives no reading without an error. With the fix, a name leading to a FIFO returns at once. `test_a_name_with_a_nul_is_no_reading` passes either way.
- **Smallest fix (applied).** Two lines at the top of `read_net()`'s loop (321) skip any name containing `/`. The test is `"/" in str(name)`, so a name that isn't text can't raise where the f-string never did.

### L-8 A delegated ZFS dataset can take another instance's disk counters

**Low. Fixed in `fe566a4`, `6bd38d7`, `aebe798` and `3de9844`.**

- **Where.** `find_zfs_datasets()`, line 243, maps a dataset to an instance by the text after the **last** `/containers/`.
- **Impact.** With `zfs.delegate` (off by default), a container's root can create `default/containers/c1/containers/victim`. That dataset maps to `victim` and can replace victim's DISK R and DISK W with numbers c1 chooses. Which one wins depends on the order glob returns the kstat files in. Misleading display only.
- **How confirmed.** `test_a_delegated_child_dataset_cannot_claim_another_instance`, with fixture kstat files, now tries both orders and a VM. It fails before `fe566a4`, because the nested dataset read last won, and passes with it. For the case of an instance with no ZFS dataset, `test_a_nested_dataset_cannot_claim_an_instance_without_zfs` and `test_with_lxds_pools_only_datasets_directly_in_them_count` fail before `6bd38d7`, as do `test_where_each_zfs_pool_keeps_its_datasets` and the end-to-end `test_a_nested_dataset_cannot_feed_another_instances_disk_columns`. In that last one, an instance on io.stat showed 2,500,000 B/s read from a nested dataset instead of its own 2,000 B/s. Without the API, `test_even_without_lxds_api_a_nested_dataset_cannot_claim_an_instance`, `test_without_lxds_api_nesting_at_any_depth_or_project_is_skipped` and `test_without_the_api_a_nested_dataset_cannot_feed_them_either` fail before `aebe798` and pass with it. For a container that has stopped, `test_even_a_stopped_containers_nested_dataset_cannot_claim_an_instance`, the `root_dataset()` tests and `test_without_the_api_a_stopped_containers_dataset_cannot_feed_them` fail before `3de9844` and pass with it.
- **Fix (applied, in four steps).** `fe566a4`: for each instance, keep the dataset with the shortest name, since an instance's own dataset is always shorter than one nested inside another instance's. It's three lines. `6bd38d7`: `fetch_server()` already reads every storage pool, and now records where each ZFS pool keeps its volumes: `zfs.pool_name` in the pool's config, which defaults to the pool's name. `find_zfs_datasets()` then counts a dataset only when it sits directly in one of those. That excludes datasets nested in a container and datasets outside LXD's pools, and still works for a pool whose dataset has `/containers/` in its path, which `partition()` would have broken. If what LXD reports matches none of this host's datasets, the shortest-name rule stays rather than blank the DISK columns. That can't reopen the gap: a nested dataset lives under a container's own dataset, which matches whenever the roots are right. `aebe798`: without the API, lxdtop can still see which containers are running, from their cgroup directories, and a container can only make datasets inside its own. So when there are no pool datasets to go by, a dataset whose path runs through `containers/<a running container>/` doesn't count, however deep and in whatever project. A pool kept in a dataset such as `tank/containers/lxd` still works, unless a running container happens to be named `lxd`. `3de9844`: that rule could only spot a dataset while the container that made it ran. So without the API, lxdtop now reads which dataset each running container has as `/`: its first process's `/proc/<pid>/mountinfo` gives the filesystem type and, for ZFS, the dataset. Any user can read that file, including across mount namespaces (checked as uid 65534 with no groups). A running container's answer is taken as exact, so a dataset that isn't its `/` isn't its disk, and a container whose `/` isn't ZFS has no ZFS disk. The earlier rules stay as the fallback when the answer can't be read.
- **Checked on a real host.** On LXD 6.7, standalone, `--no-api` shows ZFS numbers in DISK R and DISK W for the same containers as normal mode does, so the `mountinfo` reading works on real LXD containers. That check can't tell whether `zfs.pool_name` matched the dataset holding the pool's `containers/`, or lxdtop fell back to the no-API rules, which also show the numbers. `lxc storage show <pool>` would tell.

### L-9 Unexpected JSON from the API stops lxdtop or its refreshes

**Low. Fixed in `daa158d`. Found after the first version of this review, while fixing L-4.**

- **Where.** `LxdApi.fetch()`, line 499, caught only `(OSError, ValueError, KeyError, http.client.HTTPException)`. `fetch_instances()` assumes the reply's metadata is a list of objects, each with an object `state` and `network` (463–477), and uses the name as a dict key (466, 476).
- **Impact.** An answer in a shape LXD never sends (`"metadata": "x"`, an instance that is a string, `state` or `network` of the wrong type) raises `AttributeError`, and a name that is a list raises `TypeError`. Neither was caught. `main()` calls `fetch()` once before drawing (1524), so at startup lxdtop exited with a traceback. In the background thread (510–514), refreshes ended for good while the footer kept saying `lxd api ok`, so names, addresses and stopped instances froze at the last answer and looked current. This has the same precondition as L-4: something other than LXD on its socket.
- **How confirmed.** `test_a_reply_in_an_unexpected_shape` (five shapes) and `test_dump_when_lxd_answers_in_an_unexpected_shape` fail on `main` and pass with the fix. With the real thread on `main`, one malformed answer killed it and the footer stayed "ok"; with the fix, the footer showed `error (AttributeError)` and went back to "ok" at the next good answer.
- **Smallest fix (applied).** One line: 499 is now `except Exception as err:`. The last good answer stays on screen, and the footer shows the error's name.

## Notes

### N-1 Not security: events came from the default project only

Line 557 asked for `/1.0/events?type=lifecycle` with no `project` or `all-projects=true`, and LXD then sends only the default project's events. So on a host with more than one project, nothing that happened in the others reached the feed, though the table lists their instances (463). Fixed in `1bba705` by asking for `&all-projects=true`, which LXD 5.21 and 6 both accept. Outside the default project, LXD also adds `?project=<name>` to each event's source, and snapshot events have no name field, so `_record()` now leaves the query off before splitting the path (594). Otherwise a snapshot would show as `nightly?project=dev`.

Checked in LXD's source on its `stable-5.21` and `main` branches: `request.ProjectParams()` treats a request with no project as `default`, the events server skips other projects' events for such a listener, and `api.URL.Project()` adds the query. `test_events_from_every_project` and `test_the_project_after_the_source_is_left_off` fail before the fix. This widens which API users' text reaches the screen, which is safe now that L-2 and L-3 are fixed. Not checkable on that host, which has only the default project. On a host with another project: start or snapshot an instance in it, and it should appear in the feed.

### N-2 Secrets stay off the screen

Exec command lines are shown only with `--show-commands` (603–607), and the example console unit doesn't set it. Keep it that way for a console anyone can see. `test_exec_commands_show_only_when_asked` checks that the password in the fixture's `mysql -pS3cret!` stays out of the feed by default.

### N-3 Websocket protocol details

None of these matters with LXD, which follows RFC 6455. A pong for a ping payload over 125 bytes, which the RFC forbids, is malformed at 126–127 bytes and raises `ValueError` from 128 (584), so lxdtop reconnects. lxdtop doesn't answer a close frame (580), doesn't check `Sec-WebSocket-Accept`, and ignores reserved bits and unknown opcodes. Over a local root-owned socket, none of these is a risk.

### N-4 What holds up

- Process names are split at the **last** `)` (401–402), so a name containing `)` can't shift the numeric fields.
- Only the first part of a cgroup path, the part LXD names, decides which container a process belongs to (333–335).
- Pool names are URL-quoted in the API path (486).
- The UI never waits on LXD: the API and events run in background threads, and HTTP calls time out after 5 s.

### N-5 `REMOTE_TZ` and `SCREEN_VTS` go into remote commands unquoted

`lxdtop-remote` lines 85, 61 and 63. Both come from `remote.conf`, which the script already runs as shell code, so they cross no new boundary. If you want a guard anyway, check them: `case "$REMOTE_TZ" in *[!A-Za-z0-9_+/-]*) exit 2 ;; esac`.

### N-6 `remote.conf` is shell code

`lxdtop-remote` line 24. This is by design and documented. Anything that can write `~/.config/lxdtop/remote.conf` can run commands as you, the same as `~/.profile`.

### N-7 `install` overwrote `~/bin/lxdtop` in place

`lxdtop-remote` line 70: `cat > ~/bin/lxdtop`. A dropped connection left a truncated script, which the console unit then restarted every 3 s until it was reinstalled, and a symlink at that path was written through. The smallest fix was to write `~/bin/lxdtop.new` and then `mv` it into place. Separately, the `screen` command needs passwordless `sudo chvt`; limit that sudoers rule to `chvt`.

Fixed in `016cba3`. Still over one ssh connection, so one login, the host now writes the copy to `~/bin/lxdtop.new` and checks it. Its SHA-256 must match the local file's, which `lxdtop-remote` works out before connecting (`sha256sum` on Linux, `shasum -a 256` on macOS) and passes along in the command. Then `~/bin/lxdtop.new --version` must print a line starting with `lxdtop `. Only then does `mv` rename it over `~/bin/lxdtop`. A rename replaces the file, or a symlink at that path, in one step, so `~/bin/lxdtop` is always a whole file: the old one, or the new one after its checks. If a check fails, the old file is left exactly as it was, `lxdtop.new` is removed, and the error says what failed. A copy cut off by a dropped connection simply ends early on the host, so it fails the SHA-256 check and is removed the same way. On success, `install` prints the version and the SHA-256 it checked. Since `e289ca3`, it also flushes `lxdtop.new` to disk (`sync FILE`) before the rename, so that a power cut just after an install can't leave an empty `~/bin/lxdtop` either.

How confirmed: with a real OpenSSH 9.6 `sshd` on the same machine, and a ProxyCommand that reset the connection after a set number of bytes. Before the fix, a cut 40,000 bytes in left a 32,768-byte `~/bin/lxdtop`. After it, cuts from the handshake to 40 bytes before the end left the old file and no `lxdtop.new`, with dash and with bash as the login shell. A login shell that kept the command's stderr showed what the host printed after the connection was gone: the SHA-256 mismatch. sshd doesn't stop a command that has no terminal when its connection goes, so the command finished its checks itself (this sshd ran without PAM); if a hangup or TERM signal stops it instead, a trap removes `lxdtop.new`. In the suite, `tests/fake_ssh.py` can cut the input off or change a byte, and `InstallTest` runs with dash and with bash as the login shell. `test_a_copy_cut_off_partway_leaves_the_old_one`, `test_a_copy_that_arrives_damaged_leaves_the_old_one`, `test_a_file_that_does_not_run_leaves_the_old_one` and `test_a_symlink_at_bin_lxdtop_is_replaced_not_written_through` fail before the fix.

One case can't be refused: if the connection drops after the whole copy has arrived, for example a silent drop just after the last byte, the host can't tell that from the copy's normal end, so it checks the copy and installs it. `lxdtop-remote` then reports the ssh failure and says the host's `~/bin/lxdtop` is unchanged unless the whole copy arrived and passed its checks.

### N-8 Not security: lxdtop exited when the locale wasn't installed

`locale.setlocale(locale.LC_ALL, "")` (1516) raises `locale.Error` when `LANG` names a locale the host lacks. That is common over ssh, because macOS sends `LANG` and Ubuntu's sshd accepts it, so `LANG=de_DE.UTF-8` from a Mac stopped lxdtop before it drew anything. Fixed in `41f69d2` by ignoring that error: Python has already set the character type at startup, so curses still gets UTF-8. Checked with `LANG=de_DE.UTF-8` on a host without that locale. `test_a_locale_the_host_does_not_have` fails on `cf50ad6`.

### N-9 Not security: one storage pool LXD couldn't read stopped every refresh

`fetch_server()` asks for each pool's usage (486–487). LXD 5.21 and 6 answer with an error (HTTP 500) when they can't read the pool: `storagePoolResourcesGet` passes on the driver's error, and for ZFS that comes from `zfs get` on the pool, which fails when the pool wasn't imported or its disk is missing. The error left `fetch_server()`, so `fetch()` threw the whole answer away (499). A failed fetch doesn't advance the once-a-minute round, so every later refresh asked for the pools again and failed again. For as long as the pool was down, names, addresses, VMs, stopped instances and the network columns froze, or never showed if lxdtop started that way, and the footer said `lxd api error (ValueError)`. Fixed in `2013f92`: a pool whose usage call returns an error is left out of the memory panel, and its ZFS dataset still counts for the DISK columns. `test_a_pool_lxd_cannot_read_is_left_out` fails before the fix. A usage call that hangs past the 5-second timeout instead of failing still costs that refresh, as before.

### N-10 Not security: on an LXD cluster, every member's instances and events showed

On a cluster member, `GET /1.0/instances` (call 1) returns the instances of every member, each with its member's name as `location`. The event stream (call 5) carries every member's events too, because each member pulls the others' events and passes them on to its own listeners, each with its `location`. lxdtop showed the instances on other members as dim rows (a running one read "running") and counted them in the status line's "stopped". It could also match a running VM elsewhere to a local process with the same PID, and the feed mixed in every member's activity. Fixed in `b6ac6ab` and `9091fb1`. `GET /1.0` (call 2) says whether LXD is clustered (`server_clustered`) and this member's name (`server_name`). lxdtop now asks for it before the instances, then skips instances and events whose `location` is another member.

A standalone host is unchanged. LXD then reports `server_clustered: false` and gives every instance the location `none`, so nothing is skipped. The fixtures now carry those fields, and every earlier test still passes; the only change to them is that one also expects the empty member name. In a one-off comparison, `main`'s `lxdtop` and the fixed one drew identical frames from the same standalone fake host and LXD: 160x45 with five events in the feed, 100x30, `--color` and `--no-api`. They also sent the same requests; only their order differs, on the once-a-minute round. `test_on_a_cluster_only_this_members_instances`, `test_dump_on_a_cluster_member`, `test_on_a_cluster_only_this_members_events` and `test_the_event_feed_learns_this_cluster_member` fail before the fixes.

Not checked, because no cluster was available: everything above about a cluster comes from LXD's source on `stable-5.21` and `main`, not from a running cluster. That includes:
- that `server_name`, an instance's `location` and an event's `location` all use the same member name;
- that events from other members reach a client on this one;
- how long call 1 takes on a cluster, where LXD asks every member for its instances' state, while lxdtop gives up after 5 seconds.

Also, on a cluster LXD leaves `zfs.pool_name` out of the pool list (call 3), so lxdtop takes each ZFS pool's name as its dataset. If no dataset matches, it falls back to the no-API rules from L-8. If only some pools match, instances on the others use `io.stat`.

## Limits

- **No real LXD.** No LXD daemon was available. LXD's naming rules, how much of a VM's state its agent controls (L-7), and that `zfs.pool_name` names the dataset a ZFS pool's volumes live in (L-8) come from LXD's documentation, and are marked where unverified. What N-1, N-9 and N-10 rely on was read in LXD's source for 5.21 and 6, but not seen from a running daemon. No LXD cluster was available at all. The one check on a real host so far is described under L-8.
- **One ncurses version.** curses behaviour was checked with ncurses 6.5 (Ubuntu 24.04). Since the fix, control characters are replaced before curses sees them, so other versions no longer matter for L-3.
- **One OpenSSH version.** R-1 was checked with OpenSSH 9.6p1. Older clients were modelled with `tests/fake_ssh.py`, not run.
- **No terminal emulators.** What an escape sequence can do depends on the terminal, and none was tested. The fix removes them whatever the terminal.
