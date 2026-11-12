# Running PiHub all the time

Four units: the page (`pihub.service`), the runner
(`pihub-runner.service`), the collector (`pihub-collect.service`) and its
timer. Replace the four placeholders,
copy them to `/etc/systemd/system/` and switch them on:

```
for f in pihub.service pihub-runner.service pihub-collect.service pihub-collect.timer; do
  sed -e "s#@USER@#$USER#g" -e "s#@REPO@#$HOME/PiHub#g" \
      -e "s#@DATA@#$HOME/pihub-data#g" -e "s#@PORT@#8096#g" "systemd/$f" \
    | sudo tee "/etc/systemd/system/$f" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now pihub-runner.service pihub.service pihub-collect.timer
```

The runner is the only part that runs actions from the cards. It listens on
a Unix socket (`/run/pihub/runner.sock`, owner only) and is not locked down,
because the actions need sudo. The page passes login and action requests on
to it and runs nothing itself. Actions stay off until a password is set;
without a runner the page is display only:

```
bin/pihub --data ~/pihub-data password
```

The command asks twice in the terminal and never takes the password as an
argument. The runner reads it at the next login, no restart needed. Every
action and every login is logged in `<data>/private/actions.log` (without the output
of the commands); the last 5000 entries are kept. The password hash
(`login.json`) and the log live in `<data>/private/` (mode 0700), which the web
service cannot see. Setting a new password ends all running sessions.

The page listens on all interfaces, so a firewall has to say who gets in.
With ufw, for the home network only:

```
sudo ufw allow from <YOUR-NETWORK>/24 to any port 8096 proto tcp comment 'PiHub'
```

Never forward the port of the page on the router, also not with a password:
the password protects the actions, but the page is plain HTTP.

To remove: `sudo systemctl disable --now pihub-runner.service pihub.service pihub-collect.timer`,
delete the four files, `sudo systemctl daemon-reload`, delete the firewall rule.
