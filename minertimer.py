#!/usr/bin/python3
"""minertimer: daily time limit for Minecraft Java Edition on macOS.

Runs as a LaunchDaemon (root). Only real play time is counted: Minecraft is
running, one of the child's accounts is the one in front of the screen, and
there was recent mouse or keyboard input. A child can own several macOS
accounts (or all non-admin accounts), which share one daily budget. Optionally connects to Home Assistant via
MQTT discovery (sensors, limits, bonus, switches).

Python standard library only (tested with /usr/bin/python3 3.9 from the
Command Line Tools).
"""

import argparse
import configparser
import datetime as dt
import grp
import json
import logging
import logging.handlers
import os
import plistlib
import pwd
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

VERSION = "2.3.0"

DEFAULT_CONFIG = "/Library/Application Support/minertimer/minertimer.conf"
DEFAULT_STATE_DIR = "/var/db/minertimer"
DEFAULT_LOG = "/var/log/minertimer.log"

log = logging.getLogger("minertimer")

# --------------------------------------------------------------------------
# Texts shown to the child and in Home Assistant (German / English)
# --------------------------------------------------------------------------

STRINGS = {
    "de": {
        "minutes": ("%d Minute", "%d Minuten"),
        "start_title": "Minecraft",
        "start_text": "Heute noch {m} Spielzeit.",
        "warn_title": "Minecraft-Zeit läuft ab",
        "warn_text": "Minecraft endet in {m}. Jetzt speichern!",
        "warn_speak": "Minecraft endet in {m}.",
        "block_title": "Minecraft beendet",
        "block_stopped": "Minecraft wurde beendet.",
        "block_curfew": "Jetzt ist Sperrzeit.",
        "block_limit": "Tageslimit erreicht. Morgen geht es weiter.",
        "block_speak": "Die Minecraft-Zeit ist vorbei.",
        "st_off": "Limit aus", "st_curfew": "gesperrt (Sperrzeit)", "st_stopped": "gestoppt",
        "st_limit": "gesperrt (Limit)", "st_playing": "spielt", "st_idle": "läuft (pausiert)",
        "st_free": "frei",
        "day_free": "frei", "day_school": "Schultag",
        "model": "Minecraft-Zeitlimit",
        "e_used": "Spielzeit heute", "e_remaining": "Restzeit heute", "e_allowed": "Erlaubt heute",
        "e_status": "Status", "e_day_type": "Tagesart", "e_playing": "spielt gerade",
        "e_running": "Minecraft läuft", "e_weekday": "Limit Schultag",
        "e_weekend": "Limit freier Tag", "e_bonus": "Bonus heute", "e_bonus_15": "+15 min Bonus",
        "e_stop": "Stop jetzt", "e_enabled": "Limit aktiv", "e_curfew": "Sperrzeit",
        "e_account": "Spielt im Konto",
    },
    "en": {
        "minutes": ("%d minute", "%d minutes"),
        "start_title": "Minecraft",
        "start_text": "{m} of play time left today.",
        "warn_title": "Minecraft time is running out",
        "warn_text": "Minecraft ends in {m}. Save your game now!",
        "warn_speak": "Minecraft ends in {m}.",
        "block_title": "Minecraft closed",
        "block_stopped": "Minecraft was stopped.",
        "block_curfew": "It is curfew time now.",
        "block_limit": "Daily limit reached. See you tomorrow.",
        "block_speak": "Minecraft time is over.",
        "st_off": "limit off", "st_curfew": "blocked (curfew)", "st_stopped": "stopped",
        "st_limit": "blocked (limit)", "st_playing": "playing", "st_idle": "running (idle)",
        "st_free": "available",
        "day_free": "free day", "day_school": "school day",
        "model": "Minecraft time limit",
        "e_used": "Played today", "e_remaining": "Remaining today", "e_allowed": "Allowed today",
        "e_status": "Status", "e_day_type": "Day type", "e_playing": "Playing now",
        "e_running": "Minecraft running", "e_weekday": "Limit school day",
        "e_weekend": "Limit free day", "e_bonus": "Bonus today", "e_bonus_15": "+15 min bonus",
        "e_stop": "Stop now", "e_enabled": "Limit active", "e_curfew": "Curfew",
        "e_account": "Playing in account",
    },
}

GLOBAL_PREFS = "/Library/Preferences/.GlobalPreferences.plist"


def detect_language(prefs_path=GLOBAL_PREFS):
    """System language of the Mac: 'de' for German, otherwise 'en'."""
    try:
        with open(prefs_path, "rb") as f:
            prefs = plistlib.load(f)
        langs = prefs.get("AppleLanguages") or [prefs.get("AppleLocale", "")]
        first = str(langs[0]).lower() if langs else ""
    except Exception:
        first = ""
    return "de" if first.startswith("de") else "en"


class Texts:
    def __init__(self, lang):
        self.lang = lang if lang in STRINGS else "en"
        self.s = STRINGS[self.lang]

    def __getitem__(self, key):
        return self.s[key]

    def minutes(self, n):
        one, many = self.s["minutes"]
        return (one if n == 1 else many) % n

# --------------------------------------------------------------------------
# Minecraft detection
# --------------------------------------------------------------------------

