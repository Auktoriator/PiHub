# Security

## Model

PiHub is meant for a home network. It is not hardened for the internet and
must not be exposed to it.

- **The web page runs nothing.** It shows data and passes login and action
  requests on to the runner over a Unix socket. Under systemd it runs
  locked down (see `systemd/pihub.service`) and cannot see the password hash
  or the action log.
- **The runner is the only part that runs commands.** It checks the password,
  reads the cards from disk itself and runs exactly the `run` text written
  on the card. The page never sends a command text. A value for an action
  must come from a list PiHub measures itself. `copy` actions are always
  refused. Every login and action is logged.
- **Cards are code.** Whoever can write to the data folder
  (`~/pihub-data/cards`) can make the runner execute commands with the
  rights of its user, and on many machines that user has `sudo` without a
  password. Keep the folder owner-only and review every action before the
  runner is started.
- **No HTTPS.** The page speaks plain HTTP. Run it on the home network only
  and never forward its port on the router.
- **No login for viewing.** Anyone who can reach the page can read the map,
  ports and findings. Use a firewall rule for your own network only.
- **The scan is read-only** and is written so that no credential reaches the
  inventory (no `docker inspect`, no command lines, no file contents apart
  from a few nginx directives and mount points).
- **Your data stays out of the repository.** Inventory, cards, state and the
  password hash live in `~/pihub-data` (or `$PIHUB_DATA`).

PiHub has been run on one Raspberry Pi 5 with Debian 13. It has not been
audited by a third party.

## Reporting a vulnerability

Please report privately and do not open a public issue. Use the repository's
private reporting: on <https://github.com/Auktoriator/PiHub> open the
**Security** tab and choose **Report a vulnerability**. Describe what you
found, how to reproduce it and what you think the impact is. Do not include
real passwords, addresses or data from your machine.

This is a small project maintained in spare time. Expect an answer within a
few days, not hours.
