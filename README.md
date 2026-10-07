# lxdtop

A live, btop-style view of an LXD host in the terminal, with one row per container or virtual machine.

lxdtop is a single Python file that needs nothing beyond the standard library. It only reads: it never changes anything on the host.

## What it shows

- **Status line**: LXD version, OS and kernel, a clock, and how many instances are running or stopped.
- **CPU panel**: total use, a small bar per CPU thread, CPU temperature and clock speed, and the load average.
- **Memory panel**: used memory, the ZFS cache (ARC) shown separately, free memory, swap, and storage pool and root disk usage.
- **Network panel**: traffic through the host's physical ports in Mb/s, with traffic in drawn upward and traffic out drawn downward, plus each port's rate, busiest first.
- **CPU by instance**: a stacked graph of the last few minutes, one color for each of the busiest instances, then everything else and the host itself.
- **LXD events**: a live feed of starts, stops, snapshots, `exec` sessions and file transfers. An event that repeats within 10 minutes stays on one line with a count.
- **Instance table**: uptime, CPU, CPU history, the busiest process inside each container, memory, disk reads and writes, network in and out (Mb/s), task count and IPv4 address.

It is laid out for a 160x45 screen, such as a small console monitor or a large terminal window, and adapts to smaller windows by dropping panels, then columns.

## Requirements

- Linux with cgroup v2 (Ubuntu 22.04 or later)
- LXD 5 or 6 (snap or package; `LXD_DIR` is honored)
- Python 3
- Membership in the `lxd` group, or root, for names, IP addresses, VMs, stopped instances and the event feed. Without it, lxdtop still shows each container's CPU, memory and disk.

Tested on Ubuntu 24.04 with LXD 6.7 and 6.9 and Python 3.12.

## Install and run

On the LXD host, download `lxdtop` and make it executable:

```bash
mkdir -p ~/bin
curl -fsSL -o ~/bin/lxdtop https://raw.githubusercontent.com/bjhinkle/lxdtop/main/lxdtop
chmod 755 ~/bin/lxdtop
~/bin/lxdtop
```

From a clone, `install -m 755 lxdtop ~/bin/lxdtop` does the same.

Over SSH, ask for a terminal: `ssh -t <host> '~/bin/lxdtop'`.

Keys: `q` quits, `s` cycles the sort (cpu, mem, disk, net, name), and `-` and `+` change the refresh interval (1 to 10 seconds).

| Option | Effect |
|---|---|
| `--show-commands` | Show the command line of `lxc exec` sessions in the event feed. Hidden by default, because command lines can contain passwords |
| `--interval N` | Seconds between readings (default 2) |
| `--no-api` | Skip LXD's API: no names, IPs, VMs, stopped instances or events |
| `--ascii` | Plain ASCII drawing characters |
| `--dump` | Print one frame as text and exit, for testing (with `--color`, `--size COLSxROWS`, `--samples N`) |

The clock follows the system time zone. Set `TZ` to change it, for example `TZ=UTC lxdtop`.

## Run it from another machine

`contrib/lxdtop-remote` runs lxdtop on a host over SSH from your own Mac or Linux machine, installs it there, and remembers usernames for hosts your SSH config doesn't cover.

```bash
ln -s "$PWD/contrib/lxdtop-remote" ~/.local/bin/lxdtop-remote
lxdtop-remote install myhost    # copy lxdtop to myhost:~/bin/lxdtop
lxdtop-remote myhost            # run it in this terminal
```

On the host it runs `~/bin/lxdtop`, or the `lxdtop` on the host's PATH when `~/bin` has none, so a single copy in `/usr/local/bin` serves every user.

Optional settings live in `~/.config/lxdtop/remote.conf`: a default host, a time zone for the clock, and a window size to switch to while it runs. The comments at the top of the script list them.

## Run it full-time on a console screen

`examples/lxdtop-console.service` is a systemd unit that keeps lxdtop running on virtual terminal 8, for a small monitor attached to the host. Switch to it with Alt+F8 at the keyboard, or with `lxdtop-remote screen myhost` from another machine.

## Where the numbers come from

| What | Source |
|---|---|
| Container CPU, history and graph | `/sys/fs/cgroup/lxc.payload.<name>/cpu.stat`. 100% means one CPU core fully busy |
| Container memory | `memory.current` minus inactive file cache from `memory.stat`: the working set, as `docker stats` reports it |
| VM CPU and memory | The VM's QEMU process (`/proc/<pid>/stat` and `VmRSS`). LXD's API reports zero for VMs that do not run its guest agent, so lxdtop reads the process instead |
| Busiest process and uptime | `/proc/<pid>/cgroup` (which container a process belongs to) and `/proc/<pid>/stat` |
| Disk reads and writes | ZFS per-dataset counters (`/proc/spl/kstat/zfs/<pool>/objset-*`), which include reads served from cache; cgroup `io.stat` on other storage |
| Network in and out | Host-side veth and tap interface counters, in megabits per second |
| Host panels | `/proc/stat`, `/proc/meminfo`, ZFS `arcstats`, hwmon temperature, cpufreq and physical port counters |
| Names, addresses, stopped instances, versions and pool usage | LXD's REST API over its local socket every 15 seconds (GET requests only) |
| Event feed | LXD's lifecycle event stream, a websocket on the same socket |

Network traffic is shown in bits per second and data in bytes: bandwidth is in bits, data is in bytes.

## Terminals

On the Linux console, lxdtop uses 16 colors and only the characters in Ubuntu's default console font, so its graphs use full, medium and light shade blocks. Terminals with 256 colors get finer greys and blues.

## Status

Early, and provided as is.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the few rules that keep lxdtop small and safe.

## License

MIT. See [LICENSE](LICENSE).
