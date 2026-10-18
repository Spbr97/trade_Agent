#!/usr/bin/env bash
# One-time (and re-runnable) setup of tradedesk on an Ubuntu 22.04/24.04 server, x86_64 or
# arm64 (Oracle Ampere). Run as a sudo-capable user from a checkout of the repo:
#   sudo bash deploy/linux/install.sh
# It installs packages, creates the `tradedesk` service user, puts the repo at /opt/tradedesk,
# builds the venv, installs cloudflared and the systemd units, and enables the timers.
# It never touches credentials: storing the INDstocks secrets and the ntfy topic is a manual
# step afterwards (docs/cloud-deploy.md), done by the owner, not by a script.
# First install: TRADEDESK_NO_START=1 sudo -E bash deploy/linux/install.sh (units stay off).
set -euo pipefail

ROOT=/opt/tradedesk
USER_NAME=tradedesk
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "== packages and timezone"
apt-get update -y
apt-get install -y git curl rsync ca-certificates
timedatectl set-timezone Asia/Kolkata

echo "== service user and checkout at $ROOT"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash "$USER_NAME"
mkdir -p "$ROOT"
if [ "$SRC" != "$ROOT" ]; then
  # Anchored (/data/): an unanchored "data/" would also skip the src/tradedesk/data package.
  rsync -a --delete --exclude /data/ --exclude /.venv/ "$SRC"/ "$ROOT"/
fi
mkdir -p "$ROOT/data/logs" "$ROOT/data/reports"
chown -R "$USER_NAME:$USER_NAME" "$ROOT"

echo "== uv and the virtualenv (Python 3.12, same pins as Windows)"
if ! sudo -u "$USER_NAME" -H bash -lc 'command -v uv' >/dev/null 2>&1; then
  sudo -u "$USER_NAME" -H bash -lc 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
sudo -u "$USER_NAME" -H bash -lc "cd $ROOT && ~/.local/bin/uv sync --python 3.12"

echo "== cloudflared"
if [ ! -x /usr/bin/cloudflared ]; then
  arch="$(dpkg --print-architecture)"
  curl -fsSL -o /tmp/cloudflared.deb \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}.deb"
  dpkg -i /tmp/cloudflared.deb
fi
# The unit runs /usr/local/bin/cloudflared. Link to the package's fixed path every run:
# `command -v` can resolve to the link itself and leave it pointing at itself.
ln -sfn /usr/bin/cloudflared /usr/local/bin/cloudflared

echo "== root-only secrets file (read by systemd, never by the service user directly)"
# 0711: services may pass through to their own folders (oci/, owned by the guard user), but
# cannot list it or read env, which stays 0600 root and is read by systemd itself.
install -d -m 0711 /etc/tradedesk
[ -e /etc/tradedesk/env ] || install -m 0600 /dev/null /etc/tradedesk/env
chmod 600 /etc/tradedesk/env
# The INDstocks login lives in an encrypted keyring file. Its password sits only here, is
# generated once on this machine, and is never printed. Anyone with root can read it, which
# is true of any unattended setup.
sed -i '/^PYTHON_KEYRING_BACKEND=/d' /etc/tradedesk/env
echo 'PYTHON_KEYRING_BACKEND=tradedesk.server_keyring.EnvPasswordKeyring' >> /etc/tradedesk/env
grep -q '^KEYRING_CRYPTFILE_PASSWORD=' /etc/tradedesk/env ||
  echo "KEYRING_CRYPTFILE_PASSWORD=$(head -c 32 /dev/urandom | base64 | tr -d '=+/\n')" >> /etc/tradedesk/env
# `tradedesk-cli auth setup` etc. run as the service user with that environment, on a real
# terminal so hidden prompts work (a plain `sudo -u tradedesk` would not see the password).
cat > /usr/local/bin/tradedesk-cli <<'WRAP'
#!/usr/bin/env bash
exec sudo systemd-run -t --quiet --wait --collect -p User=tradedesk   -p EnvironmentFile=/etc/tradedesk/env -p WorkingDirectory=/opt/tradedesk   /opt/tradedesk/.venv/bin/tradedesk "$@"
WRAP
chmod 755 /usr/local/bin/tradedesk-cli

