"""Tests ohne Mac: System, Uhr und Benutzer werden simuliert.

Aufruf: python3 -m unittest discover -s tests
"""
import datetime as dt
import os
import pwd
import signal
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import minertimer as mt  # noqa: E402

FORGE = ("/Users/kid/Documents/curseforge/minecraft/Install/java/java-runtime-delta/Contents/Home/bin/java "
         "-XstartOnFirstThread -Xss1M -cp /x/forge.jar net.minecraftforge.bootstrap.ForgeBootstrap "
         "--version forge-61.2.0 --gameDir /Users/kid/Documents/curseforge/minecraft/Instances/Minecraft "
         "--launchTarget forge_client")
VANILLA = ("/Users/kid/Library/Application Support/minecraft/runtime/java-runtime-gamma/mac-os/"
           "java-runtime-gamma/jre.bundle/Contents/Home/bin/java -Xmx2G -cp a.jar "
           "net.minecraft.client.main.Main --username kid --version 1.21.4 --assetIndex 19")
MAVEN = "/Users/kid/tools/jdk-17/bin/java -classpath plexus-classworlds.jar org.codehaus.classworlds.Launcher clean package"
LAUNCHER = "/Applications/CurseForge.app/Contents/MacOS/CurseForge --type=renderer minecraft"


class FakeSystem:
    def __init__(self):
        self.procs = []
        self.console = 1000
        self.idle = 0.0
        self.killed = []
        self.notes = []
        self.dead = set()

    def processes(self):
        return [p for p in self.procs if p[0] not in self.dead]

    def console_uid(self):
        return self.console

    USERS = {"kid": 1000, "bro": 1001, "dad": 1002, "other": 2000}
    ADMINS = {"dad"}

    def uid_of(self, name):
        return self.USERS.get(name)

    def name_of(self, uid):
        return {v: k for k, v in self.USERS.items()}.get(uid, str(uid))

    def is_admin(self, name):
        return name in self.ADMINS

    def idle_seconds(self):
        return self.idle

    def alive(self, pid):
        return pid not in self.dead

    def kill(self, pid, sig):
        self.killed.append((pid, sig))

    def notify(self, user, uid, title, message, speak=None):
        self.notes.append((user, title, message))


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def make(conf_text, start=dt.datetime(2026, 11, 3, 15, 0, 0)):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "t.conf")
    with open(path, "w") as f:
        f.write(conf_text)
    cfg = mt.Config(path)
    store = mt.Store(os.path.join(d, "state"))
    sysm = FakeSystem()
    clock = Clock(start.timestamp())
    timer = mt.Timer(cfg, store, sysm, clock=clock,
                     today=lambda: dt.date.fromtimestamp(clock.t))
    return timer, sysm, clock, store, path


CONF = """
[general]
language = de
interval = 15
idle_seconds = 180
warn_minutes = 10,5,1
kill_grace = 20

[user kid]
weekday_minutes = 30
weekend_minutes = 60
"""

FAKE_PW = mock.Mock(pw_uid=1000)


