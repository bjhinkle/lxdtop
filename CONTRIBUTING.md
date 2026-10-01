# Contributing

Thanks for helping. Issues and pull requests are welcome. A few rules keep lxdtop small and safe to run on any LXD host:

- **One file, Python standard library only.** No third-party packages, so installing it stays a single copy.
- **Read-only.** lxdtop sends only GET requests to LXD, listens to its event stream, and reads files under `/proc` and `/sys`. It never changes anything on the host.
- **Works on the Linux console.** That means 16 colors and only the characters in Ubuntu's default console font: full block, medium and light shade, box drawing, and `■ ● · … ▲ ▼ ↑ ↓ °`. There are no half blocks, dark shade or braille there. `--ascii` must keep working too.
- **Units.** Network bandwidth is shown in bits per second (Mb/s, decimal); data amounts (disk, memory) in bytes.
- **Secrets stay off the screen.** For example, `lxc exec` command lines are hidden unless `--show-commands` is given, because they can contain passwords.

## Testing

- `./lxdtop --dump` prints one frame as text; add `--color`, `--size 100x30` or `--samples 10` (readings taken first, so the graphs have history).
- Run it for real in a terminal at a few sizes, including 80x24, and on the Linux console if you can.
- In your pull request, say what you tested on: distribution, LXD version, storage driver, and whether there were containers, VMs or both.