# Markers in the command line of a Minecraft Java client (vanilla, Forge,
# NeoForge, Fabric, Quilt). Launchers themselves (CurseForge, the official
# launcher, Prism) are not counted, only the game.
MC_MARKERS = re.compile(
    r"(net\.minecraft\.client"
    r"|--assetIndex\b"
    r"|--launchTarget\s+\S*client"
    r"|net\.minecraftforge"
    r"|cpw\.mods"
    r"|net\.neoforged"
    r"|net\.fabricmc"
    r"|org\.quiltmc"
    r"|--gameDir\s+\S*minecraft)",
    re.IGNORECASE,
)
JAVA_HINT = re.compile(r"(^|\s)(-cp|-classpath|-jar|--class-path)\s")


def is_minecraft(command):
    if not MC_MARKERS.search(command):
        return False
    exe = os.path.basename(command.split(" ", 1)[0]).lower()
    return exe.startswith("java") or bool(JAVA_HINT.search(" " + command + " "))


# --------------------------------------------------------------------------
# System access (replaceable in tests)
# --------------------------------------------------------------------------


class MacSystem:
    def __init__(self, dry_run=False, voice=""):
        self.dry_run = dry_run
        self.voice = voice

    def processes(self):
        """List of (pid, uid, command)."""
        out = subprocess.run(
            ["/bin/ps", "-axww", "-o", "pid=,uid=,command="],
            capture_output=True, text=True, timeout=20,
        ).stdout
        procs = []
        for line in out.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                procs.append((int(parts[0]), int(parts[1]), parts[2]))
        return procs

    def uid_of(self, name):
        try:
            return pwd.getpwnam(name).pw_uid
        except KeyError:
            return None

    def name_of(self, uid):
        try:
            return pwd.getpwuid(uid).pw_name
        except KeyError:
            return str(uid)

    def is_admin(self, name):
        try:
            g = grp.getgrnam("admin")
        except KeyError:
            return False
        if name in g.gr_mem:
            return True
        try:
            return pwd.getpwnam(name).pw_gid == g.gr_gid
        except KeyError:
            return False

    def console_uid(self):
        try:
            return os.stat("/dev/console").st_uid
        except OSError:
            return None

    def idle_seconds(self):
        try:
            out = subprocess.run(
                ["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"],
                capture_output=True, text=True, timeout=20,
            ).stdout
        except Exception:
            return 0.0
        m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
        return int(m.group(1)) / 1e9 if m else 0.0

    def alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def kill(self, pid, sig):
        if self.dry_run:
            log.info("[dry-run] would send signal %s to PID %s", sig, pid)
            return
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError as e:
            log.error("Not allowed to terminate PID %s: %s", pid, e)

    def notify(self, user, uid, title, message, speak=None):
        if self.dry_run or os.geteuid() != 0:
            log.info("[message to %s] %s: %s", user, title, message)
            return
        script = 'display notification "%s" with title "%s" sound name "Glass"' % (
            _as_escape(message), _as_escape(title))
        base = ["/bin/launchctl", "asuser", str(uid), "/usr/bin/sudo", "-u", user]
        try:
            subprocess.Popen(base + ["/usr/bin/osascript", "-e", script],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if speak:
                cmd = ["/usr/bin/say"]
                if self.voice:
                    cmd += ["-v", self.voice]
                subprocess.Popen(base + cmd + [speak],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            log.warning("Message to %s failed: %s", user, e)


def _as_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

USER_KEYS = ("weekday_minutes", "weekend_minutes", "curfew", "enabled")
CURFEW_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_curfew(text):
    """'20:30-07:00' -> ((20,30),(7,0)); empty -> None; invalid -> ValueError."""
    if not text or not text.strip():
        return None
    m = CURFEW_RE.match(text)
    if not m:
        raise ValueError("Curfew must be HH:MM-HH:MM: %r" % text)
    h1, m1, h2, m2 = (int(x) for x in m.groups())
    if not (0 <= h1 < 24 and 0 <= h2 < 24 and m1 < 60 and m2 < 60):
        raise ValueError("Invalid time in curfew: %r" % text)
    return (h1, m1), (h2, m2)


def parse_holidays(text):
    ranges = []
    for item in re.split(r"[,\n]", text or ""):
        item = item.strip()
        if not item:
            continue
        if ".." in item:
            a, b = item.split("..", 1)
        else:
            a = b = item
        ranges.append((dt.date.fromisoformat(a.strip()), dt.date.fromisoformat(b.strip())))
    return ranges


def split_list(text):
    return [x for x in re.split(r"[,\s]+", str(text or "")) if x]


def to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "ja", "on", "an")


class Config:
    def __init__(self, path):
        self.path = path
        cp = configparser.ConfigParser(interpolation=None)
        with open(path, encoding="utf-8") as f:
            cp.read_file(f)
        g = cp["general"] if cp.has_section("general") else {}
        self.interval = float(g.get("interval", 15))
        self.idle_seconds = float(g.get("idle_seconds", 180))
        self.warn_minutes = sorted(
            {int(x) for x in str(g.get("warn_minutes", "10,5,1")).split(",") if x.strip()},
            reverse=True)
        self.kill_grace = float(g.get("kill_grace", 20))
        self.holidays = parse_holidays(g.get("holidays", ""))
        self.voice = str(g.get("voice", "")).strip()
        self.exempt_users = set(split_list(g.get("exempt_users", "")))
        self.exempt_admins = to_bool(g.get("exempt_admins", "true"))
        lang = str(g.get("language", "auto")).strip().lower() or "auto"
        self.language = detect_language() if lang == "auto" else lang
        self.texts = Texts(self.language)

        m = cp["mqtt"] if cp.has_section("mqtt") else {}
        self.mqtt = {
            "enabled": to_bool(m.get("enabled", "false")),
            "host": str(m.get("host", "")).strip(),
            "port": int(m.get("port", 1883)),
            "username": str(m.get("username", "")).strip(),
            "password": str(m.get("password", "")),
            "discovery_prefix": str(m.get("discovery_prefix", "homeassistant")).strip(),
            "device_name": str(m.get("device_name", "")).strip(),
        }

        self.users = {}
        for sec in cp.sections():
            if not sec.lower().startswith("user "):
                continue
            name = sec[5:].strip()
            s = cp[sec]
            u = {
                "weekday_minutes": int(s.get("weekday_minutes", 60)),
                "weekend_minutes": int(s.get("weekend_minutes", s.get("weekday_minutes", 60))),
                "curfew": str(s.get("curfew", "")).strip(),
                "enabled": to_bool(s.get("enabled", "true")),
            }
            parse_curfew(u["curfew"])  # validate early
            accounts = split_list(s.get("accounts", ""))
            u["wildcard"] = "*" in accounts
            u["accounts"] = tuple(dict.fromkeys(
                [name] + [a for a in accounts if a != "*" and a != name]))
            self.users[name] = u
        if not self.users:
            raise ValueError("No [user <name>] sections in %s" % path)
        owner = {}
        for name, u in self.users.items():
            for a in u["accounts"]:
                if a in owner:
                    raise ValueError("Account %s is listed for both %s and %s" % (a, owner[a], name))
                owner[a] = name
        wild = [n for n, u in self.users.items() if u["wildcard"]]
        if len(wild) > 1:
            raise ValueError("accounts = * is only allowed for one child, not for %s" % ", ".join(wild))
        self.wildcard_user = wild[0] if wild else None

    def is_free_day(self, day):
        if day.weekday() >= 5:
            return True
        return any(a <= day <= b for a, b in self.holidays)


# --------------------------------------------------------------------------
# State (per user, survives restarts)
# --------------------------------------------------------------------------


class Store:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        self.path = os.path.join(state_dir, "state.json")
        self.data = {"users": {}, "overrides": {}}
        try:
            with open(self.path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data.update(loaded)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.error("State file unreadable (%s), starting with empty state", e)

    def save(self):
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".state.")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def user(self, name, today):
        u = self.data["users"].get(name)
        if not u or u.get("date") != today.isoformat():
            u = {"date": today.isoformat(), "used": 0.0, "bonus": 0.0, "warned": [],
                 "forfeit": 0.0, "stopped": False}
            self.data["users"][name] = u
        return u

    def overrides(self, name):
        return self.data["overrides"].setdefault(name, {})


# --------------------------------------------------------------------------
# Core logic
# --------------------------------------------------------------------------


class Timer:
    def __init__(self, cfg, store, system, clock=time.time, today=dt.date.today):
        self.cfg = cfg
        self.store = store
        self.sys = system
        self.clock = clock
        self.today = today
        self.last_tick = None
        self.signaled = {}       # pid -> time of SIGTERM
        self.running_prev = {}   # user -> bool
        self.last_block_note = {}  # user -> time of last block message
        self.last_log = {}
        self.snapshot = {}       # user -> dict for MQTT

    # ---- effective settings (file + changes from Home Assistant) ----

    def setting(self, user, key):
        base = self.cfg.users[user][key]
        ov = self.store.overrides(user).get(key)
        if ov is None:
            return base
        if ov.get("base") != base:
            # the file was changed since -> the file wins
            self.store.overrides(user).pop(key, None)
            log.info("%s: setting %s taken from config file (%r), Home Assistant value dropped",
                     user, key, base)
            return base
        return ov["value"]

    def set_override(self, user, key, value):
        if user not in self.cfg.users:
            return False
        if key == "curfew":
            parse_curfew(value)
            value = value.strip()
        elif key == "enabled":
            value = to_bool(value)
        elif key in ("weekday_minutes", "weekend_minutes"):
            value = max(0, min(24 * 60, int(float(value))))
        else:
            return False
        self.store.overrides(user)[key] = {"value": value, "base": self.cfg.users[user][key]}
        log.info("%s: %s set to %r (Home Assistant)", user, key, value)
        self.store.save()
        return True

    def set_bonus(self, user, minutes, add=False):
        if user not in self.cfg.users:
            return False
        st = self.store.user(user, self.today())
        minutes = float(minutes)
        st["bonus"] = max(0.0, min(24 * 3600.0, (st["bonus"] if add else 0.0) + minutes * 60))
        if st.get("stopped"):
            # after "stop now", a bonus grants exactly these minutes from now on
            st["forfeit"] = max(0.0, self.allowed(user, st, raw=True) - st["used"] - minutes * 60) \
                if add else max(0.0, self.allowed(user, st, raw=True) - st["used"] - st["bonus"])
            st["stopped"] = False
        # allow warnings again if there is enough time left
        st["warned"] = [w for w in st["warned"] if w * 60 >= self.remaining(user, st)]
        log.info("%s: bonus today is now %d min", user, st["bonus"] // 60)
        self.store.save()
        return True

    # ---- calculations ----

    def allowed(self, user, st, raw=False):
        day = dt.date.fromisoformat(st["date"])
        key = "weekend_minutes" if self.cfg.is_free_day(day) else "weekday_minutes"
        total = self.setting(user, key) * 60.0 + st["bonus"]
        return total if raw else max(0.0, total - st.get("forfeit", 0.0))

    def stop_now(self, user):
        """The rest of today's time is forfeited and a running game is closed.
        A later bonus (+15/+30) then grants exactly those minutes."""
        if user not in self.cfg.users:
            return False
        st = self.store.user(user, self.today())
        st["forfeit"] = max(0.0, self.allowed(user, st, raw=True) - st["used"])
        st["stopped"] = True
        log.info("%s: stop now (Home Assistant), remaining time forfeited", user)
        self.store.save()
        return True

    def curfew_status(self, user, now):
        """(inside_curfew, seconds_until_start_or_None)"""
        cf = parse_curfew(self.setting(user, "curfew"))
        if not cf:
            return False, None
        (h1, m1), (h2, m2) = cf
        t = dt.datetime.fromtimestamp(now)
        cur = t.hour * 60 + t.minute + t.second / 60.0
        start, end = h1 * 60 + m1, h2 * 60 + m2
        if start == end:
            return False, None
        if start < end:
            inside = start <= cur < end
        else:
            inside = cur >= start or cur < end
        if inside:
            return True, 0.0
        until = (start - cur) % (24 * 60)
        return False, until * 60.0

    def remaining(self, user, st, now=None):
        now = self.clock() if now is None else now
        rem = self.allowed(user, st) - st["used"]
        inside, until = self.curfew_status(user, now)
        if inside:
            return 0.0
        if until is not None:
            rem = min(rem, until)
        return max(0.0, rem)

    # ---- one cycle ----

    def exempt(self, name):
        if name in self.cfg.exempt_users:
            return True
        return self.cfg.exempt_admins and self.sys.is_admin(name)

    def assign_accounts(self, procs, now):
        """uid -> child for every account whose Minecraft counts."""
        uid_child = {}
        for child, u in self.cfg.users.items():
            for acc in u["accounts"]:
                uid = self.sys.uid_of(acc)
                if uid is None:
                    if self.last_log.get(("nouser", acc)) is None:
                        log.warning("%s: account %s does not exist on this Mac", child, acc)
                        self.last_log[("nouser", acc)] = now
                    continue
                uid_child[uid] = child
        wild = self.cfg.wildcard_user
        for pid, uid, cmd in procs:
            if uid in uid_child or uid < 500 or not is_minecraft(cmd):
                continue
            name = self.sys.name_of(uid)
            if wild and not self.exempt(name):
                uid_child[uid] = wild
            elif self.last_log.get(("free", pid)) is None:
                log.warning("Minecraft (PID %s) is running in account %s, which is not limited",
                            pid, name)
                self.last_log[("free", pid)] = now
        return uid_child

    def tick(self):
        now = self.clock()
        if self.last_tick is None:
            delta = 0.0
        else:
            delta = now - self.last_tick
            if delta < 0 or delta > self.cfg.interval * 2.5:
                delta = 0.0   # sleep or clock jump: do not count
        self.last_tick = now

        today = self.today()
        procs = self.sys.processes()
        console = self.sys.console_uid()
        idle = None
        changed = False
        uid_child = self.assign_accounts(procs, now)

        for user in self.cfg.users:
            st = self.store.user(user, today)
            uids = {uid for uid, c in uid_child.items() if c == user}
            mc = [p for p in procs if p[1] in uids and is_minecraft(p[2])]
            running = bool(mc)
            accounts = tuple(sorted({self.sys.name_of(p[1]) for p in mc}))
            at_screen = console is not None and any(p[1] == console for p in mc)
            if running and at_screen and idle is None:
                idle = self.sys.idle_seconds()
            playing = running and at_screen and idle is not None and idle < self.cfg.idle_seconds
            # messages go to the account in front of the screen, else to the game's owner
            if at_screen:
                uid = console
            elif mc:
                uid = mc[0][1]
            else:
                uid = self.sys.uid_of(user)
            acct = self.sys.name_of(uid) if uid is not None else user

            prev = self.running_prev.get(user, ())
            if accounts != prev:
                if accounts:
                    log.info("%s: Minecraft running in account %s (PIDs %s)", user,
                             ", ".join(accounts), ",".join(str(p[0]) for p in mc))
                else:
                    log.info("%s: Minecraft stopped", user)
                self.running_prev[user] = accounts
                if running and not prev and to_bool(self.setting(user, "enabled")):
                    rem = self.remaining(user, st, now)
                    if rem > 0:
                        tx = self.cfg.texts
                        self.sys.notify(acct, uid, tx["start_title"],
                                        tx["start_text"].format(m=tx.minutes(round(rem / 60))))

            if playing and delta > 0:
                st["used"] += delta
                changed = True
                if now - self.last_log.get(("play", user), 0) >= 300:
                    log.info("%s: playing, %d min today, %d min left", user,
                             st["used"] // 60, self.remaining(user, st, now) // 60)
                    self.last_log[("play", user)] = now

            enabled = to_bool(self.setting(user, "enabled"))
            rem = self.remaining(user, st, now)
            in_curfew, _ = self.curfew_status(user, now)
            blocked = enabled and rem <= 0

            # warnings (only while actively playing)
            if enabled and playing and not blocked:
                due = [w for w in self.cfg.warn_minutes if rem <= w * 60 and w not in st["warned"]]
                if due:
                    w = min(due)
                    st["warned"] = sorted(set(st["warned"]) | set(due), reverse=True)
                    changed = True
                    tx = self.cfg.texts
                    m = tx.minutes(max(1, round(rem / 60)))
                    log.info("%s: warning %d min (%.0f s left)", user, w, rem)
                    self.sys.notify(acct, uid, tx["warn_title"], tx["warn_text"].format(m=m),
                                    speak=tx["warn_speak"].format(m=m))

            # block
            if blocked and running:
                stopped = st.get("stopped") and not in_curfew
                reason = "curfew" if in_curfew else ("stop now" if stopped else "daily limit reached")
                if now - self.last_block_note.get(user, 0) >= 60:
                    tx = self.cfg.texts
                    if stopped:
                        msg = tx["block_stopped"]
                    elif in_curfew:
                        msg = tx["block_curfew"]
                    else:
                        msg = tx["block_limit"]
                    self.sys.notify(acct, uid, tx["block_title"], msg, speak=tx["block_speak"])
                    self.last_block_note[user] = now
                for pid, _, _ in mc:
                    t0 = self.signaled.get(pid)
                    if t0 is None:
                        log.info("%s: %s, terminating PID %s (SIGTERM)", user, reason, pid)
                        self.sys.kill(pid, signal.SIGTERM)
                        self.signaled[pid] = now
                    elif now - t0 >= self.cfg.kill_grace and self.sys.alive(pid):
                        log.info("%s: PID %s did not exit, SIGKILL", user, pid)
                        self.sys.kill(pid, signal.SIGKILL)
                        self.signaled[pid] = now + 3600  # do not repeat every cycle

            self.snapshot[user] = self._snapshot(user, st, running, playing, enabled,
                                                 blocked, in_curfew, rem, accounts)

        alive_pids = {p[0] for p in procs}
        for pid in list(self.signaled):
            if pid not in alive_pids:
                del self.signaled[pid]
        for key in [k for k in self.last_log if k[0] == "free" and k[1] not in alive_pids]:
            del self.last_log[key]
        if changed:
            self.store.save()

    def _snapshot(self, user, st, running, playing, enabled, blocked, in_curfew, rem, accounts=()):
        day = dt.date.fromisoformat(st["date"])
        tx = self.cfg.texts
        if not enabled:
            status = tx["st_off"]
        elif blocked:
            status = tx["st_curfew"] if in_curfew else (
                tx["st_stopped"] if st.get("stopped") else tx["st_limit"])
        elif playing:
            status = tx["st_playing"]
        elif running:
            status = tx["st_idle"]
        else:
            status = tx["st_free"]
        return {
            "used_min": round(st["used"] / 60, 1),
            "remaining_min": round(rem / 60, 1),
            "allowed_min": round(self.allowed(user, st) / 60, 1),
            "bonus_minutes": round(st["bonus"] / 60),
            "weekday_minutes": self.setting(user, "weekday_minutes"),
            "weekend_minutes": self.setting(user, "weekend_minutes"),
            "curfew": self.setting(user, "curfew"),
            "enabled": "ON" if enabled else "OFF",
            "running": "ON" if running else "OFF",
            "playing": "ON" if playing else "OFF",
            "day_type": tx["day_free"] if self.cfg.is_free_day(day) else tx["day_school"],
            "status": status,
            "account": ", ".join(accounts) or "-",
        }


# --------------------------------------------------------------------------
# Minimal MQTT 3.1.1 client (QoS 0)
# --------------------------------------------------------------------------


def _mqtt_str(s):
    b = s.encode("utf-8")
    return struct.pack("!H", len(b)) + b


def _mqtt_len(n):
    out = bytearray()
    while True:
        d, n = n % 128, n // 128
        out.append(d | (0x80 if n else 0))
        if not n:
            return bytes(out)


class MiniMQTT:
    def __init__(self, host, port, username, password, client_id,
                 will_topic, will_payload, keepalive=60):
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.client_id = client_id
        self.will_topic, self.will_payload = will_topic, will_payload
        self.keepalive = keepalive
        self.sock = None
        self.last_send = 0.0
        self.last_recv = 0.0
        self.last_ping = 0.0
        self.packet_id = 0
        self.on_message = None

    @property
    def connected(self):
        return self.sock is not None

    def connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=10)
        flags = 0x02 | 0x04 | 0x20  # clean session, will, will retain
        payload = _mqtt_str(self.client_id) + _mqtt_str(self.will_topic) + _mqtt_str(self.will_payload)
        if self.username:
            flags |= 0x80
            payload += _mqtt_str(self.username)
            if self.password:
                flags |= 0x40
                payload += _mqtt_str(self.password)
        var = _mqtt_str("MQTT") + bytes([4, flags]) + struct.pack("!H", self.keepalive)
        body = var + payload
        sock.sendall(bytes([0x10]) + _mqtt_len(len(body)) + body)
        self.sock = sock
        ptype, data = self._read_packet()
        if ptype != 0x20 or len(data) < 2 or data[1] != 0:
            self.close()
            raise ConnectionError("MQTT CONNACK refused (code %s)" %
                                  (data[1] if len(data) > 1 else "?"))
        self.last_send = self.last_recv = time.time()
        self.last_ping = 0.0

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None

    def _send(self, data):
        try:
            self.sock.sendall(data)
            self.last_send = time.time()
        except Exception:
            self.close()
            raise

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("MQTT connection closed")
            buf += chunk
        return buf

    def _read_packet(self):
        head = self._recv_exact(1)[0]
        mult, length = 1, 0
        for _ in range(4):
            d = self._recv_exact(1)[0]
            length += (d & 0x7F) * mult
            if not d & 0x80:
                break
            mult *= 128
        data = self._recv_exact(length) if length else b""
        self.last_recv = time.time()
        return head & 0xF0, data if (head & 0xF0) != 0x30 else (head, data)

    def publish(self, topic, payload, retain=False):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        body = _mqtt_str(topic) + payload
        self._send(bytes([0x30 | (1 if retain else 0)]) + _mqtt_len(len(body)) + body)

    def subscribe(self, topics):
        self.packet_id = self.packet_id % 65535 + 1
        body = struct.pack("!H", self.packet_id)
        for t in topics:
            body += _mqtt_str(t) + b"\x00"
        self._send(bytes([0x82]) + _mqtt_len(len(body)) + body)

    def loop(self, timeout):
        """Waits up to timeout seconds for messages and keeps the connection alive."""
        end = time.time() + timeout
        while self.sock:
            now = time.time()
            # ping even while we keep sending but receive nothing
            if (now - self.last_send >= self.keepalive / 2
                    or (now - self.last_recv >= self.keepalive / 2
                        and now - self.last_ping >= self.keepalive / 4)):
                self._send(b"\xc0\x00")  # PINGREQ
                self.last_ping = now
            if now - self.last_recv > self.keepalive * 1.5:
                self.close()
                raise ConnectionError("MQTT broker not responding")
            wait = min(end - now, self.keepalive / 4)
            if wait <= 0:
                return
            r, _, _ = select.select([self.sock], [], [], wait)
            if not r:
                continue
            try:
                ptype, data = self._read_packet()
            except Exception:
                self.close()
                raise
            if ptype == 0x30:
                head, body = data
                tlen = struct.unpack("!H", body[:2])[0]
                topic = body[2:2 + tlen].decode("utf-8", "replace")
                pos = 2 + tlen
                if (head >> 1) & 0x03:  # QoS > 0: skip packet id
                    pos += 2
                if self.on_message:
                    try:
                        self.on_message(topic, body[pos:].decode("utf-8", "replace"))
                    except Exception as e:
                        log.warning("Error handling MQTT message %s: %s", topic, e)


# --------------------------------------------------------------------------
# Home Assistant bridge
# --------------------------------------------------------------------------


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "mac"


def computer_name():
    try:
        out = subprocess.run(["/usr/sbin/scutil", "--get", "ComputerName"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return socket.gethostname().split(".")[0]


class HomeAssistantBridge:
    def __init__(self, cfg, timer):
        self.cfg = cfg
        self.timer = timer
        self.name = cfg.mqtt["device_name"] or computer_name()
        self.host = slug(self.name)
        self.base = "minertimer/%s" % self.host
        self.avail = self.base + "/status"
        self.client = None
        self.next_try = 0.0
        self.backoff = 10.0
        self.last_published = {}

    def _new_client(self):
        m = self.cfg.mqtt
        c = MiniMQTT(m["host"], m["port"], m["username"], m["password"],
                     "minertimer-%s" % self.host, self.avail, "offline")
        c.on_message = self.on_message
        return c

    def ensure(self):
        if not self.cfg.mqtt["enabled"] or not self.cfg.mqtt["host"]:
            return
        if self.client and self.client.connected:
            return
        if time.time() < self.next_try:
            return
        self.client = self._new_client()
        try:
            self.client.connect()
            self.client.publish(self.avail, "online", retain=True)
            self.publish_discovery()
            self.client.subscribe([self.base + "/+/set/+", "%s/status" % self.cfg.mqtt["discovery_prefix"]])
            self.last_published = {}
            self.backoff = 10.0
            log.info("MQTT connected to %s:%s as %s", self.cfg.mqtt["host"],
                     self.cfg.mqtt["port"], self.cfg.mqtt["username"] or "(anonymous)")
        except Exception as e:
            log.warning("MQTT connection failed: %s (retry in %ds)", e, self.backoff)
            self.client.close()
            self.next_try = time.time() + self.backoff
            self.backoff = min(self.backoff * 2, 300)

    def device(self):
        return {
            "identifiers": ["minertimer_%s" % self.host],
            "name": "Minertimer %s" % self.name,
            "manufacturer": "minertimer",
            "model": self.cfg.texts["model"],
            "sw_version": VERSION,
        }

    def publish_discovery(self):
        p = self.cfg.mqtt["discovery_prefix"]
        tx = self.cfg.texts
        for user in self.cfg.users:
            u = slug(user)
            label = user.capitalize()
            st = "%s/%s/state" % (self.base, u)
            setp = "%s/%s/set/" % (self.base, u)

            def ent(component, key, name, extra):
                uid = "minertimer_%s_%s_%s" % (self.host, u, key)
                cfgd = {
                    "name": "%s %s" % (label, name),
                    "unique_id": uid,
                    "object_id": uid,
                    "availability_topic": self.avail,
                    "state_topic": st,
                    "device": self.device(),
                }
                cfgd.update(extra)
                self.client.publish("%s/%s/%s/config" % (p, component, uid), cfgd, retain=True)

            ent("sensor", "used", tx["e_used"], {
                "value_template": "{{ value_json.used_min }}", "unit_of_measurement": "min",
                "icon": "mdi:minecraft", "state_class": "measurement"})
            ent("sensor", "remaining", tx["e_remaining"], {
                "value_template": "{{ value_json.remaining_min }}", "unit_of_measurement": "min",
                "icon": "mdi:timer-sand"})
            ent("sensor", "allowed", tx["e_allowed"], {
                "value_template": "{{ value_json.allowed_min }}", "unit_of_measurement": "min",
                "icon": "mdi:calendar-clock"})
            ent("sensor", "status", tx["e_status"], {
                "value_template": "{{ value_json.status }}", "icon": "mdi:information-outline"})
            ent("sensor", "day_type", tx["e_day_type"], {
                "value_template": "{{ value_json.day_type }}", "icon": "mdi:calendar",
                "entity_category": "diagnostic"})
            ent("binary_sensor", "playing", tx["e_playing"], {
                "value_template": "{{ value_json.playing }}", "icon": "mdi:controller"})
            ent("sensor", "account", tx["e_account"], {
                "value_template": "{{ value_json.account | default('-') }}", "icon": "mdi:account"})
            ent("binary_sensor", "running", tx["e_running"], {
                "value_template": "{{ value_json.running }}", "icon": "mdi:application",
                "entity_category": "diagnostic"})
            for key, name in (("weekday_minutes", tx["e_weekday"]),
                              ("weekend_minutes", tx["e_weekend"])):
                ent("number", key, name, {
                    "value_template": "{{ value_json.%s }}" % key,
                    "command_topic": setp + key, "min": 0, "max": 600, "step": 5,
                    "mode": "box", "unit_of_measurement": "min", "icon": "mdi:timer-cog",
                    "entity_category": "config"})
            ent("number", "bonus_minutes", tx["e_bonus"], {
                "value_template": "{{ value_json.bonus_minutes }}",
                "command_topic": setp + "bonus_minutes", "min": 0, "max": 300, "step": 5,
                "mode": "box", "unit_of_measurement": "min", "icon": "mdi:gift"})
            ent("button", "bonus_15", tx["e_bonus_15"], {
                "command_topic": setp + "add_bonus", "payload_press": "15", "icon": "mdi:plus-circle"})
            ent("button", "stop_now", tx["e_stop"], {
                "command_topic": setp + "stop_now", "payload_press": "STOP", "icon": "mdi:stop-circle"})
            ent("switch", "enabled", tx["e_enabled"], {
                "value_template": "{{ value_json.enabled }}",
                "command_topic": setp + "enabled", "payload_on": "ON", "payload_off": "OFF",
                "state_on": "ON", "state_off": "OFF", "icon": "mdi:shield-lock",
                "entity_category": "config"})
            ent("text", "curfew", tx["e_curfew"], {
                "value_template": "{{ value_json.curfew }}",
                "command_topic": setp + "curfew", "min": 0, "max": 11,
                "pattern": r"^$|^\d{1,2}:\d{2}-\d{1,2}:\d{2}$", "icon": "mdi:sleep",
                "entity_category": "config"})
        log.info("Home Assistant discovery published for %s", ", ".join(self.cfg.users))

    def users_by_slug(self):
        return {slug(u): u for u in self.cfg.users}

    def on_message(self, topic, payload):
        if topic == "%s/status" % self.cfg.mqtt["discovery_prefix"]:
            if payload.strip() == "online":
                log.info("Home Assistant restarted, publishing discovery again")
                self.publish_discovery()
                self.last_published = {}
            return
        parts = topic.split("/")
        # minertimer/<host>/<user>/set/<key>
        if len(parts) != 5 or parts[3] != "set":
            return
        user = self.users_by_slug().get(parts[2])
        if not user:
            return
        key, value = parts[4], payload.strip()
        try:
            if key == "bonus_minutes":
                self.timer.set_bonus(user, float(value))
            elif key == "add_bonus":
                self.timer.set_bonus(user, float(value or 15), add=True)
            elif key == "stop_now":
                if self.timer.stop_now(user):
                    self.timer.tick()   # close the game now, not in the next cycle
            elif key == "enabled":
                self.timer.set_override(user, "enabled", value)
            elif key in ("weekday_minutes", "weekend_minutes", "curfew"):
                self.timer.set_override(user, key, value)
        except ValueError as e:
            log.warning("Invalid value from Home Assistant for %s/%s: %r (%s)", user, key, value, e)
        self.last_published.pop(user, None)  # publish state right away

    def publish_states(self, force=False):
        if not (self.client and self.client.connected):
            return
        now = time.time()
        for user, snap in self.timer.snapshot.items():
            prev = self.last_published.get(user)
            if not force and prev and prev[1] == snap and now - prev[0] < 300:
                continue
            try:
                self.client.publish("%s/%s/state" % (self.base, slug(user)), snap, retain=True)
                self.last_published[user] = (now, dict(snap))
            except Exception as e:
                log.warning("MQTT publish failed: %s", e)
                return

    def wait(self, seconds):
        if self.client and self.client.connected:
            try:
                self.client.loop(seconds)
                return
            except Exception as e:
                log.warning("MQTT connection lost: %s", e)
                self.next_try = time.time() + 10
        time.sleep(max(0.0, seconds))

    def shutdown(self):
        if self.client and self.client.connected:
            try:
                self.client.publish(self.avail, "offline", retain=True)
            except Exception:
                pass
            self.client.close()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def check_permissions(paths):
    """Warns if files that root executes or reads are writable by others."""
    if os.geteuid() != 0:
        return
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_uid != 0 or st.st_mode & 0o022:
            log.error("INSECURE: %s is not owned by root or is writable by others", p)


def describe_users(cfg):
    parts = []
    for u, v in cfg.users.items():
        acc = list(v["accounts"]) + (["all other non-exempt accounts"] if v["wildcard"] else [])
        parts.append("%s %d/%d min (accounts: %s)" % (
            u, v["weekday_minutes"], v["weekend_minutes"], ", ".join(acc)))
    return "; ".join(parts)


def setup_logging(path, verbose):
    handlers = []
    if path:
        try:
            handlers.append(logging.handlers.RotatingFileHandler(
                path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
        except OSError as e:
            print("Cannot use log file %s: %s" % (path, e), file=sys.stderr)
    # under launchd stderr goes to minertimer.err.log, keep that for crashes
    if not handlers or sys.stderr.isatty():
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)


def print_status(state_dir):
    path = os.path.join(state_dir, "state.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for user, st in sorted(data.get("users", {}).items()):
        print("%-12s %s  played %3d min  bonus %3d min  warnings %s" % (
            user, st.get("date"), st.get("used", 0) // 60, st.get("bonus", 0) // 60,
            st.get("warned")))
    if data.get("overrides"):
        print("Changes from Home Assistant:", json.dumps(data["overrides"], ensure_ascii=False))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Minecraft time limit for macOS")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--state-dir", default=DEFAULT_STATE_DIR)
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--dry-run", action="store_true",
                    help="do not terminate or notify, only log")
    ap.add_argument("--once", action="store_true", help="run a single cycle")
    ap.add_argument("--status", action="store_true", help="show today's state")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.status:
        print_status(args.state_dir)
        return 0

    setup_logging(args.log, args.verbose)
    cfg = Config(args.config)
    cfg_mtime = os.stat(args.config).st_mtime
    check_permissions([os.path.abspath(__file__), args.config, os.path.dirname(args.config)])
    store = Store(args.state_dir)
    system = MacSystem(dry_run=args.dry_run, voice=cfg.voice)
    timer = Timer(cfg, store, system)
    bridge = HomeAssistantBridge(cfg, timer)
    log.info("minertimer %s started (%s, language %s), users: %s%s", VERSION, computer_name(), cfg.language,
             describe_users(cfg),
             " [dry-run]" if args.dry_run else "")

    stop = {"flag": False}

    def _stop(signum, frame):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stop["flag"]:
        started = time.time()
        # reload the configuration when it changes
        try:
            mt = os.stat(args.config).st_mtime
            if mt != cfg_mtime:
                new = Config(args.config)
                cfg_mtime = mt
                timer.cfg = bridge.cfg = cfg = new
                system.voice = new.voice
                log.info("Configuration reloaded")
                if bridge.client and bridge.client.connected:
                    bridge.publish_discovery()
        except Exception as e:
            log.error("Configuration invalid, keeping the previous one: %s", e)
            try:
                cfg_mtime = os.stat(args.config).st_mtime
            except OSError:
                pass
        try:
            timer.tick()
        except Exception:
            log.exception("Error in cycle")
        bridge.ensure()
        bridge.publish_states()
        if args.once:
            break
        bridge.wait(max(1.0, cfg.interval - (time.time() - started)))

    bridge.shutdown()
    log.info("minertimer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