echo "== systemd units"
units="$(mktemp -d)"
chown "$USER_NAME" "$units"  # rendered as the service user, so it must own the dir
sudo -u "$USER_NAME" "$ROOT/.venv/bin/python" "$ROOT/deploy/linux/render_units.py" \
  --root "$ROOT" --user "$USER_NAME" --out "$units"
# Remove units for jobs that no longer exist, then install the fresh set.
for old in /etc/systemd/system/tradedesk-*.service /etc/systemd/system/tradedesk-*.timer; do
  [ -e "$old" ] || continue
  [ -e "$units/$(basename "$old")" ] || { systemctl disable --now "$(basename "$old")" || true; rm -f "$old"; }
done
install -m 0644 "$units"/* /etc/systemd/system/
systemctl daemon-reload
if [ "${TRADEDESK_NO_START:-0}" = "1" ]; then
  # First install: leave everything off until the data is copied over, so no job writes a
  # fresh ledger that forks the hash-chained evidence. Re-run without the flag to start.
  echo "== units installed but NOT started (TRADEDESK_NO_START=1)"
else
  systemctl enable --now tradedesk-dashboard.service tradedesk-cloudflare-tunnel.service
  for timer in "$units"/*.timer; do systemctl enable --now "$(basename "$timer")"; done
fi

echo "== lab base record: re-recorded for the code just deployed (only ever here, on deploy)"
if [ -f "$ROOT/data/m14_m18/base_manifest.json" ]; then
  sudo -u "$USER_NAME" "$ROOT/.venv/bin/python" "$ROOT/deploy/linux/refresh_base_manifest.py"     --src "$ROOT" --root "$ROOT"
else
  echo "   no lab data yet: the record is created after the data copy, on the next deploy"
fi

echo "== links for Windows-written paths in sealed evidence (data/ must already be in place)"
sudo -u "$USER_NAME" "$ROOT/.venv/bin/python" "$ROOT/deploy/linux/link_windows_paths.py" --root "$ROOT"

echo "== weekly numbers digest (not named tradedesk-*, so the cleanup above keeps it)"
install -m 0644 "$ROOT"/deploy/linux/digest-weekly.service "$ROOT"/deploy/linux/digest-weekly.timer   /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now digest-weekly.timer

echo "== nightly backup (pulled by the laptop), log rotation, dashboard-link push"
install -d -m 0700 -o ubuntu -g ubuntu /home/ubuntu/backups
install -m 0644 "$ROOT"/deploy/linux/logrotate-tradedesk /etc/logrotate.d/tradedesk
for unit in tradedesk-backup dashboard-link; do
  install -m 0644 "$ROOT/deploy/linux/$unit.service" "$ROOT/deploy/linux/$unit.timer" /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now tradedesk-backup.timer dashboard-link.timer

echo "== Oracle billing guard (second watcher, on the always-on machine)"
# Not named tradedesk-*, so the cleanup above never removes it. The timer starts only once the
# owner has put the Oracle API config and key in /etc/tradedesk/oci (docs/cloud-deploy.md).
install -m 0644 "$ROOT"/deploy/linux/oracle-billing-guard.service   "$ROOT"/deploy/linux/oracle-billing-guard.timer /etc/systemd/system/
systemctl daemon-reload
if [ -f /etc/tradedesk/oci/config ]; then
  systemctl enable --now oracle-billing-guard.timer
else
  echo "   no /etc/tradedesk/oci/config yet: guard timer not started"
fi

echo "== done. Next: docs/cloud-deploy.md steps 4-6 (data copy, secrets, ntfy)."
systemctl list-timers 'tradedesk-*' --no-pager | head -15 || true  # head closes the pipe early: exit 141, not a failure
