# LinuxPilot

LinuxPilot is a simple command-line tool that checks the health of a Linux machine.
It works on servers, desktops and Raspberry Pi.

Run one command and it tells you what is fine, what needs attention, why it matters,
and what to check next.

- Read-only. It never changes your system.
- No background service. It runs, prints the result, and exits. Live views run only
  while you keep them open and stop when you quit.
- No dependencies. Only Python 3.9 or newer is needed.
- No root needed for normal use.
- Sends no data anywhere.

## Example

```text
$ linuxpilot
LinuxPilot 0.1.0 | web-01 | Ubuntu 24.04 LTS | up 41 days, 3h   checked in 0.6s

  [WARN] 1 warning needs attention

  [ OK ] System                       Ubuntu 24.04 LTS, kernel 6.8.0, x86_64, up 41 days
  [ OK ] CPU load                     0.40 / 0.52 / 0.61 (1/5/15 min) on 4 cores
  [ OK ] CPU usage        #.........  7% busy over 0.5s
  [ OK ] CPU temperature              52°C (coretemp)
  [ OK ] Memory           ###.......  2.1 GB / 7.8 GB used (27%)
  [WARN] Disk /           #########.  91% used (45.6 GB / 50.0 GB, 4.4 GB free)
  [ OK ] Internet                     reachable (1.1.1.1 in 9 ms)

Needs attention
  1. [WARNING] Disk /: 91% used (45.6 GB / 50.0 GB, 4.4 GB free)
     Why:  / is 91% full. When a filesystem fills up, databases, logs and package
           updates start failing.
     Next: Find what is using space: `sudo du -xh --max-depth=2 / | sort -h | tail -20`

Overall: WARNING  |  0 critical, 1 warning, 6 ok
```

The values above are only an example. In a normal terminal the output is in color,
with symbols and solid usage bars, and a short animation runs while it checks.
When the output goes to a file or another program, it stays plain.

## Install

You can install LinuxPilot with pip, pipx or uv. Pick the one you already use.
If you are not sure, use pipx.

| Method | Best for |
|---|---|
| pip | Virtual environments, or systems that allow pip installs |
| pipx | Most users, especially on Debian, Ubuntu and Raspberry Pi OS |
| uv | People who already use uv |

### Option 1: pip

```bash
pip install linuxpilot
```

On newer Debian, Ubuntu and Raspberry Pi OS this may fail with an
`externally-managed-environment` error. This is normal: these systems block pip from
installing into the system Python to protect system packages. Either use pipx or uv
instead, or install inside a virtual environment:

```bash
python3 -m venv ~/.venvs/linuxpilot
~/.venvs/linuxpilot/bin/pip install linuxpilot
~/.venvs/linuxpilot/bin/linuxpilot
```

### Option 2: pipx

pipx installs command-line tools in their own isolated environment and makes the
command available everywhere.

Install pipx first.

Debian, Ubuntu, Raspberry Pi OS:

```bash
sudo apt update
sudo apt install pipx
pipx ensurepath
```

Fedora:

```bash
sudo dnf install pipx
pipx ensurepath
```

Close and reopen your terminal, then install LinuxPilot:

```bash
pipx install linuxpilot
```

### Option 3: uv

If you do not have uv yet, see the
[uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).
Then run:

```bash
uv tool install linuxpilot
```

### Run it

```bash
linuxpilot
```

That is all you need for everyday use.

## Common commands

| Command | What it does |
|---|---|
| `linuxpilot` | Run all checks and show a summary |
| `linuxpilot -v` | Show more details for every check |
| `linuxpilot --only disk,memory` | Run only some checks |
| `linuxpilot --skip network` | Run everything except some checks |
| `linuxpilot --no-network` | Do not contact the internet |
| `linuxpilot --quick` | Skip the short CPU measurement |
| `linuxpilot --json` | Print the result as JSON |
| `linuxpilot --ascii` | Use plain text markers like `[ OK ]`, as in the example above |
| `linuxpilot watch` | Live, interactive view in the terminal, press q to quit |
| `linuxpilot serve` | Live dashboard in your browser, press Ctrl+C to stop |
| `linuxpilot list-checks` | List all available checks |
| `linuxpilot --help` | Show help |

## Live view

Use a live view when you want to keep an eye on a machine for a while, for example
during a backup, an update or a heavy job. Nothing runs in the background. The live
view lasts only while the command is open, and stops completely when you quit.

### In the terminal

```bash
linuxpilot watch
```

This opens an interactive view that refreshes every 5 seconds. It works over a plain
SSH connection, needs nothing extra installed, and opens on the most serious problem
with its explanation already shown.

At the top you see the overall result and small live graphs of CPU, memory, load and
temperature. Below that is the list of checks, with usage bars for disk, memory and CPU.

Keys:

| Key | What it does |
|---|---|
| Up / Down (or j / k) | Move between checks |
| Enter or Space | Open or close the details of a check |
| e | Open or close all checks |
| f or Tab | Show all checks, only problems, or only OK |
| / | Search by name (Enter keeps it, Esc clears it) |
| r | Check again now |
| p | Pause or resume |
| + / - | Refresh less often or more often |
| c | Copy the suggested command |
| v | Show checks that do not apply to this machine |
| ? | Show all keys |
| q or Esc | Quit |

Ctrl+C also quits, and Ctrl+Z pauses it in the background like any other program (`fg`
brings it back). Your terminal is always restored when you leave.

The copy key uses a standard terminal feature that most modern terminals support,
including over SSH. If your terminal does not support it, select the command with the mouse.

Useful options:

```bash
linuxpilot watch --interval 10        # refresh every 10 seconds (minimum 2)
linuxpilot watch --only cpu,memory    # watch only some checks
linuxpilot watch --plain              # simple refreshing report without keys
linuxpilot watch --json >> log.jsonl  # write one JSON line per refresh to a file
```

