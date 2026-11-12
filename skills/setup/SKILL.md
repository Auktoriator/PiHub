---
name: setup
description: "First-time setup of PiHub on this machine: scan the Raspberry Pi, write one plain-language card per service, check them, and (only with the owner's consent) set up the web page as a service. Use when the user wants to install, set up or start PiHub, or asks for a self-explaining dashboard or map of their home server."
disable-model-invocation: true
---

# PiHub: first-time setup

You set PiHub up on the machine Claude Code is running on. PiHub is a
command centre that explains the machine to its owner: scripts scan it, you
write one "card" per service once, and from then on a small web page shows
the map, findings, security, storage and history without any AI.

Your part is the one that needs understanding: deciding what each thing is
and saying so in everyday words. Everything else is done by scripts. Work in
small steps, say what you are about to do before you do it, and ask when
something is unclear instead of guessing. The owner is not necessarily an
engineer.

## Rules that always apply

- **Install nothing without asking** (no apt, pip, npm, no extra tools). PiHub
  needs only Python and the standard library.
- **Delete nothing.** If something has to go, move it into a folder such as
  `~/tidy-<date>/` and tell the owner. The owner deletes for good.
- **Never read, print or copy credentials.** That means `.env` files, key
  files, token files, password stores, `rclone.conf`, shell history,
  anything named like `*secret*`, `*token*`, `*.pem`, `id_*`. You may know
  that such a file exists and what it is for, never what it holds. Never put
  a secret into a card.
- **Never type, see or store a password.** The owner sets it (step 7).
- **`~/pihub-data` stays outside every repository.** It describes one
  machine. Do not put it under git, do not copy it into a repo, do not
  paste it into a public place. `chmod 700` on the folder is fine to keep.
- **When unsure about a service, write a card without actions** rather
  than one with guessed commands. A card is executable code (step 5).
- Answer in the owner's language; the files in `~/PiHub` stay English.

## 1. Check first, then say what will happen

Run, and tell the owner the result:

```
python3 --version          # PiHub needs 3.11 or newer
ls ~/PiHub                 # is the program already there?
ls ~/pihub-data            # is there earlier data?
systemctl --version | head -1
```

If Python is older than 3.11, stop and say so; do not install anything.

Then tell the owner, in a few sentences, what is going to happen: the
machine is scanned read-only, you write descriptions, the owner reviews every
command that could be run, and only after an explicit yes does anything run
permanently.

If `~/PiHub` does not exist, ask for consent and then run:

```
git clone https://github.com/Auktoriator/PiHub ~/PiHub
```

Why there and not in the plugin: the plugin is only the instructions. The
program runs later as a service and must not depend on the plugin cache,
which Claude Code may replace or clean up at any time. If `~/PiHub` exists
already, do not overwrite it; look at `git -C ~/PiHub status` and ask.

Find out the owner's language (from the conversation, otherwise ask). Create
the data folder and the setting:

```
mkdir -p ~/pihub-data && chmod 700 ~/pihub-data
```

`~/pihub-data/settings.toml` gets `language = "de"` or `language = "en"`.
Only those two exist. Write the file only if it does not exist yet; if it
does, show it and ask.

## 2. Scan

```
~/PiHub/bin/pihub scan
```

Say before running it: this only reads. It lists systemd units and timers,
containers (names and state), listening ports and the processes behind them,
the firewall rules, addresses blocked by CrowdSec, disks and mounts, a few
nginx directives (`listen`, `server_name`, `proxy_pass`, `allow`, `deny` and
similar) and the top-level names in the home folder. A few sections ask for
`sudo -n` (firewall, blocked addresses); where that is not allowed they are
skipped and the scan says so. Tools that are not installed are skipped too.

What it deliberately does not read: `docker inspect` (environment variables
hold passwords), command lines of processes, the contents of any file apart
from those nginx directives and the mount points in `/etc/fstab`.

Read the output. A section that failed is not a problem, but remember it:
you cannot describe what the scan could not see.

## 3. Understand

Read `~/pihub-data/inventory.json`. It says what is there, not what it is
for. To find out what things are for, read what the owner wrote down:

- `README*` files and notes in the home folder and in project folders
- `CLAUDE.md` or similar notes, if the owner keeps them
- the scripts the owner added under `/usr/local` (names and comments)
- Compose files (`compose.yaml`, `docker-compose.yml`): service names, images,
  ports, volumes. Not the `environment:` values, and not `.env` files.
- unit files of own services (`systemctl cat <unit>`), looking at `Description=`
  and `ExecStart=` only

Collect questions instead of guessing: which unit is the family's media
server, which port is meant for the internet, which "disk" is a backup that
must stay unplugged. Ask the owner in one batch, with short, concrete
questions ("There is a container called `x`. Do you use it?").

