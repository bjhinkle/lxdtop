# Notes for Claude Code sessions in lxdtop

lxdtop is one Python file that shows a live view of an LXD host, using only the standard library. `contrib/lxdtop-remote` runs and installs it over ssh. The owner reviews and merges every change.

## Where things are

- `lxdtop` is the whole program. Its sections, in order, are:
  - settings, styles and glyphs;
  - reading the kernel's counters;
  - LXD's API and event stream (`LxdApi`, `LxdEvents`);
  - sampling (`Monitor`);
  - drawing primitives;
  - panels;
  - the help screen and the detail view;
  - one reading as JSON;
  - running it (keys, `run()`, `main()`).
- `contrib/lxdtop-remote` runs lxdtop on a host over ssh. Its `install` checks the copy on the host, then moves it into place.
- `examples/lxdtop-console.service` runs lxdtop full-time on a console.
- `tests/` is the unittest suite. In `tests/support.py`:
  - `FakeHost` is a temp directory laid out like /proc and /sys;
  - `FakeLxd` stands in for LXD's socket;
  - `monitor()`, `row()` and `render()` build frames.
- `tests/fixtures/frame-*.txt` are frames drawn by 1.7.1. They pin the classic and ASCII drawing.
- `SECURITY-REVIEW.md` is finished. Don't make more fixes there unless asked.
- **Open work is issue #16.** It lists everything found and not fixed yet, one line each with the PR it came from. When a PR fixes an item, tick it with "fixed by #N".

## The owner's rules

- **CONTRIBUTING.md's rules hold:**
  - one file, Python standard library only;
  - read-only: GET requests and the event stream only;
  - works on the Linux console in 16 colors with Ubuntu's console font;
  - `--ascii` keeps working.
- **The classic drawing must look exactly as it does on the console.** The fine drawing (braille, eighth blocks, 256 colors) is only for terminal windows, and `pick_drawing()` chooses between the two.
- **Don't restructure `rate()`, the sampling loop (`Monitor.sample*()`, `measure()`) or the event websocket code (`LxdEvents`).** The owner reads them to learn Python. Small, targeted fixes in them are fine.
- **`contrib/lxdtop-remote` stays POSIX sh** and passes shellcheck at its default severity. The local side may be macOS or Linux, and the host's login shell dash or bash.
- **Secrets stay off the screen.** Exec command lines show only with `--show-commands`, and nothing from an instance's config appears unless the screen already shows it.
- **Support LXD 5.21/stable and 6/stable.**
- **If a change needs `tests/test_readonly.py` edited,** explain why in the PR.
- **Keep each PR to the work it's for.** List anything else you find at the end of its description, under "Other things I found (not fixed here)", and add it to issue #16.

## How changes are made

- **One PR per piece of work,** on a `claude/…` branch from main. Never merge; the owner does, often within minutes.
- **Check that a PR is still open** right before pushing to its branch. If it has merged, start again from main.
- **A fix comes with a test that fails before it.** Show that failure in the PR.
- **Check the layout at 80x24, 160x45 and one larger size.** For drawing changes, compare frames from main and the branch, cell by cell and with colors, and say exactly what changed.
- **Put a short walkthrough of the new code** in the PR description.
- **Bump `VERSION` and add a "What's new in X.Y.Z" entry** to the README, in their own commit: patch for fixes, minor for features.
- **Read CI before moving on.** It runs the suite on Python 3.10, 3.12 and 3.x, plus shellcheck.
- **Keep lines to 120 characters.** Comments say why, in plain words.
- **Branches can't be deleted from a Claude session** (GitHub answers 403). Ask the owner to delete merged ones.

## Testing

- **The suite:** `python3 -m unittest discover -s tests`. It needs only the standard library and no LXD. Run it on Python 3.10 to 3.14 with `-X dev` where you can.
- **Frames:** `./lxdtop --dump --size 160x45` prints one; add `--color`, `--ascii` or `--drawing fine|classic`. `--dump --json` prints one reading as JSON.
- **Comparing two versions' frames:**
  1. Load each `lxdtop` with `importlib.machinery.SourceFileLoader`.
  2. Point `tests.support.lxdtop` at it.
  3. Draw the same `monitor()` into a `TextCanvas`, with `time.strftime` and `socket.gethostname` patched.
- **CPU cost:** time `Monitor.sample()` and `draw()` with `time.process_time()` on a `FakeHost` with about 50 containers. For 1.8.0, at the 2 s default, this came to about 1.2% of one core with the classic drawing and 1.3% with the fine one.
