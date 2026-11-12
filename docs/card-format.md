# Card format

One card is one TOML file, `<data>/cards/<id>.toml`. Areas are listed in
`<data>/areas.toml`. `bin/pihub check` validates both.

Texts are written for the owner of the machine, not for an engineer: what
the thing does for them, in everyday words.

## Top level

| Key | Required | Meaning |
|---|---|---|
| `schema` | no | format version, currently `1` |
| `id` | yes | same as the file name |
| `name` | yes | everyday name ("Web server", not "nginx.service") |
| `area` | yes | id of an area in `areas.toml` |
| `summary` | yes | one line, at most 70 characters |
| `about` | yes | two or three sentences: what it is and why it matters |
| `units` | no | systemd units this card describes. `x.timer` also covers `x.service` |
| `containers` | no | container names this card describes |
| `needs` | no | ids of cards this one cannot work without |
| `switch` | no | a unit that turns this off on purpose. When it is inactive, the card is "off, as intended", not broken |
| `todo` | no | a known open point in one or two sentences. The card shows yellow until the line is removed |

## Tables

Each is a list (`[[ports]]`, `[[steps]]`, …).

**`ports`**: what this service means to open, and for whom.

| Key | Required | Meaning |
|---|---|---|
| `port` | yes | number |
| `proto` | no | `tcp` (default) or `udp` |
| `reach` | yes | who is meant to get in: any of `internet`, `lan`, `tailscale`, `local` |
| `why` | yes | one or two sentences: why it is open, or why it is shut |

**`links`**: pages to open. `label` required; `port` (with optional `path` and `scheme`, default `http`) or a full `url`; `note`. A link with a port opens on the host name the page itself was opened with.

**`places`**: files and folders that matter. `path` and `what` required; `caution` for anything that must not be touched, rotated or copied.

**`checks`**: how to tell that it works.

| Key | Required | Meaning |
|---|---|---|
| `label` | yes | what is being checked, as a statement |
| `run` | yes | a read-only command. Passing means exit code 0 |
| `expect` | no | a regular expression the output must contain |
| `blind_spot` | no | what this check does not show |
| `show` | no | `true`: show the first line of the output as the result, also when the check passes (a reading such as "last backup 3 h ago"). Without it, the output of a passing check is thrown away |

**`steps`**: what to do when it does not work, in order. `text` required; `run` is a read-only command that goes with the step.

**`actions`**: the commands that are allowed.

| Key | Required | Meaning |
|---|---|---|
| `label` | yes | what the button says |
| `run` | yes | the exact command |
| `level` | yes | `read` (changes nothing), `change` (asks first), `copy` (never run from PiHub, shown for copying) |
| `effect` | for `change` and `copy` | what happens to people and services when this runs |
| `timeout` | no | seconds the command may run, a whole number from 1 to 3600. Default 120. After that the command and everything it started is ended |
| `choose` | no | the command needs one value, picked from a list that is measured at that moment: `blocked_addresses` (addresses currently locked out) or `unmounted_targets` (mount points in `/etc/fstab` that are not mounted). `run` must then contain `{value}` exactly once; without `choose` it must not appear |

Only `read` and `change` actions can be run from PiHub, and only by a logged-in
owner (see `systemd/README.md`). `copy` is refused by the runner, whatever the
page asks. The runner reads the card from disk for every request and runs the
text of `run`; the value is quoted for the shell and must match
`[A-Za-z0-9_.:/@-]{1,200}`, must not start with `-`, and must be on the measured list. Where the command understands it, put `--` before `{value}` (`... -- {value}`). Example:

```toml
[[actions]]
label = "Unblock an address"
run = "sudo cscli decisions delete --ip {value}"
level = "change"
choose = "blocked_addresses"
timeout = 30
effect = "The address may connect again."
```

## What `check` compares

- Own units, containers and listening ports that no card describes.
- Units and containers a card names that do not exist.
- For every port on a card, the intended reach against the measured
  binding: nothing listening; meant for the network but bound to the
  machine only; meant for the machine only but bound wider, so that only
  the firewall keeps it shut.

When a part of the machine could not be read, `check` names it and claims
nothing about it: a unit is not "missing" because `systemctl` was not there to
ask, and a port is not "shut" because no firewall was read.

## What PiHub reads on your machine

`pihub scan` (and `pihub collect`, which scans first) reads the machine in
separate sections. It only reads; it starts and changes nothing, and it never
stores container settings, process command lines or file contents (see the top
of `pihub/scan.py`). Each section stands alone. If one cannot be read, the
inventory records why, `scan` prints it, and the page says so in words at the
place where the data would be. The rest is unaffected.

| Section | Reads with | Needs `sudo` | If it is missing |
|---|---|---|---|
| `host` | `/etc/os-release`, `/proc`, the device tree | no | fields stay empty |
| `units`, `timers` | `systemctl` (`list-units`, `list-unit-files`, `list-timers` with `--output=json`; only tested with Debian 13) | no | Cards show their units as "not known", not as missing. No unit counts as undescribed, and a `switch` is not evaluated |
| `containers` | `docker ps -a` (first as you, then with `sudo` if you are not in the `docker` group) | only without docker group | Cards show their containers as "not known" |
| `ports` | `ss` | for the names of the programs behind the ports | Without sudo the ports are still listed, without program names (the Security page says so). Without `ss` no port is known, and nothing is said about whether a card's port listens |
| `firewall` | `ufw status verbose` | yes | The Security page says that no readable firewall runs here. Ports are listed as they listen, with the verdict "cannot be verified", never as "shut". A switched-off ufw is shown as such |
| `blocks` | `cscli decisions list` (CrowdSec) | yes | The Security page says that CrowdSec is not installed or not readable. No locked-out address is shown, and the "Unlock" action finds nothing to offer |
| `storage` | `lsblk` (JSON), `/etc/fstab` (mount point, type and harmless flags only), the filesystem usage | no | The Storage page says so. The `unmounted_targets` list is empty |
| `web` | `/etc/nginx/sites-enabled` (a fixed set of directives only) | no | Recorded in the inventory; no page shows it yet |
| `home` | the names in your home folder | no | The Files page says so |
| `vitals` | `/sys/class/thermal`, `/proc` | no | A reading the machine does not have (for example no temperature sensor) is left out; the History page says "no readings" for it |

Where `sudo` is needed, PiHub calls `sudo -n`: it never asks for a password
and never waits for one. Without passwordless `sudo` (and when you are not
root) the sections marked above fail with the reason "needs administrator
rights", and the page says so instead of showing nothing. Two ways out: run
`pihub collect` as root (for example from a systemd service), or allow exactly
these commands for the user that runs it, with `visudo`. Adjust the paths
(`command -v ufw`) and replace `youruser`:

```
youruser ALL=(root) NOPASSWD: /usr/sbin/ufw status verbose, \
    /usr/bin/cscli decisions list -o json, \
    /usr/bin/ss -H -t -l -n -p, /usr/bin/ss -H -u -l -n -p
```

Other firewalls (firewalld, plain nftables) and other intrusion tools are not
read. The Docker check of "published ports skip the firewall" looks for the
`docker-proxy` program and does not see a setup without it.
