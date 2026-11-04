#!/bin/zsh
# Remove minertimer:  sudo zsh uninstall.sh
# With --keep-config the configuration and today's counters are kept.
set -u
LABEL="de.dieoma76.minertimer"
APP_DIR="/Library/Application Support/minertimer"

if [[ $EUID -ne 0 ]]; then echo "Please run with sudo."; exit 1; fi

launchctl bootout "system/$LABEL" 2>/dev/null || true
rm -f "/Library/LaunchDaemons/$LABEL.plist"
if [[ "${1:-}" == "--keep-config" ]]; then
  rm -f "$APP_DIR/minertimer.py" "$APP_DIR/minertimer.conf.example"
  echo "Service removed, configuration and counters kept."
else
  rm -rf "$APP_DIR" /var/db/minertimer
  echo "minertimer removed completely (logs stay in /var/log/minertimer*.log)."
fi
echo "Note: Home Assistant entities do not disappear on their own."
echo "Delete the device in Home Assistant under Settings > Devices > MQTT."
