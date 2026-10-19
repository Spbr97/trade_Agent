# Running tradedesk on a Linux server

For the repo owner moving tradedesk off the laptop. Everything runs the same way as on
Windows: same jobs, same times (Asia/Kolkata), same logs under `data/logs/`, same health
check. The only differences are systemd timers instead of Task Scheduler, and ntfy phone
pushes instead of desktop pop-ups.

Recommended host: **Oracle Cloud Always Free**, an Ampere A1 VM with Ubuntu 24.04 in Mumbai
or Hyderabad. Size it at 2 OCPU / 12 GB (the reported 2026 free allowance; use 4 / 24 if
your account still shows it) with a 100-150 GB boot volume. The repo's 7.1 GB of data fits
easily. A cheap paid VPS works the same way with the same steps.

## Known limits (read before relying on it)

- No free host promises uptime. A timer missed while the server was down runs once as soon
  as it is back (`Persistent=true`), and the hourly health check pushes any failure to your
  phone. A missed *live session* cannot be replayed, though.
- Oracle may reclaim an Always Free VM that stays idle (under 20% CPU, network and memory
  for 7 days). Upgrading the account to Pay As You Go stops that, and Always Free resources
  stay free. Set a budget alert of Rs 1 so any charge emails you at once.
- Free Ampere capacity in Indian regions is sometimes "out of host capacity". Retry later or
  try the other availability domain.

## Steps

1. **Create the VM** (Oracle console > Compute > Instances > Create). Image: Ubuntu 24.04
   (aarch64), shape: VM.Standard.A1.Flex. Add your SSH public key. Note the public IP.
2. **Get the code onto it.** The repo is private, so add a read-only deploy key on GitHub
   (repo > Settings > Deploy keys), then on the server:
   `git clone git@github.com:<you>/<repo>.git ~/tradedesk && cd ~/tradedesk`
3. **Install**: `sudo bash deploy/linux/install.sh`. This takes about 10 minutes and ends
   by listing the timers.
4. **Move the data. The laptop must stop first.** The forward-pick ledgers are hash-chained
   evidence. Two machines writing two copies would fork them, so only one machine may run at
   a time.
   - On the laptop (PowerShell, as yourself):
     `Get-ScheduledTask -TaskName tradedesk-* | Disable-ScheduledTask`
   - Copy: `scp -r data <user>@<ip>:/tmp/tradedesk-data`
   - On the server:
     `sudo rsync -a /tmp/tradedesk-data/ /opt/tradedesk/data/ && sudo chown -R tradedesk: /opt/tradedesk/data`
   - Leave the laptop's tasks disabled. It is the cold standby: to fall back, stop the
     server's timers, copy `data/` back, and re-enable the tasks. The server's
     `data/m14_m18/deps/catboost` is the Linux build (since 2026-10-20); on the laptop put the
     Windows copy from `data/m14_m18/deps_windows/` back in its place.
5. **Secrets.** This step is open: see "Decision needed" below. Whichever way is chosen,
   you enter the INDstocks Client ID, MPIN and TOTP secret yourself:
   `sudo -u tradedesk -H /opt/tradedesk/.venv/bin/tradedesk auth setup`, then
   `... tradedesk auth check`.
6. **Phone alerts.** Install the ntfy app. Then:
   `sudo -u tradedesk -H /opt/tradedesk/.venv/bin/tradedesk alerts setup-ntfy`
   Subscribe to the topic it prints, then run `... tradedesk alerts ntfy-test`.
7. **Check it.**
   - `systemctl list-timers 'tradedesk-*'` should list 35 timers.
   - `systemctl status tradedesk-dashboard tradedesk-cloudflare-tunnel` should show both
     running.
   - The dashboard link is in `/opt/tradedesk/data/logs/cloudflare_tunnel.log`.
   - After the next :05, `data/reports/health.json` should show `tasks_checked` of 37.

## After a change on Windows

If a task is added or retimed on the laptop, run `python scripts/export_task_schedule.py`
there and commit `deploy/schedule.json`. Then on the server, `git pull` and re-run
`sudo bash deploy/linux/install.sh`. It removes units for jobs that no longer exist.

## Decision needed: where the secrets live

CLAUDE.md requires the TOTP secret to come from a keyring, never from code or config. A
server with no desktop has no OS keychain, so `keyring` needs a file backend:

- **keyrings.cryptfile** (recommended): an encrypted file. Its password is held in a
  root-only systemd credential, so jobs unlock it unattended. Anyone with root on the VM
  can read it either way. That is true of any unattended setup.
- **keyrings.alt plaintext**: simpler, but unencrypted on disk. Not recommended.

Until this is chosen and wired, the jobs that need INDstocks (data loads, live sessions)
fail on the server, and the health check reports them. Crypto (CoinDCX, no credentials)
and the research jobs that read stored data run regardless.