def run(timer, clock, seconds, step=15):
    for _ in range(int(seconds // step)):
        clock.t += step
        timer.tick()


@mock.patch.object(pwd, "getpwnam", return_value=FAKE_PW)
class TimerTests(unittest.TestCase):
    def test_detection(self, _):
        self.assertTrue(mt.is_minecraft(FORGE))
        self.assertTrue(mt.is_minecraft(VANILLA))
        self.assertFalse(mt.is_minecraft(MAVEN))
        self.assertFalse(mt.is_minecraft(LAUNCHER))
        self.assertFalse(mt.is_minecraft("vi /Users/kid/minecraft/notes.txt"))

    def test_counts_only_when_active(self, _):
        timer, s, clock, store, _ = make(CONF)
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 600)
        used = store.user("kid", timer.today())["used"]
        self.assertAlmostEqual(used, 600, delta=1)
        # Idle: zählt nicht
        s.idle = 600
        run(timer, clock, 600)
        self.assertAlmostEqual(store.user("kid", timer.today())["used"], used, delta=1)
        # anderer Benutzer am Bildschirm: zählt nicht
        s.idle = 0
        s.console = 2000
        run(timer, clock, 600)
        self.assertAlmostEqual(store.user("kid", timer.today())["used"], used, delta=1)
        # Spiel eines anderen Benutzers zählt nicht für kid
        s.console = 1000
        s.procs = [(501, 2000, FORGE)]
        run(timer, clock, 300)
        self.assertAlmostEqual(store.user("kid", timer.today())["used"], used, delta=1)

    def test_sleep_gap_not_counted(self, _):
        timer, s, clock, store, _ = make(CONF)
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        clock.t += 3 * 3600   # Mac hat geschlafen
        timer.tick()
        self.assertEqual(store.user("kid", timer.today())["used"], 0)

    def test_warnings_and_kill(self, _):
        timer, s, clock, store, _ = make(CONF)
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 30 * 60 - 15)
        warn_texts = [n[2] for n in s.notes if "endet in" in n[2]]
        self.assertEqual(len(warn_texts), 3, warn_texts)
        self.assertEqual(s.killed, [])
        run(timer, clock, 30)
        self.assertIn((500, signal.SIGTERM), s.killed)
        run(timer, clock, 30)
        self.assertIn((500, signal.SIGKILL), s.killed)
        # Neustart nach Ablauf wird direkt beendet
        s.dead.add(500)
        s.procs.append((600, 1000, VANILLA))
        clock.t += 15
        timer.tick()
        self.assertIn((600, signal.SIGTERM), s.killed)
        # Maven-Build bleibt unberührt
        s.procs.append((700, 1000, MAVEN))
        run(timer, clock, 60)
        self.assertFalse(any(k[0] == 700 for k in s.killed))

    def test_bonus_unblocks(self, _):
        timer, s, clock, store, _ = make(CONF)
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 31 * 60)
        self.assertTrue(s.killed)
        s.killed.clear()
        s.dead.add(500)
        timer.set_bonus("kid", 15, add=True)
        s.procs = [(800, 1000, FORGE)]
        run(timer, clock, 10 * 60)
        self.assertEqual(s.killed, [])
        self.assertEqual(timer.snapshot["kid"]["status"], "spielt")

    def test_stop_now_and_bonus(self, _):
        timer, s, clock, store, _ = make(CONF)
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 5 * 60)
        timer.stop_now("kid")
        timer.tick()
        self.assertIn((500, signal.SIGTERM), s.killed)
        self.assertEqual(timer.snapshot["kid"]["status"], "gestoppt")
        self.assertEqual(timer.snapshot["kid"]["remaining_min"], 0)
        self.assertTrue(any(n[2] == "Minecraft wurde beendet." for n in s.notes))
        # Neustart wird ebenfalls beendet
        s.killed.clear(); s.dead.add(500); s.procs = [(600, 1000, FORGE)]
        run(timer, clock, 15)
        self.assertIn((600, signal.SIGTERM), s.killed)
        # +30 gibt genau 30 min frei, nicht die alte Restzeit dazu
        s.killed.clear(); s.dead.add(600); s.procs = [(700, 1000, FORGE)]
        timer.set_bonus("kid", 30, add=True)
        timer.tick()
        self.assertAlmostEqual(timer.snapshot["kid"]["remaining_min"], 30, delta=0.5)
        run(timer, clock, 29 * 60)
        self.assertEqual(s.killed, [])
        run(timer, clock, 90)
        self.assertIn((700, signal.SIGTERM), s.killed)
        # Zustand übersteht Neustart
        store2 = mt.Store(store.state_dir)
        self.assertTrue(store2.data["users"]["kid"]["forfeit"] > 0)

    def test_stop_now_new_day_resets(self, _):
        timer, s, clock, store, _ = make(CONF)
        timer.stop_now("kid")
        clock.t += 24 * 3600
        timer.tick()
        self.assertEqual(timer.snapshot["kid"]["remaining_min"], 30)  # Mittwoch = Schultag

    def test_weekend_and_holiday(self, _):
        timer, s, clock, store, _ = make(CONF, start=dt.datetime(2026, 11, 7, 10, 0))  # Samstag
        st = store.user("kid", timer.today())
        self.assertEqual(timer.allowed("kid", st), 3600)
        timer2, *_ = make(CONF.replace("[user kid]", "holidays = 2026-12-21..2027-01-06\n\n[user kid]"),
                          start=dt.datetime(2026, 12, 22, 10, 0))
        st2 = timer2.store.user("kid", timer2.today())
        self.assertEqual(timer2.allowed("kid", st2), 3600)

    def test_new_day_resets(self, _):
        timer, s, clock, store, _ = make(CONF, start=dt.datetime(2026, 11, 3, 23, 50))
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 9 * 60)
        self.assertGreater(store.user("kid", timer.today())["used"], 500)
        run(timer, clock, 120)
        self.assertEqual(timer.today(), dt.date(2026, 11, 4))
        self.assertLess(store.user("kid", timer.today())["used"], 200)

    def test_curfew(self, _):
        timer, s, clock, store, _ = make(CONF.replace("weekend_minutes = 60", "weekend_minutes = 60\ncurfew = 20:30-07:00"),
                                         start=dt.datetime(2026, 11, 3, 20, 20))
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        st = store.user("kid", timer.today())
        self.assertAlmostEqual(timer.remaining("kid", st), 600, delta=1)
        run(timer, clock, 11 * 60)
        self.assertIn((500, signal.SIGTERM), s.killed)
        self.assertEqual(timer.snapshot["kid"]["status"], "gesperrt (Sperrzeit)")

    def test_disabled_counts_but_never_kills(self, _):
        timer, s, clock, store, _ = make(CONF)
        timer.set_override("kid", "enabled", "OFF")
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 40 * 60)
        self.assertEqual(s.killed, [])
        self.assertGreater(store.user("kid", timer.today())["used"], 30 * 60)

    def test_override_dropped_when_file_changes(self, _):
        timer, s, clock, store, path = make(CONF)
        timer.set_override("kid", "weekday_minutes", "45")
        self.assertEqual(timer.setting("kid", "weekday_minutes"), 45)
        with open(path) as f:
            text = f.read()
        with open(path, "w") as f:
            f.write(text.replace("weekday_minutes = 30", "weekday_minutes = 60"))
        timer.cfg = mt.Config(path)
        self.assertEqual(timer.setting("kid", "weekday_minutes"), 60)

    def test_state_survives_restart(self, _):
        timer, s, clock, store, path = make(CONF)
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        run(timer, clock, 300)
        store2 = mt.Store(store.state_dir)
        self.assertAlmostEqual(store2.user("kid", timer.today())["used"], 300, delta=1)


