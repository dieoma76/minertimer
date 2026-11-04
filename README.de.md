# minertimer

**Tägliches Zeitlimit für Minecraft Java Edition auf macOS.** Es funktioniert unabhängig davon, wie das Spiel gestartet wird: über CurseForge, den offiziellen Launcher, Prism und andere.

Die Bildschirmzeit von macOS kann Minecraft Java Edition nicht begrenzen, weil das Spiel als Java-Prozess läuft ([seit Jahren offenes Problem](https://bugs.mojang.com/browse/MCL-14705)). minertimer schließt diese Lücke. Es ist ein kleiner Hintergrunddienst, der als root läuft. Ein Kind ohne Admin-Rechte kann ihn deshalb nicht abschalten.

English version: [README.md](README.md)

## Was es tut

- **Es zählt nur echte Spielzeit.** Eine Sekunde zählt, wenn Minecraft läuft, das begrenzte Konto gerade vor dem Bildschirm sitzt und es in den letzten 3 Minuten Maus- oder Tastatureingaben gab. Ein vergessenes Spiel im Hintergrund, ein schlafender Mac oder ein anderer angemeldeter Benutzer verbrauchen keine Zeit.
- **Es erkennt nur das Spiel.** Erkannt wird der Minecraft-Client (Vanilla, Forge, NeoForge, Fabric, Quilt). Andere Java-Programme wie Entwicklungsumgebungen, Build-Werkzeuge oder ein eigener Server bleiben unberührt.
- **Es warnt vor dem Ende.** Bei 10, 5 und 1 Minute Restzeit bekommt das Kind eine Mitteilung und eine Ansage. Zum Spielstart zeigt eine Mitteilung, wie viel Zeit heute noch übrig ist.
- **Es beendet in zwei Stufen.** Bei Ablauf wird Minecraft erst höflich beendet und nach 20 Sekunden hart. Wird das Spiel neu gestartet, wird es sofort wieder beendet.
- **Die Limits sind flexibel.** Es gibt eigene Limits für Schultage und freie Tage (Wochenende plus eingetragene Ferien). Eine optionale Sperrzeit wie `20:30-07:00` verbietet das Spielen ganz.
- **Mehrere Kinder und Macs sind möglich.** Pro Konto gibt es einen `[user …]`-Abschnitt, pro Mac eine Installation.
- **Deutsch oder Englisch.** Mitteilungen, Ansagen und die Namen der Home-Assistant-Entitäten folgen der Systemsprache des Macs. Alternativ lässt sich die Sprache fest mit `language = de|en` einstellen.
- **Home Assistant ist optional.** Die Anbindung läuft über MQTT-Discovery. Home Assistant zeigt dann Spielzeit, Restzeit und Status. Dort lassen sich die Limits ändern, ein Bonus geben, das Spiel sofort stoppen, die Sperrzeit setzen und das Limit an- und ausschalten. **Ohne Home Assistant funktioniert alles genauso**, eingestellt per Konfigurationsdatei.
- **Es ist manipulationssicher.** Programm, Konfiguration und Zählerstand gehören root. Der Zählerstand übersteht Neustarts, und der Dienst startet nach einem Absturz von selbst neu.
- **Es protokolliert.** Spielstart und -ende, Warnungen und das Beenden stehen in `/var/log/minertimer.log`.

## Voraussetzungen

- macOS mit den **Command Line Tools**, die `/usr/bin/python3` mitbringen. Falls sie fehlen: `xcode-select --install`. Sonst ist nichts nötig.
- ein Admin-Konto für die Installation
- das Kind nutzt ein **Standardkonto**, kein Admin-Konto
- optional ein MQTT-Broker, zum Beispiel die Mosquitto-App in Home Assistant

Getestet mit macOS 15 (Intel) und Minecraft 1.21 über CurseForge/Forge. Rückmeldungen zu anderen Umgebungen sind willkommen.

## Installation

Als Admin (nicht als Kind) im Terminal:

```sh
git clone https://github.com/dieoma76/minertimer.git ~/minertimer
cd ~/minertimer
sudo zsh install.sh
```

Bei der ersten Installation fragt das Skript nach:
- dem zu begrenzenden Konto
- den Minuten für Schultage und freie Tage
- der Sprache
- optional der MQTT-Verbindung (das Passwort wird unsichtbar eingegeben und landet nur in der root-geschützten Konfiguration)

Für ein Update führst du `git pull` und danach noch einmal `sudo zsh install.sh` aus. Die Konfiguration bleibt erhalten.

**Wichtig:** Installiere nie aus einem Ordner, den das Kind verändern kann. Das gilt zum Beispiel für dessen `Öffentlich`-Ordner und für eine Homebrew-Installation, die dem Kind gehört. Das Skript läuft als root.

## Bedienung

| Was | Befehl |
|---|---|
| Einstellungen ändern | `sudo vi "/Library/Application Support/minertimer/minertimer.conf"` (wird automatisch übernommen) |
| Stand heute | `sudo /usr/bin/python3 "/Library/Application Support/minertimer/minertimer.py" --status` |
| Log | `tail -f /var/log/minertimer.log` |
| Läuft der Dienst? | `sudo launchctl print system/de.dieoma76.minertimer \| grep state` |
| Entfernen | `sudo zsh uninstall.sh` (mit `--keep-config` bleiben Einstellungen und Zählerstand erhalten) |

Alle Optionen sind in [`minertimer.conf.example`](minertimer.conf.example) erklärt.

## Home Assistant (optional)

1. Lege einen Benutzer für minertimer an: Einstellungen → Personen → Benutzer. Er soll kein Administrator sein und sich nur aus dem lokalen Netz anmelden dürfen. Die Mosquitto-App akzeptiert Home-Assistant-Benutzer ohne weitere Einrichtung. Bei einem anderen Broker legst du dort einen MQTT-Benutzer an.
2. Beantworte die MQTT-Frage in `install.sh` mit „y“ und gib Broker, Benutzer und Passwort ein. Alternativ trägst du den `[mqtt]`-Abschnitt später in die Konfiguration ein.
3. Es erscheint das Gerät **„Minertimer &lt;Computername&gt;“** mit diesen Entitäten pro begrenztem Konto:

| Entität | Art |
|---|---|
| Spielzeit heute, Restzeit heute, Erlaubt heute, Status | Sensoren |
| spielt gerade | Binärsensor |
| Limit Schultag, Limit freier Tag | Zahl (Konfiguration) |
| Bonus heute | Zahl, wird um Mitternacht zurückgesetzt |
| +15 min Bonus | Taste |
| Stop jetzt | Taste: beendet Minecraft sofort, die Restzeit des Tages verfällt. Ein Bonus danach gibt genau diese Minuten frei. |
| Limit aktiv | Schalter (aus = nur zählen) |
| Sperrzeit | Text `HH:MM-HH:MM`, leer = keine |

Werte, die in Home Assistant geändert werden, gelten, bis derselbe Wert in der Konfigurationsdatei geändert wird. Dann gewinnt wieder die Datei. Ist Home Assistant oder der Broker nicht erreichbar, läuft das Limit mit den zuletzt bekannten Werten weiter.

MQTT-Themen:
- Zustand: `minertimer/<mac>/<konto>/state` (JSON, retained)
- Befehle: `minertimer/<mac>/<konto>/set/<feld>`
- Verfügbarkeit: `minertimer/<mac>/status`

## Grenzen

minertimer ist ein Werkzeug für Eltern, kein Sicherheitsprodukt. Es hilft **nicht**, wenn:
- das Kind ein Admin-Konto hat
- das Kind in einem anderen Konto spielt, für das es keinen `[user …]`-Abschnitt gibt
- das Kind auf einem anderen Gerät spielt

## Hintergrund

minertimer ist aus einem Fork von [soferio/minertimer](https://github.com/soferio/minertimer) entstanden und wurde komplett neu geschrieben, weil das Original:
1. jede Sekunde zählte, in der irgendein Prozess „java“ hieß, für alle Benutzer und auch, während der Mac schlief. Ein über Nacht offenes Spiel hat so das Budget des nächsten Tages verbraucht.
2. jedes Java-Programm beendete, nicht nur Minecraft.
3. Warnungen aus dem Root-Dienst schickte, die deshalb nie in der Sitzung des Kindes ankamen.
4. nur SIGTERM sendete, sodass ein hängendes Spiel weiterlief.
5. kein Log hatte und nach einem Absturz nicht neu startete.
6. je nach Installation Skript- und Logordner für alle beschreibbar hinterließ, sodass das Kind das Root-Skript ersetzen konnte.

## Tests

```sh
python3 -m unittest discover -s tests
```

Die Tests simulieren System, Uhr und Benutzer und laufen deshalb auch unter Linux.

## Lizenz

MIT, wie das Original. Copyright 2024 Soferio Pty Limited für die ursprüngliche Idee. Siehe [`LICENSE`](LICENSE).
