# Security review: lxdtop 1.4.0 and lxdtop-remote

Reviewed: `lxdtop` and `contrib/lxdtop-remote` at commit `cf50ad6` (2026-10-07). **Line numbers below refer to that commit.** Fixes made since then have moved later lines in `lxdtop` down by up to 51, and each finding also names its function.

lxdtop runs with access to LXD's socket, which is equivalent to root on the host, and it draws text that people inside containers choose. So this review asks four things: does it only read, can container-chosen text break out of its place on the terminal, can `lxdtop-remote` be made to run something it shouldn't, and what happens when the event stream is slow, closed or malformed.

## Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| [L-1](#l-1-a-process-name-that-isnt-utf-8-ends-lxdtop) | A process name that isn't valid UTF-8 ends lxdtop | Medium | Fixed in `5eb660b` |
| [L-2](#l-2---dump-prints-escape-sequences-from-containers-unchanged) | `--dump` prints escape sequences from containers unchanged | Medium | Fixed in `8726a25` |
| [L-3](#l-3-in-the-live-view-r-b-and-n-move-the-cursor) | In the live view, `\r`, `\b` and `\n` in a name move the cursor, and format characters pass through | Low | Fixed in `8726a25` |
| [L-4](#l-4-unexpected-input-ends-the-event-listener-for-good) | Unexpected input ends the event listener for good, while the panel still says "live" | Low | Fixed in `85b8719` |
| [L-5](#l-5-a-null-or-non-string-event-field-crashes-lxdtop) | A `null` or non-string event field crashes lxdtop when it is drawn | Low | Fixed in `a93f240` |
| [L-6](#l-6-no-timeouts-on-the-event-socket) | No timeouts on the event socket, so a stalled LXD still looks "live" | Low | Fixed in `a057527` |
| [L-7](#l-7-interface-names-from-lxd-go-into-a-path-unchecked) | Interface names from LXD go into a `/sys` path unchecked | Low | Fixed in `c9c129a` |
| [L-8](#l-8-a-delegated-zfs-dataset-can-take-another-instances-disk-counters) | With `zfs.delegate`, a container can take over another instance's disk counters | Low | Fixed in `fe566a4`, `6bd38d7`, `aebe798` and `3de9844` |
| [L-9](#l-9-unexpected-json-from-the-api-stops-lxdtop-or-its-refreshes) | Unexpected JSON from the API ends lxdtop at startup, or stops its refreshes while the footer says "ok" | Low | Fixed in `daa158d` |
| [R-1](#r-1-a-host-or-username-starting-with---is-read-by-ssh-as-an-option) | `lxdtop-remote`: a host or username starting with `-` is read by ssh as an option | Low | Fixed in `6f8420c` |
| [R-2](#r-2-lxdtop-options-are-parsed-again-by-the-hosts-shell) | `lxdtop-remote`: lxdtop options are parsed again by the host's shell | Low | Fixed in `12e82db` |
| [N-1 to N-8](#notes) | Notes with no security impact | Info | No change |

Nothing reached High. One bug that isn't a security issue was also fixed: lxdtop exited at startup when `LANG` named a locale the host doesn't have ([N-8](#n-8-not-security-lxdtop-exited-when-the-locale-wasnt-installed), `41f69d2`).

**Severity scale.** *High*: someone could change the host, run code as the lxdtop user, or read secrets. *Medium*: an unprivileged process in any container can stop lxdtop or send control sequences to the operator's terminal. *Low*: needs an unusual setup, a non-default option, a second foothold, or input only the operator or root controls, or it only misleads the display. *Info*: no security impact.

**Nothing is left open.** Every finding here is fixed, and the test suite has no expected failures left. Two fixes still need checking once on a real LXD host, because no daemon was available here: the event timeout ([L-6](#l-6-no-timeouts-on-the-event-socket)) and the ZFS disk mapping ([L-8](#l-8-a-delegated-zfs-dataset-can-take-another-instances-disk-counters)).

**Method.** I read all of both files. Each claim below was then checked by running something, either a test in `tests/` or a one-off run described under *How confirmed*, on Ubuntu 24.04 (Linux 6.18, Python 3.10 to 3.13, ncurses 6.5, OpenSSH 9.6p1). CI also runs the suite on Python 3.10, 3.12 and 3.15. No LXD daemon was available, so LXD's side (its validation rules, and what a VM's agent can report) comes from LXD's documentation and is marked where I couldn't check it.

## 1. Does lxdtop only read?

**Yes.** It sends only GET requests and the event-stream upgrade, opens files only for reading, and never writes a file, starts a process or changes the host.

### Every LXD API call

| # | Method | Path | Line | When |
|---|---|---|---|---|
| 1 | GET | `/1.0/instances?recursion=2&all-projects=true` | 463 | At startup (1524), every 15 s, and once early when a container appears that LXD hasn't described yet (722) |
| 2 | GET | `/1.0` | 481 | Every fourth refresh, so once a minute |
| 3 | GET | `/1.0/storage-pools?recursion=1` | 485 | With 2 |
| 4 | GET | `/1.0/storage-pools/<pool>/resources` | 486–487 | With 2, once per pool. The name is URL-quoted |
| 5 | GET with `Upgrade: websocket` | `/1.0/events?type=lifecycle` | 557 | At startup, and 5 s after any disconnect (552) |

Calls 1–4 all go through `LxdApi.get()`, whose only request is `conn.request("GET", path)` (452). Call 5 is written by hand on the unix socket (557–559). After the upgrade, the only thing lxdtop sends is pong frames answering LXD's pings (582–585), which are part of the websocket protocol rather than API calls.

**Flagged: nothing.** There is no POST, PUT, PATCH or DELETE, and no operation, exec, console or file endpoint.

**How confirmed.**
- `tests/test_lxd_api.py`, `test_only_get_requests_to_these_paths`: runs `LxdApi.fetch()` five times against a fake LXD on a unix socket that records every request line. Every request was a GET, to exactly the paths above.
- `tests/test_events.py`, `test_the_upgrade_request` and `test_pongs_are_the_only_thing_sent_after_the_handshake`: the upgrade is `GET /1.0/events?type=lifecycle`, and with events and pings interleaved, lxdtop sent exactly two masked pongs and nothing else.
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

What's left: wide characters (CJK, emoji) are printable but take two cells. They can shift the rest of a row in `--dump` output and misalign columns. That's cosmetic, with no control effect.

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

## 4. The event websocket: slow, closed or malformed

`LxdEvents` runs in its own daemon thread (538–539). The screen only copies the feed under a lock (624–626), so a slow or stuck event stream can't freeze the display. What happens in each case:

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
| Frame length ≥ 2^63, or too large to allocate | `OverflowError` or `MemoryError`; reconnect (L-4) | reconnecting |
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
- **Smallest fix (applied).** A named constant, `EVENT_TIMEOUT = 60`, and `sock.settimeout(EVENT_TIMEOUT)` where `_loop()` makes the socket (543). That's rather than in `_listen()`, as first suggested, so the tests that call `_listen()` directly keep their own timeout. A timeout raises `TimeoutError`, an `OSError`, so `_loop()` reconnects. LXD pings its event listeners and drops ones that don't answer, which is why lxdtop answers pings. I couldn't measure the interval without a daemon, so check once on a real host: on a quiet host the panel should stay "live" for several minutes.

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
- **Still to check.** Once on a real host: `lxc storage show <pool>` should list `zfs.pool_name` as the dataset holding the pool's `containers/` and `virtual-machines/`. DISK R and DISK W should still show for instances on it, both normally and with `--no-api`. The second run checks the `mountinfo` reading, whose fixtures here follow the kernel's format but weren't taken from a real LXD container.

### L-9 Unexpected JSON from the API stops lxdtop or its refreshes

**Low. Fixed in `daa158d`. Found after the first version of this review, while fixing L-4.**

- **Where.** `LxdApi.fetch()`, line 499, caught only `(OSError, ValueError, KeyError, http.client.HTTPException)`. `fetch_instances()` assumes the reply's metadata is a list of objects, each with an object `state` and `network` (463–477), and uses the name as a dict key (466, 476).
- **Impact.** An answer in a shape LXD never sends (`"metadata": "x"`, an instance that is a string, `state` or `network` of the wrong type) raises `AttributeError`, and a name that is a list raises `TypeError`. Neither was caught. `main()` calls `fetch()` once before drawing (1524), so at startup lxdtop exited with a traceback. In the background thread (510–514), refreshes ended for good while the footer kept saying `lxd api ok`, so names, addresses and stopped instances froze at the last answer and looked current. This has the same precondition as L-4: something other than LXD on its socket.
- **How confirmed.** `test_a_reply_in_an_unexpected_shape` (five shapes) and `test_dump_when_lxd_answers_in_an_unexpected_shape` fail on `main` and pass with the fix. With the real thread on `main`, one malformed answer killed it and the footer stayed "ok"; with the fix, the footer showed `error (AttributeError)` and went back to "ok" at the next good answer.
- **Smallest fix (applied).** One line: 499 is now `except Exception as err:`. The last good answer stays on screen, and the footer shows the error's name.

## Notes

### N-1 Events come from the default project only

Line 557 asks for `/1.0/events?type=lifecycle` with no `project` or `all-projects=true`. LXD's REST API documents that as the default project's events, so events for instances in other projects don't appear, while the table does list them (463). This is a feature gap rather than a security issue. Adding `&all-projects=true` would widen which API users' text reaches the screen, which is safe now that L-2/L-3 are fixed. Not checked against a live LXD.

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

### N-7 `install` overwrites `~/bin/lxdtop` in place

`lxdtop-remote` line 70: `cat > ~/bin/lxdtop`. A dropped connection leaves a truncated script, which the console unit then restarts every 3 s until it's reinstalled, and a symlink at that path is written through. The smallest fix is to write `~/bin/lxdtop.new` and then `mv` it into place. Separately, the `screen` command needs passwordless `sudo chvt`; limit that sudoers rule to `chvt`.

### N-8 Not security: lxdtop exited when the locale wasn't installed

`locale.setlocale(locale.LC_ALL, "")` (1516) raises `locale.Error` when `LANG` names a locale the host lacks. That is common over ssh, because macOS sends `LANG` and Ubuntu's sshd accepts it, so `LANG=de_DE.UTF-8` from a Mac stopped lxdtop before it drew anything. Fixed in `41f69d2` by ignoring that error: Python has already set the character type at startup, so curses still gets UTF-8. Checked with `LANG=de_DE.UTF-8` on a host without that locale. `test_a_locale_the_host_does_not_have` fails on `cf50ad6`.

## Limits

- **No real LXD.** No LXD daemon was available. LXD's naming rules, its default-project behaviour for events (N-1), how much of a VM's state its agent controls (L-7), and that `zfs.pool_name` names the dataset a ZFS pool's volumes live in (L-8) come from LXD's documentation, and are marked where unverified.
- **One ncurses version.** curses behaviour was checked with ncurses 6.5 (Ubuntu 24.04). Since the fix, control characters are replaced before curses sees them, so other versions no longer matter for L-3.
- **One OpenSSH version.** R-1 was checked with OpenSSH 9.6p1. Older clients were modelled with `tests/fake_ssh.py`, not run.
- **No terminal emulators.** What an escape sequence can do depends on the terminal, and none was tested. The fix removes them whatever the terminal.
