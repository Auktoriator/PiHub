# Writing cards

This guide is for whoever writes cards: a person, or a model such as Claude
Code. [card-format.md](card-format.md) says which keys exist; this page says
how to fill them well, and which rules keep a card from doing harm. All
examples are invented.

## Who you are writing for

The reader is the owner of the machine on a bad day. They are not a
developer. They did not install the service yesterday, may not remember why it
is there, and have a problem now. The card has to tell them three things in
this order: what this is for them, whether it is fine, and what to do next.

- Short sentences. One idea each.
- Everyday words. Say "the web server", not "reverse proxy"; "locked out", not
  "banned by a decision". If a technical word is the thing they will see on
  screen or type (a unit name, a port), keep it, once, and explain it.
- Say what the thing does *for them*, not what it is made of.
- No marketing, no jokes, no apologies. State facts and say how sure you are.
- Write in the owner's language. The keys stay English.

## `summary`

One line, at most 70 characters. It appears next to the name in lists. Say what
it does, not what it is.

Weak: `Reverse proxy (nginx)`
Good: `Hands web requests to the right service`

Weak: `Docker container for media`
Good: `Plays the family's films on the TV`

## `about`

Two or three sentences. First: what it does for the owner. Second: what stops
working when it is down. Third, only if it helps: something that surprises
people.

Weak:
> nginx is a high-performance HTTP and reverse proxy server that handles
> TLS termination and load balancing for upstream services.

Good:
> Takes every web request and passes it on to the right service. When it is
> down, every page behind it is gone at once, although those services still
> run. It also holds the certificate for the public site.

## Port `why`

For each `[[ports]]` entry: why is it open, or why is it shut, and for whom.
The reader looks at it when a port shows up as a finding, so answer "should
this be here?".

Weak: `HTTPS`
Weak: `Port 443 is used for TLS traffic.`
Good: `The public site. The router passes this port on to the machine.`
Good: `Only for the web server on this machine. It must not be reachable from
the network; the firewall is not the only protection here.`

Set `reach` to who is *meant* to get in, not to what happens to be true now.
The check compares your intent with the measurement; if you write down what you
measured, it can never find anything.

## Checks and `blind_spot`

A `label` is a statement that is true when all is well: "Service is running",
"Last backup is less than a day old". `blind_spot` says what a passing check
does not prove. Without it, a green light claims more than it knows.

Weak: no `blind_spot`, or `blind_spot = "none"`
Weak: `blind_spot = "limited"`
Good: `blind_spot = "whether the pages behind it answer"`
Good: `blind_spot = "that the backup can be restored; only its age is checked"`

If you cannot name a blind spot, you have probably not thought about what the
check proves. Think again before writing "none".

### A check must be read-only and cheap

Checks run every five minutes, unattended, forever. So:

- **Read-only.** A check never changes anything, never restarts, creates,
  deletes, mounts, unlocks or writes. `systemctl is-active x` is a check;
  `systemctl restart x` is not.
- **Cheap.** It finishes in a second or two and does not scan disks, hash large
  trees, run backups, hit the internet or wake sleeping disks. A command that
  takes ten seconds is a check that makes the machine slower every five
  minutes.
- **No credentials.** It does not need a password, does not print one and does
  not run with a secret in the command line.
- **Exit code 0 means fine.** Use `expect` for a pattern that must appear in
  the output. Use `show = true` only for a reading you want to see even when
  all is well, such as the age of the last backup.
- Do not run `sudo` in a check unless you know it is allowed without a
  password; a check that waits for one hangs.

## Steps

`[[steps]]` are what to do when it does not work, in order, cheapest and safest
first: look, then test, then change. Each `text` says what the step is for;
`run` shows the command that goes with it and must be read-only, because it is
displayed as something to copy. A step that changes things belongs in an
action, with an `effect`.

Weak: `text = "Restart it."`
Good: `text = "Test the configuration. This changes nothing. If it reports an
error, do not reload."`

## Actions: `level` and `effect`

Every action has a level. The level decides who may press the button.

| Level | Use when | What PiHub does |
|---|---|---|
| `read` | the command changes nothing and costs little (show a log tail, list mounts) | runs at once after login |
| `change` | the command changes something, but undoably and without risk to data (reload a web server, unlock one address) | asks first, names the effect |
| `copy` | anything else | never runs; shows the command for the owner to copy |

**When in doubt, choose `copy`.** In particular, these are always `copy`:

