# 🍓 Raspberry Pi – Installations-Anleitung

> 🇬🇧 English version: [PI_SETUP.md](PI_SETUP.md)

So installierst du die **Trading Platform v1** als dauerhaften Hintergrunddienst
auf einem Raspberry Pi (oder jedem anderen Linux-Rechner). Funktioniert mit
**jedem Benutzernamen** – das Setup erkennt deinen Nutzer automatisch.

Du brauchst nur zwei Dateien: `platform.py` und `setup.sh`.

---

## Schritt 1 – Dateien auf den Pi kopieren

Öffne ein Terminal **auf deinem PC** (CMD/PowerShell/Terminal) und wechsle in
den Ordner, in dem `platform.py` und `setup.sh` liegen. Dann:

```bash
# DEIN_USER und PI_IP anpassen, z.B.  pihole@192.168.178.28
scp platform.py setup.sh DEIN_USER@PI_IP:~
```

> Die IP deines Pi findest du dort mit `hostname -I`.

---

## Schritt 2 – Per SSH einloggen und Setup ausführen

```bash
ssh DEIN_USER@PI_IP
```

Dann auf dem Pi:

```bash
# 1) Unsichtbare Windows-Zeilenumbrüche entfernen (wichtig, falls von Windows kopiert!)
sed -i 's/\r$//' setup.sh platform.py

# 2) Setup ausführbar machen und starten
chmod +x setup.sh
sudo bash setup.sh
```

Das Skript:
- erkennt deinen Benutzernamen automatisch (auch unter `sudo`),
- installiert die Python-Abhängigkeit `requests`,
- richtet den systemd-Dienst `trading-platform` für **deinen** Nutzer ein,
- setzt die Dateirechte korrekt.

Am Ende steht **„Setup abgeschlossen!"** samt der IP-Adresse deines Pi.

---

## Schritt 3 – Plattform starten

```bash
sudo systemctl start trading-platform
```

Status prüfen (sollte `active (running)` zeigen):

```bash
sudo systemctl status trading-platform
```

---

## Schritt 4 – Dashboard öffnen & Zugang selbst festlegen

Öffne im Browser (PC oder Handy):

```
http://PI_IP:5000
```

> ⚠️ **Neu ab v1.2:** Es wird **kein** Passwort mehr im Log erzeugt.
> Stattdessen erscheint beim allerersten Aufruf ein **Setup-Assistent**.

Dort legst du **selbst** fest:
- **Benutzername** (frei wählbar, Standard `admin`)
- **Passwort** (mindestens 8 Zeichen, zur Sicherheit doppelt eingeben)

Auf **Speichern & einloggen** klicken. Danach fragt der Browser einmal nach
genau diesen Zugangsdaten (HTTP Basic Auth) – fertig, du bist im Dashboard.

> 💡 Tipp: Richte den Zugang **direkt** nach dem Start ein. Bis das erledigt
> ist, ist das Dashboard im lokalen Netzwerk ungeschützt (wer zuerst kommt,
> vergibt das Passwort). Im Heimnetz unkritisch, aber nicht ewig offen lassen.

---

## Schritt 5 – Erste Einstellungen

Im Dashboard:

1. Tab **SETTINGS** öffnen.
2. Prüfen, dass der **Handelsmodus** auf **DEMO** steht (Papertrading).
3. Deine **Bitget API-Keys** eintragen (und optional Telegram/Discord).
4. Unten auf **EINSTELLUNGEN SPEICHERN** klicken.
5. In die Bot-Tabs (z.B. **GRID** oder **SIGNAL**) gehen und **START** klicken.

Viel Spaß beim Papertraden! 🎉

---

## 🛠️ Nützliche Befehle (Cheatsheet)

| Aktion              | Befehl                                        |
|---------------------|-----------------------------------------------|
| Starten             | `sudo systemctl start trading-platform`       |
| Stoppen             | `sudo systemctl stop trading-platform`        |
| Neu starten         | `sudo systemctl restart trading-platform`     |
| Status              | `sudo systemctl status trading-platform`      |
| Live-Log            | `tail -f ~/trading/platform.log`              |
| Letzte 100 Zeilen   | `tail -100 ~/trading/platform.log`            |

**Bot updaten:** neue `platform.py` nach `~/trading/` kopieren, dann
`sudo systemctl restart trading-platform`. Deine Einstellungen
(`platform_config.json`) bleiben dabei **erhalten**.

---

## ❓ Problembehebung

| Fehler im Terminal | Ursache & Lösung |
|--------------------|------------------|
| `$'\r': command not found` | Windows-Zeilenumbrüche. Lösung: `sed -i 's/\r$//' setup.sh platform.py` und erneut ausführen. |
| `pip3: command not found` | Fehlt selten – das neue `setup.sh` installiert `python3-pip` automatisch. Sonst: `sudo apt install -y python3-pip`. |
| `Permission denied` beim Log | Rechte gehören root. Lösung: `sudo chown -R $USER:$USER ~/trading` und neu starten. |
| Dienst startet, aber **Dashboard nicht erreichbar** | Meist falscher Benutzer im Service. Das neue `setup.sh` behebt das automatisch (nutzt deinen echten User). Prüfen mit `sudo systemctl status trading-platform`. |
| Passwort vergessen / neu setzen | Dienst stoppen, in `~/trading/platform_config.json` bei `"dashboard_password"` den Wert auf `""` setzen, Dienst starten → der Setup-Assistent erscheint erneut. |

**Feste IP empfohlen:** Vergib deinem Pi im Router eine feste IP, damit die
Dashboard-Adresse sich nie ändert.
