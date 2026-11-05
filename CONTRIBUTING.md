# Contributing

Thanks for helping. Issues and pull requests are welcome. A few rules keep lxdtop small and safe to run on any LXD host:

- **One file, Python standard library only.** No third-party packages, so installing it stays a single copy.
- **Read-only.** lxdtop sends only GET requests to LXD, listens to its event stream, and reads files under `/proc` and `/sys`. It never changes anything on the host.
- **Works on the Linux console.** lxdtop has two drawings, and the classic one is what the console can show: 16 colors and only the characters in Ubuntu's default console font: full block, medium and light shade, box drawing, and `■ ● · … ▲ ▼ ↑ ↓ °`. There are no half blocks, dark shade or braille there. The fine drawing adds braille, eighth blocks and 256-color shades for terminal windows. lxdtop picks it only when the locale is UTF-8, `TERM` isn't the console's (`linux`, or `linux-…`) and the terminal has at least 256 colors (`pick_drawing()`). `--drawing classic` or `--drawing fine` overrides that, `--dump` draws classic unless asked, and `--ascii` or a locale that isn't UTF-8 draws plain ASCII. Everything must work in the classic drawing, which must look on the console exactly as it did: `tests/test_drawing.py` compares it with frames that lxdtop 1.7.1 drew. `--ascii` must keep working too.
- **Units.** Network bandwidth is shown in bits per second (Mb/s, decimal); data amounts (disk, memory) in bytes.
- **Secrets stay off the screen.** For example, `lxc exec` command lines are hidden unless `--show-commands` is given, because they can contain passwords.

## Testing

- `python3 -m unittest` runs the unit tests in `tests/`. They need only the standard library and no LXD host: they read fixture files instead of `/proc` and `/sys`, and talk to a fake LXD socket.
- `./lxdtop --dump` prints one frame as text; add `--color`, `--size 100x30` or `--samples 10` (readings taken first, so the graphs have history).
- Run it for real in a terminal at a few sizes, including 80x24, and on the Linux console if you can.
- In your pull request, say what you tested on: distribution, LXD version, storage driver, and whether there were containers, VMs or both.