- anything that can **destroy or overwrite data**: deleting, formatting,
  pruning (`docker system prune`), `rm`, moving over existing files, changing
  `/etc/fstab`, dropping a database
- anything that can **tear down a running backup**, restart a service that a
  backup depends on, or unmount a backup disk
- anything that **restarts or powers off the machine**, or restarts the
  network the owner is connected through
- anything that **asks for or needs credentials** (a password prompt, a
  `sudo` that needs one, a login to a cloud service)
- anything that **opens the firewall** or changes who can reach the machine

Also never write as an action:

- **interactive commands** (editors, `less`, `top`, `sudo` that prompts, `ssh`
  into somewhere): the runner has no terminal, so they hang until the timeout
- **endless commands** (`tail -f`, `journalctl -f`, `watch`, `ping` without
  `-c`): use a bounded form (`journalctl -n 50`, `ping -c 3`). Set `timeout`
  as short as the job allows; after it the command and what it started are
  ended.
- **pipes into a shell** or commands built from text somebody else supplies. A
  value comes only through `choose`, from a list PiHub measures itself.

`effect` is for `change` and `copy`. It says what happens to people and
services when the command runs, in terms they feel. It names who notices, for
how long, and what is not undone.

Weak: `effect = "Reloads nginx."`
Weak: `effect = "Restarts the service."`
Good: `effect = "No interruption. The first answers afterwards may still come
from the old state."`
Good: `effect = "The media server is gone for about half a minute. Anyone
watching something is thrown out and has to start again."`
Good (copy): `effect = "Everything in the folder is gone for good. There is no
bin. Run this only in a terminal, and only after you have looked at the
list."`

A `label` is what the button says: a verb and an object. "Reload the web
server", not "Reload".

## `switch`

If the owner turns something off on purpose, tell PiHub which unit does it:
`switch = "backup.target"`. When that unit is inactive, the card shows "off, as
intended" in grey instead of red. Without it, a service that was switched off
on purpose shows up as a problem every five minutes until the owner stops
looking at the page.

## `todo`

A known open point, in one or two sentences, written so that the owner knows
what to do and why it is still open: `todo = "Test the restore from the backup
once. Nobody has tried it yet."` The card stays yellow until the line is
removed. Do not use it for wishes; use it for things that are known to be
unfinished and that you do not want to forget. Remove it when it is done.

## Never put secrets into a card

Not in `about`, `run`, `places`, `links` or anywhere else: no passwords, tokens,
keys, `.env` contents, pre-signed addresses, or the names of files that only
exist to hold them. Cards are shown on the page without a login in display
mode, may be copied into bug reports, and are run as commands.

- Write where a secret lives only as `caution` on a place, and only the
  path: `{ path = "~/backup/keyfile", what = "The key for the cloud copy",
  caution = "Without it the cloud copy is useless. Keep a second copy in your
  password manager. Never copy or print it." }`
- A command that needs a secret should read it from the place itself (an
  agent, a file with restricted rights, the program's own config), not get it
  on the command line.
- If you are not sure whether something is secret, treat it as secret.

## A complete small example

```toml
schema = 1
id = "photos"
name = "Photo library"
area = "home"
summary = "Keeps and shows the family photos"
about = "Stores the photos from all phones and shows them in the browser. If it is down, new photos stay on the phones and nothing is lost. The photos themselves are on the data disk."
units = ["photos.service"]
needs = ["datadisk"]

[[ports]]
port = 2283
reach = ["lan"]
why = "The photo page for the home network. It is not meant to be reachable from the internet."

[[checks]]
label = "Service is running"
run = "systemctl is-active photos"
blind_spot = "whether the page answers or the phones can upload"

[[steps]]
text = "Look at the last lines of the log. This changes nothing."
run = "journalctl -u photos -n 30 --no-pager"

[[actions]]
label = "Show the last log lines"
run = "journalctl -u photos -n 50 --no-pager"
level = "read"
timeout = 20

[[actions]]
label = "Restart the photo library"
run = "sudo systemctl restart photos"
level = "change"
effect = "The photo page is gone for about half a minute. A running upload from a phone is interrupted and continues later."
```

## Before you hand in a card

- Could the owner act on it half asleep?
- Does every check name its blind spot?
- Is every command read-only, bounded and free of secrets?
- Would you press each `change` button yourself, knowing its `effect`?
- Is anything uncertain? Then leave the action out and say so in `about`.
