---
name: update
description: "Update an existing PiHub setup: pull the new program, rescan the machine, write cards for new services, ask about vanished ones, have changed actions re-confirmed, or improve a single card. Use when the user wants to update PiHub, refresh the map after changing the machine, or fix or reword one card."
disable-model-invocation: true
---

# PiHub: update

PiHub is already set up (program in `~/PiHub`, data in `~/pihub-data`). If
either is missing, stop and suggest `/pihub:setup`.

The rules of `/pihub:setup` apply here too: install nothing without asking,
delete nothing (move to `~/tidy-<date>/` instead), never read or write
credentials into cards, never handle a password, keep `~/pihub-data` out of
every repository, prefer a card without actions to one with guessed
commands. Answer in the owner's language (`language` in
`~/pihub-data/settings.toml`); files in the repo stay English.

Ask what the owner wants if it is not clear: a full update, a rescan after
changing the machine, or one card improved. For one card, skip to the last
section.

## 1. Update the program (with consent)

Say what will happen, then ask. Look first:

```
git -C ~/PiHub status --short
git -C ~/PiHub fetch --dry-run 2>&1 | head
git -C ~/PiHub log --oneline HEAD..origin/main | head -20
```

If there are local changes, stop and ask; never discard them. Show the owner
the list of incoming changes in plain words. After a yes:

```
git -C ~/PiHub pull --ff-only
python3 -m unittest discover -s ~/PiHub/tests
```

(Run the tests from `~/PiHub` as working directory if discovery needs it.)
If tests fail, say so and do not restart anything. Read the changes in
`docs/card-format.md` and `systemd/` since the last version: a new card
field or changed unit files may need follow-up.

Restart the services only after another explicit yes, because the page and
the runner are briefly gone and every login ends:

```
sudo systemctl restart pihub-runner.service pihub.service
```

If `systemd/*.service` changed, the installed copies in `/etc/systemd/system/`
do not change by themselves. Show the difference and, with consent, install
them again as in `~/PiHub/systemd/README.md`.

Verify: `systemctl is-active ...` and `curl -s http://127.0.0.1:8096/api/session`
must show `"actions": true`.

## 2. Rescan and compare

```
~/PiHub/bin/pihub scan
~/PiHub/bin/pihub check
```

Read both outputs. `check` lists what no card describes, what cards name
that does not exist anymore, and port notes (intended reach versus actual
binding).

## 3. New things: write cards

For each new unit, container or port the owner cares about, read the same
sources as in setup (documentation the owner wrote, compose files, unit
descriptions; never `.env`, keys, tokens), ask when unclear, then read
`~/PiHub/docs/card-format.md` and `~/PiHub/docs/writing-cards.md` and write
the card. Run `pihub check` after each batch.

## 4. Vanished things: ask, do not delete

For a card whose unit or container no longer exists, ask the owner: removed
on purpose, renamed, or temporarily gone? Do not decide. If it is gone for
good, move the card aside:

```
mkdir -p ~/pihub-data/cards-retired-<date> && mv ~/pihub-data/cards/<id>.toml ~/pihub-data/cards-retired-<date>/
```

Also check that no other card still lists it under `needs`. If it was
switched off on purpose, the card stays and gets a `switch` instead. A port
that changed on purpose gets the card's `ports` updated; one that changed
unexpectedly is a finding to show the owner.

## 5. Changed actions: confirm again

Compare actions before and after (new or edited `[[actions]]`, including
`run`, `level`, `choose`, `timeout`). List every new or changed action in a
table (card, label, exact command, level, effect) and let the owner confirm
or reject each one. A card is executable code, and the runner picks up edits
from disk immediately, so confirmation comes before `pihub collect` or any
restart, not after.

Finish with `pihub check` and `~/PiHub/bin/pihub collect`, and say what
changed, what was checked and what was not (for instance that the page was
not opened in a browser).

## One card only

Show the owner the current card (`~/PiHub/bin/pihub show <id>`), ask what is
wrong with it, reread `~/PiHub/docs/writing-cards.md`, change only that file,
run `pihub check`, and show the result. If the change touches `[[actions]]`,
have it confirmed as in step 5.