MULTI = CONF.replace("[user kid]", "[user kid]\naccounts = *")


@mock.patch.object(pwd, "getpwnam", return_value=FAKE_PW)
class AccountTests(unittest.TestCase):
    def test_second_account_shares_budget(self, _):
        timer, s, clock, store, _ = make(CONF.replace("[user kid]", "[user kid]\naccounts = other"))
        s.console = 2000
        s.procs = [(600, 2000, FORGE)]
        timer.tick()
        run(timer, clock, 600)
        self.assertAlmostEqual(store.user("kid", timer.today())["used"], 600, delta=1)
        self.assertEqual(timer.snapshot["kid"]["account"], "other")
        self.assertEqual(s.notes[0][0], "other")   # message goes to the account in use
        # switch back to the main account: same budget continues
        s.console = 1000
        s.procs = [(601, 1000, FORGE)]
        run(timer, clock, 20 * 60 + 30)
        self.assertIn((601, signal.SIGTERM), s.killed)
        self.assertEqual(timer.snapshot["kid"]["status"], "gesperrt (Limit)")
        # limit reached: starting in the other account is closed right away
        s.console = 2000
        s.procs = [(602, 2000, FORGE)]
        timer.tick()
        self.assertIn((602, signal.SIGTERM), s.killed)

    def test_wildcard_counts_any_non_admin(self, _):
        timer, s, clock, store, _ = make(MULTI)
        s.console = 1001
        s.procs = [(700, 1001, VANILLA)]
        timer.tick()
        run(timer, clock, 300)
        self.assertAlmostEqual(store.user("kid", timer.today())["used"], 300, delta=1)
        self.assertEqual(timer.snapshot["kid"]["account"], "bro")

    def test_wildcard_skips_admins_and_exempt(self, _):
        timer, s, clock, store, _ = make(MULTI.replace("kill_grace = 20",
                                                       "kill_grace = 20\nexempt_users = other"))
        for uid in (1002, 2000):
            s.console = uid
            s.procs = [(800 + uid, uid, FORGE)]
            timer.tick()
            run(timer, clock, 120)
        self.assertEqual(store.user("kid", timer.today())["used"], 0)
        self.assertEqual(timer.snapshot["kid"]["running"], "OFF")
        # without exempt_admins the admin account counts too
        timer, s, clock, store, _ = make(MULTI.replace("kill_grace = 20",
                                                       "kill_grace = 20\nexempt_admins = false"))
        s.console = 1002
        s.procs = [(900, 1002, FORGE)]
        timer.tick()
        run(timer, clock, 120)
        self.assertAlmostEqual(store.user("kid", timer.today())["used"], 120, delta=1)

    def test_background_session_does_not_count_but_is_closed(self, _):
        timer, s, clock, store, _ = make(MULTI)
        s.console = 1001             # bro in front of the screen without a game
        s.procs = [(950, 1000, FORGE)]  # kid's game left in a background session
        timer.tick()
        run(timer, clock, 600)
        self.assertEqual(store.user("kid", timer.today())["used"], 0)
        timer.stop_now("kid")
        timer.tick()
        self.assertIn((950, signal.SIGTERM), s.killed)

    def test_config_validation(self, _):
        with self.assertRaises(ValueError):
            make(CONF + "\n[user bro]\naccounts = kid\n")
        with self.assertRaises(ValueError):
            make(MULTI + "\n[user bro]\naccounts = *\n")