### In the browser

```bash
linuxpilot serve
```

Then open http://127.0.0.1:8765 in your browser. The page updates on its own and shows
what needs attention at the top. Press Ctrl+C in the terminal to stop. After that the
page shows that it is disconnected.

What you can do on the page:

- Click any check to see its measured values, where the data came from, and any notes.
- Show only problems or only OK checks, or type in the box to find a check by name.
- Watch small graphs of CPU, memory, load and temperature since you opened the page.
- Choose how often it refreshes, press Refresh now, or Pause it.
- Press Copy next to a suggested command to copy it.

The page remembers your filter and refresh rate in that browser. The graphs are kept only
in the open tab and are gone when you close it.

Options:

```bash
linuxpilot serve --open            # also open the page in your browser
linuxpilot serve --port 9000       # use another port
linuxpilot serve --interval 10     # refresh every 10 seconds
```

The page works offline and loads nothing from the internet. It only shows information
and cannot change anything on the machine.

### Viewing a server or Raspberry Pi from your laptop

By default only the machine itself can open the dashboard. The safe way to see it from
your laptop is an SSH tunnel, which needs no open ports and no extra setup:

1. On your laptop, connect with a tunnel:
   ```bash
   ssh -L 8765:127.0.0.1:8765 user@your-server
   ```
2. In that SSH session, start the dashboard:
   ```bash
   linuxpilot serve
   ```
3. On your laptop, open http://127.0.0.1:8765

When you close the SSH session or press Ctrl+C, the dashboard stops.

If you really want other computers on your network to open it directly, use:

```bash
linuxpilot serve --host 0.0.0.0 --allow-remote
```

LinuxPilot then prints a link with a random access token. Only people with the full link
can see the data. The connection is not encrypted, so only do this on a network you trust,
or use a VPN such as Tailscale or WireGuard instead.

## What it checks

| Area | Checks |
|---|---|
| System | OS, kernel, uptime, reboot required after updates |
| CPU | Load, usage, I/O wait, temperature |
| Memory | Memory and swap use, memory pressure, processes killed for lack of memory |
| Disk | Free space, inodes, disks that became read-only unexpectedly |
| Services | Failed systemd services and why they failed |
| Network | Default route, network interfaces, DNS, internet access |
| Raspberry Pi | Low voltage and CPU throttling |

Some checks only run where they make sense. For example, temperature is usually not
available on cloud servers and virtual machines, and the Raspberry Pi check only runs on a Pi.
These are shown as "not applicable" instead of as errors.

## Why I built this

Checking on my servers and Raspberry Pi meant running a dozen commands and working
out what the output meant. I wanted one small tool that tells me plainly what is
wrong, why it matters, and what to check next, and that runs only when I ask it to.

It is still early. Feedback is very welcome, especially from people running it on a
Raspberry Pi or a distribution other than Ubuntu.

## Understanding the results

Each check gets one of these results:

| Result | Meaning |
|---|---|
| ok | Healthy |
| warning | Needs attention soon |
| critical | Needs attention now |
| unknown | LinuxPilot could not read this, for example because of permissions |
| unsupported | Does not apply to this machine |
| skipped | You chose not to run it |

## Using it in scripts

LinuxPilot returns an exit code you can use in scripts or cron jobs:

| Exit code | Meaning |
|---|---|
| 0 | Everything is OK |
| 1 | At least one warning |
| 2 | At least one critical problem |
| 64 | Wrong command or option |
| 70 | Could not start, for example because the dashboard port is already in use |

The live views (`watch` and `serve`) exit with 0 when you quit them with q or Ctrl+C.

Example:

```bash
linuxpilot --quick > /dev/null || echo "Something needs attention"
```

Show only the problems as JSON (requires `jq`):

```bash
linuxpilot --json | jq '.findings[] | select(.status != "ok")'
```

## Default limits

| Check | Warning | Critical |
|---|---|---|
| Disk space used | 85% | 95% |
| Memory available | below 10% | below 5% |
| CPU load per core (5 min) | 1.5 | 3.0 |
| CPU temperature | 80°C | 90°C |

## Raspberry Pi note

If the Pi check shows a permission message, add your user to the `video` group,
then log out and log back in:

```bash
sudo usermod -aG video $USER
```

## Privacy

LinuxPilot does not collect or upload anything. The only network checks are one DNS lookup
of `example.com` and one connection to `1.1.1.1`. Use `--no-network` to turn them off.

The browser dashboard only listens on `127.0.0.1` (this machine) unless you choose
otherwise with `--allow-remote`.

## Update and uninstall

Use the same tool you installed with:

| Method | Update | Remove |
|---|---|---|
| pip | `pip install --upgrade linuxpilot` | `pip uninstall linuxpilot` |
| pipx | `pipx upgrade linuxpilot` | `pipx uninstall linuxpilot` |
| uv | `uv tool upgrade linuxpilot` | `uv tool uninstall linuxpilot` |

If you installed into a virtual environment, use that environment's pip, or just delete
the environment folder to remove it.

## Run from source

If you want to try it without installing from PyPI:

```bash
git clone https://github.com/nideshpandey/linuxpilot.git
cd linuxpilot
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
linuxpilot
```

Run the tests with `pytest`.

## Contributing

Bug reports and pull requests are welcome. See
[CONTRIBUTING.md](https://github.com/nideshpandey/linuxpilot/blob/main/CONTRIBUTING.md)
for the project principles and how checks are built and tested.

If you run LinuxPilot on a Raspberry Pi or a distribution other than Ubuntu, sharing the
output of `linuxpilot -v` in an issue helps a lot.

## License

MIT
