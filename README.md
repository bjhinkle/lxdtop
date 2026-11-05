# lxdtop

A live, btop-style view of an LXD host in the terminal, with one row per container or virtual machine.

lxdtop is a single Python file that needs nothing beyond the standard library. It only reads: it never changes anything on the host.

## What it shows

- **Status line**: LXD version, OS and kernel, a clock, and how many instances are running or stopped.
- **CPU panel**: total use, a small bar per CPU thread, CPU temperature and clock speed, and the load average.
- **Memory panel**: used memory, the ZFS cache (ARC) shown separately, free memory, swap, and storage pool and root disk usage. A ZFS pool that isn't ONLINE shows its state there in red, after the LXD pools kept in it: one with `zfs.pool_name=tank/lxd` is in `tank`.
- **Network panel**: traffic through the host's physical ports in Mb/s, with traffic in drawn upward and traffic out drawn downward, plus each port's rate, busiest first.
- **CPU by instance**: a stacked graph of the last few minutes, one color for each of the busiest instances, then everything else and the host itself.
- **LXD events**: a live feed of starts, stops, snapshots, `exec` sessions and file transfers. An event that repeats within 10 minutes stays on one line with a count. The panel's corner says `live`, or `reconnecting` with the name of the error it's recovering from, such as `reconnecting (TimeoutError)`.
- **Instance table**: uptime, CPU, CPU history, the busiest process inside each container, memory, disk reads and writes, network in and out (Mb/s), task count and IPv4 address. Screens wider than 160 columns add the columns under [Warnings and wide screens](#warnings-and-wide-screens). Where two instances in different LXD projects have the same name, the one outside the default project shows as `<project>/<name>`, such as `dev/api`, here and everywhere else on screen.
- **Warnings**: when an instance is struggling rather than just busy, or a ZFS pool isn't ONLINE, a short line in the table's top edge says so, most serious first, for example `web1: memory kill 12m ago · tank DEGRADED`. The instance's marker (● or ■) turns red.
- **Busiest process**: the process in each container that has used the most CPU over its last few readings, so the name doesn't flicker, with its CPU over the last interval, as the container's is: it never shows more than its container. A container can use more CPU than any one of its processes shows, when its work is spread over short-lived processes: each takes a small share, and one that starts and ends between two readings shows nowhere.

It is laid out for a 160x45 screen, such as a small console monitor or a large terminal window, and adapts to smaller windows. When the screen is short, the cpu, memory and network panels stay as long as they can: first the CPU graph shrinks to 7 rows, then the stopped instances share one line (`4 stopped: auth-two-old, billing, web-31, web-32`), then only the top running instances keep a row, with `+N more` for the rest. Only after that does the graph go, and the host panels last. A narrower screen drops panels, then columns.

## Warnings and wide screens

Busy isn't the same as struggling: a container at 180% CPU may be fine, while one at 20% may be stuck behind its CPU limit or waiting on disk. lxdtop reads a few more counters from each container's cgroup to tell them apart.

The warning line sits in the instance table's top edge, between its title and `sorted by`. It shows as many warnings as fit, most serious first, then `+N more`. Pools and memory kills are in red, the rest in yellow:

| Warning | What it means | When |
|---|---|---|
| `fast SUSPENDED` | A ZFS pool is down: FAULTED, SUSPENDED, UNAVAIL, OFFLINE or REMOVED | Always |
| `web1: memory kill 12m ago` | The kernel killed a process in `web1` because it ran out of memory (an OOM kill). `2 memory kills, last 3m ago` when there were more | For an hour after it (`RECENT_KILL`), counting only kills since lxdtop started |
| `tank DEGRADED` | A ZFS pool has lost redundancy but still works | Always |
| `db: waiting on mem 42%` | Some of `db`'s tasks were stalled waiting for memory (or `cpu`, or `io` for disk) during 42% of the last 10 seconds: Linux pressure stall information, "some avg10" | From 25% (`PRESSURE_PCT`) |
| `web1: throttled 45% @2c` | `web1` hit its CPU limit of 2 cores in 45% of the limit's 100 ms periods during the last interval, so it had to wait | From 20% (`THROTTLED_PCT`) |

On screens wider than 160 columns, the table adds these columns after IPV4, in this order as space allows. Up to 160 columns the table is unchanged.

| Column | Shows | From |
|---|---|---|
| THROTTLED | `45% @2c`: the share of its CPU limit's periods in the last interval in which it was held back, then the limit in cores. Yellow from 20%. `-` means no CPU limit. A VM, or a container held only to some of the host's CPUs, shows just how many, `@2c` | `cpu.stat` (`nr_periods`, `nr_throttled`), `cpu.max` and `cpuset.cpus.effective`; a VM's `limits.cpu` |
| MEM/LIMIT | `62% of 4.0G`: MEM as a share of its memory limit. Yellow from 90% (`NEAR_LIMIT_PCT`). `-` means no limit | `memory.max`; a VM's `limits.memory` |
| WAITING | `mem 34%`: the worst of its CPU, memory and disk stalls, as in the warning. Red from 25%. `-` below 1% | `cpu.pressure`, `memory.pressure`, `io.pressure` |
| MEM KILLS | `2 12m ago`: processes the kernel has killed for memory since lxdtop started, and how long ago the last was. Red for an hour. `-` means none | `memory.events` (`oom_kill`) |
| PROCS/LIMIT | `96% of 500`: TASKS as a share of its process limit. Yellow from 90%. `-` means no limit | `pids.max` |
| LAST SNAP | `3h`: how old its newest snapshot is, or `never` | LXD's API: `GET /1.0/instances?recursion=2` already includes each instance's snapshots |

These come from a running container's cgroup, so stopped instances leave them empty. LXD applies a container's `limits.cpu` as a set of CPUs it may run on, so a container held to fewer CPUs than the host has shows that as its CPU limit, `@2c`, and with a quota (`limits.cpu.allowance`) as well, the smaller of the two. Only a quota holds a container back, so only then is there a throttling share to show. A running VM has no cgroup here, so it shows only the limits LXD gives it, from its `limits.cpu` and `limits.memory`: THROTTLED shows its CPUs, as `@2c`, and MEM/LIMIT shows MEM (its QEMU process's) as a share of its memory. Where those aren't set, it has LXD's defaults, one CPU and 1 GiB, and lxdtop shows those. Its throttling, stalls and processes are inside its guest. LAST SNAP and MEM KILLS still show for a stopped container. A file the kernel doesn't provide, or a feature it has turned off (such as pressure stall information with `psi=0`), leaves its cell empty too.

`s` sorts by THROTTLED and WAITING too, most held back or most stalled first, at any width: where the columns don't fit, the table's top edge still says `sorted by throttled` or `sorted by waiting`.

Every threshold is a named constant near the top of `lxdtop`, with a comment saying what it means: `THROTTLED_PCT`, `PRESSURE_PCT`, `RECENT_KILL`, `NEAR_LIMIT_PCT`, and `OPS_FROM` for the screen width.

## What's new in 1.9.0

- **`--dump --json` says `null` for a reading lxdtop couldn't take,** such as an unreadable `/proc/loadavg` or a container's `memory.current`, where it said `0`. On screen, those cells are left blank.
- **VMs show their CPU and memory limits,** from LXD's `limits.cpu` and `limits.memory`: in THROTTLED (`@2c`) and MEM/LIMIT on screens wider than 160 columns, in the detail view and in the JSON. lxdtop reads no other key of an instance's config.
- **A container held to some of the host's CPUs shows that limit.** LXD applies a container's `limits.cpu` as a set of CPUs, so THROTTLED now shows `@2c` for one held to two, and the JSON's `cpu_limit_cores` says 2; with a quota too, the smaller. Without a quota there's no throttling, so `cpu_throttled_pct` is `null` there.
- **`s` sorts by THROTTLED and WAITING too,** most held back or most stalled first.
- **The network graph is drawn in braille in the fine drawing,** two readings to a character, with traffic out filling down from the middle line. The classic drawing and `--ascii` are unchanged.
- **An event frame over 16 MiB is refused before it's read,** so a broken event stream can't make lxdtop ask for a huge buffer. The events panel says `reconnecting (FrameTooLargeError)` while it reconnects.

## What's new in 1.8.2

- **The detail view fits a short window.** In a window under 18 rows, the page is cut to fit inside its box, and its last line says how tall a window shows the rest. Before, its lower lines were drawn over the box's bottom edge.
- **The CPU graph's legend cuts no words.** Where it doesn't fit, `other instances` and `host itself` shorten to `other` and `host`, then the least busy names give way to `…`. The table still shows each name in its graph color. Before, at 80 columns, the legend could end in `■ h`.
- **Root's usage shows whole in the memory panel.** Where the pools and root don't fit on the panel's bottom row, their sizes go (`default 13%   root 40%`), then healthy pools give way to `…`. Root, and a pool that isn't ONLINE, always show. Before, root was cut at 120 columns (`root 40G/10`), and at 160 columns while a pool wasn't ONLINE.

## What's new in 1.8.1

- **LXD's API errors show at 80 columns.** When LXD's socket is missing or refuses connections, the instance table's bottom edge now says so, for example `lxd api error (ConnectionRefusedError)`. Where there isn't room for it beside the disk label, the disk label gives way, and on a narrower screen the error's name is shortened.

## What's new in 1.8.0

- **A finer drawing for terminal windows.** In a terminal with 256 colors and a UTF-8 locale, the CPU graph and the CPU history column are drawn in braille dots, meters move in eighths of a character, and the CPU, memory and swap meters shade from green through yellow to red. The Linux console and `--ascii` look exactly as before, and `--drawing classic` keeps the old look anywhere. See [Terminals](#terminals).

## What's new in 1.7.1

- **A crash fix.** lxdtop no longer exits when an instance's CPU controller is turned on while it runs. That instance shows no throttling figure for one refresh, then the real one.
- **`? keys` on the main screen.** The instance table's bottom edge now says where the help screen is. At 80 columns it gives way to a longer `lxd api` status, such as `no access`, and the rest of the screen is as before.

## What's new in 1.7.0

- **Keys for a closer look.** `?` lists every key. `/` filters the table by name, the arrows highlight a row, and Enter opens a page with everything lxdtop has on that instance: its readings and limits, throttling, pressure, memory kills, newest snapshot and recent events. Esc acts at once. All of it is read-only, and with nothing highlighted the screen is as before.

## What's new in 1.6.0

- **`--dump --json`.** One reading as a single JSON document: everything the screen shows, as numbers in base units, for scripts, a monitoring check, or a view that gathers several hosts over ssh. See [One reading as JSON](#one-reading-as-json).

## What's new in 1.5.0

- **Security fixes.** A review of 1.4.0 found nothing high-severity, and everything it found is fixed. Text from inside containers can't send escape sequences to your terminal or shift the screen. A process name that isn't UTF-8, or an odd or stalled answer on LXD's socket, no longer stops lxdtop or its updates, and the event panel names the error it reconnects after. Interface names from LXD stay inside `/sys/class/net`, a delegated ZFS dataset can't take another instance's disk numbers, and `lxdtop-remote` passes hosts and options to ssh without them being read as options or shell code. Details are in [SECURITY-REVIEW.md](SECURITY-REVIEW.md).
- **Cluster support.** On a member of an LXD cluster, lxdtop shows that member's instances and events, not the whole cluster's. That has been checked against LXD's source only, not yet on a real cluster (see [#16](https://github.com/bjhinkle/lxdtop/issues/16)). A standalone host looks as before.
- **Ops signals.** A warning line names instances that are struggling rather than just busy (a memory kill, CPU throttling, or tasks stalled waiting for CPU, memory or disk) and ZFS pools that aren't ONLINE, and marks their rows in red. Screens wider than 160 columns get six more columns. See [Warnings and wide screens](#warnings-and-wide-screens).
- **Other fixes.** The event feed covers every project, a storage pool LXD can't read no longer stops lxdtop's LXD data, and lxdtop no longer exits when `LANG` names a locale the host lacks.

## Requirements

- Linux with cgroup v2 (Ubuntu 22.04 or later)
- LXD 5 or 6 (snap or package; `LXD_DIR` is honored). On a cluster member, it shows that member's instances and events. That has been checked against LXD's source only, not yet on a real cluster (see [#16](https://github.com/bjhinkle/lxdtop/issues/16)).
- Python 3
- Membership in the `lxd` group, or root, for names, IP addresses, VMs, stopped instances and the event feed. Without it, lxdtop still shows each container's CPU, memory and disk.

Tested on Ubuntu 24.04 with LXD 6.7 and 6.9 and Python 3.12.

## Install and run

On the LXD host, download `lxdtop` beside where it goes, check that it runs, and move it into place:

```bash
mkdir -p ~/bin
curl -fsSL -o ~/bin/lxdtop.new https://raw.githubusercontent.com/bjhinkle/lxdtop/main/lxdtop
chmod 755 ~/bin/lxdtop.new
~/bin/lxdtop.new --version | grep '^lxdtop ' && sync ~/bin/lxdtop.new && mv ~/bin/lxdtop.new ~/bin/lxdtop
~/bin/lxdtop
```

The fourth line prints the version. Only if the download runs and says it is lxdtop does it go on: `sync` flushes the copy to disk, so that a power cut just after can't leave an empty file, and `mv` replaces `~/bin/lxdtop` with it in one step. A download cut partway never becomes `~/bin/lxdtop`, so the console service never restarts into a half-downloaded file.

From a clone, copy it the same way, beside where it goes, and rename it into place:

```bash
mkdir -p ~/bin
install -m 755 lxdtop ~/bin/lxdtop.new
~/bin/lxdtop.new --version | grep '^lxdtop ' && sync ~/bin/lxdtop.new && mv ~/bin/lxdtop.new ~/bin/lxdtop
```

`install` straight onto `~/bin/lxdtop` would remove the old file before copying the new one, so for a moment there'd be no lxdtop there, or only part of one. As above, `sync` flushes the copy to disk before `mv` renames it into place.

Over SSH, ask for a terminal: `ssh -t <host> '~/bin/lxdtop'`.

Keys: `?` lists them all. `q` quits, `s` cycles the sort (cpu, mem, disk, net, name, then the wide-screen columns throttled and waiting), and `-` and `+` change the refresh interval (1 to 10 seconds). `/` filters the table by name (Enter keeps the filter, Esc clears it), the up and down arrows highlight a row, and Enter opens a page with everything lxdtop has on the highlighted instance, its recent events included: up to 50 of its own, however busy the rest of the host is (Esc or `q` goes back). No key changes anything on the host.

| Option | Effect |
|---|---|
| `--show-commands` | Show the command line of `lxc exec` sessions in the event feed. Hidden by default, because command lines can contain passwords |
| `--interval N` | Seconds between readings (default 2) |
| `--no-api` | Skip LXD's API: no names, IPs, VMs, stopped instances or events |
| `--ascii` | Plain ASCII drawing characters |
| `--drawing fine` or `--drawing classic` | Choose the drawing: fine (braille graphs, smooth meters) or classic (as on the Linux console). lxdtop picks one when this isn't given: see [Terminals](#terminals) |
| `--dump` | Print one frame as text and exit, for testing (with `--color`, `--size COLSxROWS`, `--samples N`, `--drawing`) |
| `--dump --json` | Print one reading as a JSON document instead, for scripts and monitoring checks: see [One reading as JSON](#one-reading-as-json) |

The clock follows the system time zone. Set `TZ` to change it, for example `TZ=UTC lxdtop`.

## Run it from another machine

`contrib/lxdtop-remote` runs lxdtop on a host over SSH from your own Mac or Linux machine, installs it there, and remembers usernames for hosts your SSH config doesn't cover.

```bash
ln -s "$PWD/contrib/lxdtop-remote" ~/.local/bin/lxdtop-remote
lxdtop-remote install myhost    # copy lxdtop to myhost:~/bin/lxdtop
lxdtop-remote myhost            # run it in this terminal
```

`install` copies lxdtop over one SSH connection to a new file of its own in `~/bin` on the host, `lxdtop.new.` and six random characters, so that two installs at once never share one. It checks the copy there: its SHA-256 must match your copy's (from `sha256sum`, or `shasum` on macOS), and its `--version` must print lxdtop's version. Only then is it flushed to disk and renamed over `~/bin/lxdtop`, in one step that replaces a symlink there rather than writing through it. If a check fails or the connection drops partway, `~/bin/lxdtop` stays as it was and the new file is removed, so the console service never restarts into a half-copied file. A connection that drops without a word, which sshd can take hours to notice, ends the install on the host once nothing more has arrived for a minute, and ssh on your machine gives up a minute after it last heard from the host. On success it prints the version and the SHA-256 it checked.

An lxdtop that's already running keeps running the version it started with. `install` doesn't restart anything, but when the console service below is running, it says so, with the command that restarts it.

On the host it runs `~/bin/lxdtop`, or the `lxdtop` on the host's PATH when `~/bin` has none, so a single copy in `/usr/local/bin` serves every user.

Optional settings live in `~/.config/lxdtop/remote.conf`: a default host, a time zone for the clock, and a window size to switch to while it runs. The comments at the top of the script list them.

## Run it full-time on a console screen

`examples/lxdtop-console.service` is a systemd unit that keeps lxdtop running on virtual terminal 8, for a small monitor attached to the host. Switch to it with Alt+F8 at the keyboard, or with `lxdtop-remote screen myhost` from another machine. After installing a new version, restart it to run that one: `sudo systemctl restart lxdtop-console`.

## One reading as JSON

`lxdtop --dump --json` prints one reading as a single JSON document instead of the text frame. `--samples` and `--interval` work as with `--dump`. For example, `ssh myhost '~/bin/lxdtop --dump --json'` from another machine, or in a script:

```bash
lxdtop --dump --json | python3 -c 'import json, sys; [print(i["name"], i["cpu_pct"]) for i in json.load(sys.stdin)["instances"]]'
```

Numbers stay numbers, in base units, and each key ends in its unit: `_bytes`, `_s` (seconds), `_bytes_per_s` (disk), `_bits_per_s` (network, decimal) or `_pct`. CPU % means what it does on screen: an instance's or a process's 100 is one core, and the host's `busy_pct` is the share of the whole machine. A value lxdtop has no reading for is `null`, never `0`, and so is a limit that isn't set. Each instance, each warning about one and each event also has its LXD `project` (`default` for the default project's), since two projects can have instances of the same name. As on screen, exec command lines appear only with `--show-commands`, and nothing comes from an instance's config but a VM's `limits.cpu` and `limits.memory`, as `cpu_limit_cores` and `mem_limit_bytes`. Text from inside containers is escaped (`\u001b`), never written raw.

| Field | What it holds |
|---|---|
| `schema`, `lxdtop_version`, `taken_at`, `interval_s`, `lxd_api` | This format's version (1), lxdtop's version, when the reading was taken (UTC, ISO 8601), the seconds between readings, and the state of LXD's API (`ok`, `off`, `no access`, `error (…)`) |
| `host` | `name`, `lxd_version`, `os`, `kernel`, `cluster_member`, `uptime_s`. `cpu`: `busy_pct`, `threads_busy_pct`, `outside_instances_pct` (the graph's "host itself", 100 = one core), `temp_c`, `clock_mhz`, `load_1m`, `load_5m`, `load_15m`. `memory`: `total_bytes`, `used_bytes` (without the ZFS ARC), `zfs_arc_bytes`, `available_bytes`, `swap_total_bytes`, `swap_used_bytes`. `root_fs`: `used_bytes`, `total_bytes` |
| `network` | The host's physical ports: `in_bits_per_s` and `out_bits_per_s` (all ports, over the last three readings), `peak_bits_per_s`, and `ports`, each with `name`, `in_bits_per_s` and `out_bits_per_s` |
| `pools` | `name`, `used_bytes`, `total_bytes` (from LXD) and `health` (a ZFS pool's state: `ONLINE`, `DEGRADED`, …) |
| `warnings` | Most serious first: `severity` (5 a pool down, 4 a memory kill, 3 a degraded pool, 2 high pressure, 1 throttling), `kind`, `instance` or `pool`, and `text` as the screen shows it |
| `events` | `status`, and the `feed`, newest first: `time`, `action` (LXD's, such as `instance-started`), `instance`, `detail`, `user`, `count` |
| `instance_counts` | `running`, `stopped` |
| `instances` | In the table's order: `name`, `type`, `state`, `ipv4`, `uptime_s`, `cpu_pct`, `cpu_history_pct`, `busiest_process` (`name`, `cpu_pct`), `mem_bytes`, `disk_read_bytes_per_s`, `disk_write_bytes_per_s`, `disk_source` (`zfs`, `io.stat`, or `lxd` for a VM's from LXD's metrics), `net_in_bits_per_s`, `net_out_bits_per_s`, `tasks`. Limits and ops signals: `cpu_limit_cores`, `cpu_throttled_pct`, `mem_limit_bytes`, `mem_of_limit_pct`, `tasks_limit`, `tasks_of_limit_pct`, `waiting_on`, `waiting_pct`, `mem_kills` (since lxdtop started), `mem_kills_total` (the kernel's count since the instance started), `last_mem_kill_ago_s`, `newest_snapshot_at`, `newest_snapshot_age_s` |

## Where the numbers come from

| What | Source |
|---|---|
| Container CPU, history and graph | `/sys/fs/cgroup/lxc.payload.<name>/cpu.stat`. 100% means one CPU core fully busy |
| Container memory | `memory.current` minus inactive file cache from `memory.stat`: the working set, as `docker stats` reports it |
| VM CPU and memory | The VM's QEMU process (`/proc/<pid>/stat` and `VmRSS`). LXD's API reports zero for VMs that do not run its guest agent, so lxdtop reads the process instead |
| Busiest process and uptime | `/proc/<pid>/cgroup` (which container a process belongs to) and `/proc/<pid>/stat` |
| Disk reads and writes | ZFS per-dataset counters (`/proc/spl/kstat/zfs/<pool>/objset-*`), which include reads served from cache; cgroup `io.stat` on other storage. A VM whose disk isn't on ZFS has no counter lxdtop's user can read on the host, so its numbers come from LXD's metrics (`GET /1.0/metrics`, read every 15 seconds while there's such a VM), which LXD takes from the VM's agent or from QEMU: they show after half a minute, and change every 15 seconds |
| Network in and out | Host-side veth and tap interface counters, in megabits per second |
| Host panels | `/proc/stat`, `/proc/meminfo`, ZFS `arcstats`, hwmon temperature, cpufreq and physical port counters |
| Throttling, limits, stalls and memory kills | The container's cgroup: `cpu.stat`, `cpu.max`, `cpuset.cpus.effective`, `memory.max`, `pids.max`, `memory.events` and the three `*.pressure` files |
| VM limits | `limits.cpu` and `limits.memory` in the VM's expanded config, which LXD's instance list already includes, worked out as LXD does when it starts the VM. lxdtop reads no other key of an instance's config |
| ZFS pool health | `/proc/spl/kstat/zfs/<pool>/state`, beside the objset counters |
| Names, addresses, stopped instances, snapshots, versions and pool usage | LXD's REST API over its local socket every 15 seconds (GET requests only), waiting up to 5 seconds for each answer. On a cluster member, it waits up to 30, since listing instances asks every member, and reads each ZFS pool's config for this member (`?target=<member>`), as the cluster-wide list leaves out `zfs.pool_name` |
| Event feed | LXD's lifecycle event stream, a websocket on the same socket |

Network traffic is shown in bits per second and data in bytes: bandwidth is in bits, data is in bytes.

## Terminals

lxdtop has two drawings and picks one when it starts. The help screen (`?`) says which, and why.

- **Classic** is what the Linux console can show: 16 colors and only the characters in Ubuntu's default console font, so graphs use full, medium and light shade blocks, and meters use `■`. Terminals with 256 colors get finer greys and blues.
- **Fine** is for a terminal window that can show it: a UTF-8 locale, at least 256 colors, and a `TERM` other than `linux`. The CPU-by-instance graph, the CPU history column and the network graph are drawn in braille dots, two readings to a character, so they cover twice the time in the same width. In the network graph, traffic out fills down from the middle line, as traffic in fills up from it. Meters fill in eighths of a character, and the CPU, memory-used and swap meters shade from green through yellow to red with their level. The ZFS cache and free memory keep their own colors, since a full one is no warning.

`--drawing classic` keeps the classic drawing in a terminal window. `--drawing fine` asks for the fine one where lxdtop wouldn't pick it, for example a terminal whose `TERM` claims fewer colors than it has. `--dump` draws classic unless `--drawing fine` is given, so `lxdtop --dump --drawing fine` and `lxdtop --dump` show the two side by side. `--ascii`, or a locale that isn't UTF-8, draws plain ASCII whichever is asked for. tmux and screen set a `TERM` of their own, so on the console inside them, use `--drawing classic`.

## Status

Early, and provided as is.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the few rules that keep lxdtop small and safe.

## License

MIT. See [LICENSE](LICENSE).
