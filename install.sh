#!/bin/zsh
# Install or update minertimer.
# Run from the repository folder with an administrator account:  sudo zsh install.sh
set -eu

LABEL="de.dieoma76.minertimer"
APP_DIR="/Library/Application Support/minertimer"
CONF="$APP_DIR/minertimer.conf"
STATE_DIR="/var/db/minertimer"
PLIST="/Library/LaunchDaemons/$LABEL.plist"
SRC="${0:A:h}"

if [[ $EUID -ne 0 ]]; then
  echo "Please run with sudo: sudo zsh install.sh"; exit 1
fi

# The source must not be writable by the child, otherwise their code would end up running as root
src_owner=$(stat -f %Su "$SRC/minertimer.py")
if [[ "$src_owner" != "root" && "$src_owner" != "${SUDO_USER:-root}" ]]; then
  echo "WARNING: $SRC/minertimer.py is owned by '$src_owner', not by you (${SUDO_USER:-root})."
  echo "Only install from your own clone. Aborting."; exit 1
fi

# Python from the Command Line Tools (owned by root)
if ! xcode-select -p >/dev/null 2>&1 || ! /usr/bin/python3 -c 'import sys; assert sys.version_info >= (3, 8)' 2>/dev/null; then
  echo "Python 3 is missing. Install the Command Line Tools first: xcode-select --install"; exit 1
fi

echo "== Removing the original soferio minertimer, if present"
launchctl bootout system/com.soferio.minertimer_daily_timer 2>/dev/null || true
rm -f /Library/LaunchDaemons/com.soferio.minertimer_daily_timer.plist
rm -rf /Users/Shared/minertimer /var/lib/minertimer

echo "== Installing files"
install -d -o root -g wheel -m 755 "$APP_DIR"
install -o root -g wheel -m 755 "$SRC/minertimer.py" "$APP_DIR/minertimer.py"
install -o root -g wheel -m 644 "$SRC/minertimer.conf.example" "$APP_DIR/minertimer.conf.example"
install -d -o root -g wheel -m 700 "$STATE_DIR"
touch /var/log/minertimer.log /var/log/minertimer.err.log
chown root:wheel /var/log/minertimer.log /var/log/minertimer.err.log
chmod 644 /var/log/minertimer.log /var/log/minertimer.err.log

# setconf <file> <section> <key> <value>: sets a key inside a section of the config file
setconf() {
  /usr/bin/python3 -I - "$@" <<'PY'
import sys, re
path, section, key, val = sys.argv[1:5]
text = open(path, encoding="utf-8").read()
pat = r"(?m)^(\[%s\][\s\S]*?^)%s\s*=.*$" % (re.escape(section), re.escape(key))
text, n = re.subn(pat, lambda m: m.group(1) + "%s = %s" % (key, val), text, count=1)
if not n:
    sys.exit("setting %s/%s not found" % (section, key))
open(path, "w", encoding="utf-8").write(text)
PY
}

if [[ ! -f "$CONF" ]]; then
  echo "== First-time setup"
  install -o root -g wheel -m 600 "$SRC/minertimer.conf.example" "$CONF"
  kid=""
  while [[ -z "$kid" ]]; do
    read "kid?Which macOS account should be limited (short name, e.g. emma): "
  done
  if ! id "$kid" >/dev/null 2>&1; then echo "Account '$kid' does not exist. Aborting."; rm -f "$CONF"; exit 1; fi
  if dsmemberutil checkmembership -U "$kid" -G admin 2>/dev/null | grep -q "is a member"; then
    echo "WARNING: '$kid' is an administrator and could remove minertimer."
  fi
  sed -i '' "s/^\[user CHILD\]/[user $kid]/" "$CONF"
  read "wd?Minutes on school days (Mon-Fri) [60]: "
  read "we?Minutes on free days (Sat, Sun, holidays) [120]: "
  setconf "$CONF" "user $kid" weekday_minutes "${wd:-60}"
  setconf "$CONF" "user $kid" weekend_minutes "${we:-120}"
  read "lang?Language for messages: auto, de or en [auto]: "
  setconf "$CONF" general language "${lang:-auto}"
  read "mq?Connect to Home Assistant via MQTT? [y/N]: "
  if [[ "${mq:-n}" == [yYjJ]* ]]; then
    mqhost=""
    while [[ -z "$mqhost" ]]; do read "mqhost?MQTT broker host or IP: "; done
    read "mquser?MQTT user [minertimer]: "
    read -s "mqpass?MQTT password (hidden): "; echo
    setconf "$CONF" mqtt enabled true
    setconf "$CONF" mqtt host "$mqhost"
    setconf "$CONF" mqtt username "${mquser:-minertimer}"
    setconf "$CONF" mqtt password "$mqpass"
  fi
  echo "Settings are in $CONF. Change them with: sudo vi \"$CONF\""
else
  echo "== Keeping the existing configuration: $CONF"
fi
chown root:wheel "$CONF"; chmod 600 "$CONF"

echo "== Starting the background service"
install -o root -g wheel -m 644 "$SRC/$LABEL.plist" "$PLIST"
launchctl bootout "system/$LABEL" 2>/dev/null || true
# bootout is asynchronous: wait until the old service is really gone
for i in {1..20}; do
  launchctl print "system/$LABEL" >/dev/null 2>&1 || break
  sleep 1
done
launchctl enable "system/$LABEL"
ok=0
for i in {1..5}; do
  if launchctl bootstrap system "$PLIST" 2>/dev/null; then ok=1; break; fi
  # already loaded? that is fine too
  launchctl print "system/$LABEL" >/dev/null 2>&1 && { ok=1; break; }
  echo "  Cannot start yet, retrying ($i/5) ..."; sleep 2
done
if [[ $ok -ne 1 ]]; then
  echo "ERROR: the service could not be started. Please run install.sh again."; exit 1
fi
sleep 4

if launchctl print "system/$LABEL" 2>/dev/null | grep -q "state = running"; then
  echo "OK: minertimer is running."
else
  echo "ERROR: the service is not running. See /var/log/minertimer.err.log"
fi
tail -n 5 /var/log/minertimer.log || true
echo
echo "Today's state:  sudo /usr/bin/python3 \"$APP_DIR/minertimer.py\" --status"
echo "Log:            tail -f /var/log/minertimer.log"