Do not describe operating-system plumbing one by one (dozens of `systemd-*`
units). Cover what the owner installed or depends on; `pihub check` lists
what is still unclaimed, and you can group the rest.

## 4. Areas and cards

Read `~/PiHub/docs/card-format.md` (the exact format) and
`~/PiHub/docs/writing-cards.md` (how to word it, and the rules that prevent
damage) before writing anything.

1. Write `~/pihub-data/areas.toml`: five to seven areas in the owner's
   language, named by what matters to them (for example "House",
   "Network and access", "Backup"), not by technology.
2. Write one card per service in `~/pihub-data/cards/<id>.toml`, in the
   owner's language. The `id` is the file name without `.toml`.
3. After each batch of five to ten cards run `~/PiHub/bin/pihub check` and
   fix every message. Continue until the format is right, nothing of the
   owner's own is left undescribed, and no port note is unexplained.
   Messages about intended reach versus actual binding are findings, not
   typos: show them to the owner and ask, do not just edit the card until it
   goes quiet.

Checks run every five minutes without anyone watching, so they must only read
and must be cheap. Be strict about the levels of actions: anything that can
destroy data, interrupt a running backup, restart the machine or ask for a
credential is `copy`, which PiHub never runs.

## 5. Let the owner review all actions

Before anything runs as a service, produce the complete list of all
`[[actions]]` across all cards, for example with
`grep -n -B1 -A6 '^\[\[actions\]\]' ~/pihub-data/cards/*.toml`, and show the
owner a table: card, button label, exact command, level, effect text.

Say why: a card is executable code. Whoever can write to `~/pihub-data/cards`
can make the runner execute commands with the owner's rights, and the owner
may have `sudo` without a password. Ask the owner to confirm each action or
name the ones to remove. Remove what they reject. Do this again for any later
change to actions. Also confirm that nobody else can write to the folder
(`ls -ld ~/pihub-data ~/pihub-data/cards`; mode 700 for the folder).

Also run `~/PiHub/bin/pihub list` and `~/PiHub/bin/pihub show <id>` for two or
three cards and let the owner read them; this is what they will see later.

## 6. Permanent operation (only with explicit consent)

Up to here nothing runs permanently. Before this step, say plainly:

- What: four systemd units (page, runner, collector, timer) are copied to
  `/etc/systemd/system/` and started; the page listens on port 8096; a
  firewall rule for the home network is added if a firewall is active.
- Risk: the runner runs the commands from the cards with the owner's rights,
  after a password. The page is plain HTTP without HTTPS. Anyone in the home
  network who gets the password can act. The page itself, in display mode,
  has no login.
- Undo: `sudo systemctl disable --now pihub-runner.service pihub.service
  pihub-collect.timer`, delete the four files, `sudo systemctl daemon-reload`,
  delete the firewall rule (full text in `~/PiHub/systemd/README.md`).

Wait for a clear yes. Then follow `~/PiHub/systemd/README.md` exactly: the
`sed` replacement of `@USER@`, `@REPO@`, `@DATA@`, `@PORT@`, `daemon-reload`,
`enable --now`. If a firewall is active (`ufw status`), add the rule for the
home network only, with the real network address range (ask the owner or read
it from the scan; do not write it into any repo file). Never create or suggest
a router port forwarding, also not "just for a moment".

Then verify, do not assume:

```
systemctl is-active pihub-runner.service pihub.service pihub-collect.timer
curl -s http://127.0.0.1:8096/api/session
```

The answer must contain `"actions": true`. If it says `false`, the runner is
not reachable: look at `journalctl -u pihub-runner -n 30` and fix that first.
Run `~/PiHub/bin/pihub collect` once so the page has a current state.

## 7. Password

The owner sets it themselves in a terminal:

```
~/PiHub/bin/pihub password
```

Tell them to run it themselves (in Claude Code the `!` prefix runs a command
in the session terminal) and that it asks twice and shows nothing. You do not
type it, do not look for it, do not write it anywhere. Afterwards check only
that `/api/session` reports `"password_set": true`.

## 8. Wrap up

Tell the owner:

- the address of the page (`http://<machine-address>:8096`, with the real
  address of the machine in the conversation only)
- what was checked: units active, `/api/session` answers, `pihub check` is
  clean, the cards were read by the owner
- what was **not** checked: whether the page is reachable from another
  device (the machine cannot measure that itself; ask them to open it on a
  phone in the home Wi-Fi, and to confirm that it is not reachable from the
  mobile network), how the page looks in their browser, whether each
  command actually does what its label says
- that `/pihub:update` is what to run later, after software updates or when
  services are added or removed
