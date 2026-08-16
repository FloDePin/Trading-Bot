# Trading Platform v1 – Deployment Guide

> 🍓 **Einsteiger-freundliche Schritt-für-Schritt-Anleitung für den Raspberry Pi:**
> [PI_SETUP.de.md](PI_SETUP.de.md) · 🇬🇧 [PI_SETUP.md](PI_SETUP.md)
> Dieses Dokument hier ist die technische Referenz.

## Zielsetup

```
Heimnetzwerk
├── Pi / Heimserver  →  platform.py läuft als systemd Service
│   └── :5000        →  Dashboard erreichbar im Netzwerk
└── Telefon / PC     →  Dashboard via http://[PI-IP]:5000
                         Telegram-Benachrichtigungen auf's Handy
```

---

## Schritt 1: Dateien auf den Pi kopieren

```bash
# Vom PC aus (Benutzer + Pi-IP anpassen)
scp platform.py setup.sh YOUR_USER@PI_IP:~
```

Oder per USB-Stick, dann auf dem Pi:
```bash
mkdir ~/trading
cp /media/*/USB/platform.py ~/trading/
```

> Hinweis: Das `setup.sh` erkennt deinen Benutzernamen automatisch – es ist
> **nicht** mehr auf den Nutzer `pi` festgelegt.

---

## Schritt 2: Setup ausführen (einmalig)

```bash
# Auf dem Pi:
cd ~/trading
chmod +x setup.sh
sudo bash setup.sh
```

Das Skript installiert Python-Pakete, richtet den systemd Service
ein und aktiviert den Autostart.

---

## Schritt 3: Plattform starten

```bash
sudo systemctl start trading-platform

# Status prüfen
sudo systemctl status trading-platform

# Live-Log verfolgen
tail -f ~/trading/platform.log
```

---

## Tägliche Befehle

| Aktion            | Befehl                                          |
|-------------------|-------------------------------------------------|
| Starten           | `sudo systemctl start trading-platform`         |
| Stoppen           | `sudo systemctl stop trading-platform`          |
| Neustarten        | `sudo systemctl restart trading-platform`       |
| Status            | `sudo systemctl status trading-platform`        |
| Live-Log          | `tail -f ~/trading/platform.log`                |
| Letzte 100 Zeilen | `tail -100 ~/trading/platform.log`              |

---

## Dashboard-Zugriff

Nach dem Start erreichbar unter:
```
http://[PI-IP-ADRESSE]:5000
```

**Erster Aufruf (ab v1.2):** Es wird kein Passwort mehr ins Log generiert.
Beim ersten Öffnen erscheint ein **Setup-Assistent**, in dem du Benutzername
und Passwort (min. 8 Zeichen) selbst festlegst. Danach gilt HTTP Basic Auth
mit deinen Zugangsdaten. Zum Zurücksetzen: Dienst stoppen, in
`platform_config.json` `"dashboard_password"` auf `""` setzen, Dienst starten.

Pi-IP finden:
```bash
hostname -I
```

Tipp: In deinem Router eine feste IP für den Pi vergeben,
damit die Adresse sich nie ändert.

---

## Alternativer Start via tmux (ohne systemd)

Wenn du kein systemd willst oder schnell testen möchtest:

```bash
# tmux installieren (einmalig)
sudo apt install tmux

# Neue Session starten
tmux new -s trading

# Plattform starten
cd ~/trading && python3 platform.py

# Session im Hintergrund lassen: Strg+B, dann D
# Session wieder aufrufen:
tmux attach -t trading
```

---

## Hinweise zum DCA-Bot

Der DCA-Bot läuft auf dem **Spot-Markt**. Bitget Demo (`paptrading: 1`)
unterstützt Spot nicht vollständig. Zum Testen:

1. Settings → Handelsmodus → LIVE einschalten
2. DCA-Bot Amount auf **5 USDT** setzen
3. Interval auf **1h** für einen schnellen ersten Test

Kein Hebel, kein Liquidationsrisiko – 5 USDT Live ist bei Spot vertretbar.

---

## Wichtige Betriebsnotizen

**Grid Bot – Übersprungene Level:**
Bei schnellen Marktbewegungen kann der Bot in 10 Sekunden
mehrere Level überspringen. Nur das nächste Level wird getriggert.
Das ist sicherer als mehrere Market-Orders auf einmal.

**Tägliche Telegram-Zusammenfassung:**
Kommt jeden Abend um 22:00 Uhr automatisch aufs Handy.
Telegram muss in Settings konfiguriert sein.

**Nach Updates:**
```bash
# Neue platform.py einspielen und Service neu starten
cp platform.py ~/trading/platform.py
sudo systemctl restart trading-platform
```

**Config bleibt erhalten:**
`platform_config.json` wird nie überschrieben – API-Keys und
Settings bleiben bei jedem Update bestehen.
