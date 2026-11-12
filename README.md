# PiHub

A command centre for a Raspberry Pi home server that explains itself.

PiHub is for the person who owns a small home server and wants to understand
it, also on a day when no AI assistant is at hand. Scripts scan the machine,
Claude Code writes a short plain-language description of each service once,
and from then on a small web page shows what runs, what is wrong, what that
means and what to do next, without any AI.

## How it works

| Step | Who | What |
|---|---|---|
| Scan | scripts, no AI | read units, timers, containers, ports, firewall, blocked addresses, disks and web sites into an inventory |
| Understand | Claude Code, once | write one card per service: plain-language name, what it needs, intended ports, checks, steps for when it fails, allowed commands |
| Run | a small service, no AI | show the map, run the checks, run allowed commands |

The scan says what is there. The cards say what it means. Where the two
disagree, that disagreement is the finding: a service nobody described, or a
port that should serve the home network and only listens on the machine
itself.

## Set up

On the machine itself, in Claude Code:

```
claude plugin marketplace add Auktoriator/PiHub
claude plugin install pihub@pihub
/pihub:setup
```

Claude Code checks what is there, asks you what is unclear, scans, writes
the cards, lets you review every command that could be run, and only with
your explicit yes sets up the services. You set the password yourself. Later,
`/pihub:update` brings in new versions and describes new services.

## What you have afterwards

A web page with seven views:

- **Map**: all services by area, with traffic-light state and lines for what depends on what
- **What is going on**: findings with meaning and next step, which you can silence with a reason
- **Security**: every listening port, who it is meant for and what the firewall lets in, and who is locked out
- **Storage**: what should be mounted and what is, with a mount button
- **History**: temperature, load, memory and disk for 24 hours or 7 days, plus changes of state and run actions
- **Files**: the places that matter and the commands for everyday file work
- **Day to day**: links to all pages and the commands by area

Logged in, you can run the actions written on the cards: `read` at once,
`change` after a question that names the consequences, `copy` never (it is
only shown, to copy into a terminal).

## Security, honestly

- The web page runs nothing. It passes requests on to a separate process, the
  runner, which checks the password and runs only commands written on the
  cards, read from disk by the runner itself.
- Cards are code. Whoever can write to the data folder can make the runner
  run commands as its user. Review every action (Claude Code does this with
  you during setup).
- There is no HTTPS. Run PiHub on the home network only, restrict the port in
  the firewall to your own network, and never forward it on the router.
- Viewing the page needs no login. Only actions do.
- Your inventory and cards describe your machine. They live in `~/pihub-data`,
  not in this repository.

More in [SECURITY.md](SECURITY.md), including how to report a vulnerability.

## Requirements and limits

- Linux with systemd, Python 3.11 or newer, standard library only. Nothing to
  install for the program itself.
- The scan covers systemd, Docker, ufw, CrowdSec and nginx. Whatever is not
  installed is skipped. Some sections need `sudo` without a password prompt
  (firewall, blocked addresses) and are skipped without it.
- **So far PiHub has been run on exactly one machine: a Raspberry Pi 5 with
  Debian 13.** Other setups are untested; expect rough edges.
- The page has been tested without a browser (a smoke test with Node). Look
  and feel on other devices is not covered.

The scan is written so that no credential can reach the inventory: it does
not call `docker inspect`, records process names but no command lines, and
reads no file contents apart from a fixed set of nginx directives and the
mount points in `/etc/fstab`.

## Without Claude Code

The cards are plain TOML files. Copy [examples/](examples/) to
`~/pihub-data`, write your own following [docs/card-format.md](docs/card-format.md)
and [docs/writing-cards.md](docs/writing-cards.md), and run
`bin/pihub check`. To run it all the time, see
[systemd/README.md](systemd/README.md). Clone the repository to `~/PiHub`
(the unit files expect the program there, but that can be changed).

## Command line

```
bin/pihub scan         read the machine, write the inventory (changes nothing)
bin/pihub check        check the cards and compare them with the inventory
bin/pihub collect      scan, run the checks on the cards, write the current state
bin/pihub serve        the web page on http://127.0.0.1:8096
bin/pihub password     set the password for actions (asks twice in a terminal)
bin/pihub runner       the second process that checks the password and runs actions
bin/pihub list         all cards by area
bin/pihub show <id>    one card: what it is, checks, steps, commands
```

`--data <folder>` (or `$PIHUB_DATA`) chooses the data folder; the default is
`~/pihub-data`. The language is `language = "de"` or `"en"` in
`<data>/settings.toml`. `collect` runs only the commands listed under
`checks` on the cards.

## Documents

- [Card format](docs/card-format.md)
- [Writing cards](docs/writing-cards.md): style guide and safety rules
- [Running PiHub as a service](systemd/README.md)
- [Konzept](docs/konzept.md) (German): the idea, the pages, the stages
- [Security](SECURITY.md)

## Licence

MIT, see [LICENSE](LICENSE).