class LanguageTests(unittest.TestCase):
    def test_detect_language(self):
        import plistlib
        d = tempfile.mkdtemp()
        de = os.path.join(d, "de.plist")
        en = os.path.join(d, "en.plist")
        with open(de, "wb") as f:
            plistlib.dump({"AppleLanguages": ["de-DE", "en-US"]}, f)
        with open(en, "wb") as f:
            plistlib.dump({"AppleLanguages": ["en-GB"]}, f)
        self.assertEqual(mt.detect_language(de), "de")
        self.assertEqual(mt.detect_language(en), "en")
        self.assertEqual(mt.detect_language(os.path.join(d, "missing.plist")), "en")

    def test_texts_complete(self):
        self.assertEqual(set(mt.STRINGS["de"]), set(mt.STRINGS["en"]))
        self.assertEqual(mt.Texts("de").minutes(1), "1 Minute")
        self.assertEqual(mt.Texts("en").minutes(5), "5 minutes")
        self.assertEqual(mt.Texts("fr").lang, "en")

    @mock.patch.object(pwd, "getpwnam", return_value=FAKE_PW)
    def test_english_messages(self, _):
        timer, s, clock, store, _ = make(CONF.replace("language = de", "language = en"))
        s.procs = [(500, 1000, FORGE)]
        timer.tick()
        self.assertIn("of play time left today", s.notes[0][2])
        run(timer, clock, 30 * 60 + 30)
        self.assertTrue(any("Minecraft ends in" in n[2] for n in s.notes))
        self.assertIn((500, signal.SIGTERM), s.killed)
        self.assertEqual(timer.snapshot["kid"]["status"], "blocked (limit)")
        self.assertEqual(timer.snapshot["kid"]["day_type"], "school day")


if __name__ == "__main__":
    unittest.main()
