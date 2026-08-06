<div align="center">

# 📈 Trading Platform v1

**Selbst gehostete Multi-Bot-Trading-Plattform für Bitget Futures & Spot, von FloDePin**

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org)

🇬🇧 [English](README.md) | 🇩🇪 **Deutsch**

*Eine quelloffene, selbst gehostete Multi-Bot-Trading-Plattform für Bitget Futures & Spot mit Echtzeit-Web-Dashboard. In reinem Python gebaut – keine Cloud, kein Abo, kein Mittelsmann.*

</div>

---

## Was ist neu

Eine vollständige Sicherheits- und Korrektheitsüberprüfung (2026-07) hat mehrere Probleme behoben und die Plattform gehärtet:

- **Dashboard-Login.** Das Dashboard und seine gesamte API verlangen jetzt einen Login (HTTP Basic Auth). Beim ersten interaktiven Start suchst du dir Benutzername/Passwort selbst in der Konsole aus; jeder folgende Start verlangt die Anmeldung erneut (3 Versuche) oder generiert ein Passwort für Headless‑Starts und protokolliert es einmalig in `platform.log`.
- **Order-Sicherheit.** Orders tragen jetzt einen Idempotenz-Schlüssel (`clientOid`), sodass eine nach einem Netzwerk-Hänger wiederholte Anfrage dieselbe Order nicht mehr doppelt platzieren kann.
- **Grid-Bot-Buchhaltung korrigiert.** Der Grid Bot verfolgt jetzt, was er tatsächlich gekauft hat, und schließt nur echte Positionen, statt bei jedem einzelnen Level eine neue zu eröffnen – die Exponierung bleibt begrenzt.
- **Signal-Bot Win/Loss-Streak-Tracking korrigiert.** Ein toter Codepfad sorgte dafür, dass Win/Loss‑Streaks und die Trade‑Historie für per SL/TP geschlossene Positionen nie protokolliert wurden; das ist nun behoben.
- **Funding Bot ist klar als reines Monitoring gekennzeichnet.** Er verfolgt Funding‑Rate‑Opportunities und schätzt den möglichen Ertrag, platziert aber keine echten Orders. Sein geschätzter PnL ist im Dashboard separat ausgewiesen.
- **Robusterer Notfall‑Stopp.** Emergency Stop wiederholt jetzt einen fehlgeschlagenen Positions‑Close statt nach einem Versuch aufzugeben, und meldet dir namentlich, falls eine Position trotzdem nicht geschlossen werden konnte.
- **Stored‑XSS‑Fixes** bei Alert‑Namen, Bot‑Logs und dem Wirtschaftskalender; Input‑Validierung/Grenzwerte in der API (Backtest‑Zeitraum, Hebel, Grid‑Größe), sodass fehlerhafte Anfragen einen sauberen Fehler zurückliefern.

---

## Was die Plattform kann

Betreibt bis zu 4 automatisierte Trading‑Bots gleichzeitig, jeder auf seinem eigenen Bitget‑Sub‑Account, gesteuert über ein lokales, mit Login gesichertes Browser‑Dashboard. Unterstützt sowohl Demo‑ (Paper Trading) als auch Live‑Trading.

**Signal Bot** – Technische Analyse über mehrere Tokens hinweg. Bewertet 9 Indikatoren und eröffnet Long/Short‑Positionen, wenn die Schwelle erreicht ist, mit ATR‑basiertem Stop‑Loss/Take‑Profit.

**Grid Bot** – Platziert ein Raster aus Buy/Sell‑Orders über eine Preisspanne und schließt, was er tatsächlich gekauft hat. Profitiert von Seitwärtsmärkten. Unterstützt mehrere unabhängige Grid‑Instanzen.

**Funding Bot** – Reines Monitoring: verfolgt Funding‑Rate‑Opportunities über mehrere Tokens und schätzt den möglichen delta‑neutralen Ertrag. Platziert keine echten Orders.

**DCA Bot** – Dollar‑Cost‑Averaging auf dem Bitget‑Spot‑Markt. Kauft einen festen Betrag in regelmäßigen Intervallen.

---

## Features

### Bots
- Signal Bot: Wilder RSI, EMA‑Cross (8/20), MACD, Bollinger Bands, Volume Ratio, Funding Rate, Fear & Greed, CoinGecko News‑Sentiment, Makro‑Blackout
- ATR‑basierter dynamischer Stop‑Loss und Take‑Profit
- Positionsgröße als % des Kontostands
- Korrelations‑Check: max. N gleichzeitige Positionen
- Korrelations‑Filter beim Einstieg: der Signal‑Bot überspringt eine neue Position, die zu stark mit einer bereits offenen korreliert (Schwelle konfigurierbar, standardmäßig an; fail‑open — bei Ausfall der Korrelation‑Berechnung wird der Einstieg nicht verhindert)
- Win/Loss‑Streak‑Tracking
- Order‑Platzierung ist idempotent (sicher gegen doppelte Orders bei Wiederholung)
- Grid Bot verfolgt seine eigene Position und schließt nur, was er gekauft hat (begrenzte Exponierung)
- Multi‑Grid: mehrere unabhängige Grid‑Instanzen
- Notfall‑Stopp wiederholt fehlgeschlagene Positions‑Closes und meldet, welches Symbol betroffen war

### Dashboard
- Login‑geschützt (HTTP Basic Auth) – geführte Einrichtung beim ersten Start, änderbar in den Settings
- Echtzeit‑Übersicht mit Fear & Greed‑Verlauf (30 Tage)
- Pro‑Bot PnL‑Sparklines und Status (Funding‑Bot‑Schätzung separat ausgewiesen, aus der echten Summe ausgeschlossen)
- Offene Positionen über alle Sub‑Accounts hinweg
- Markt‑Tab: Live‑Preise für 15+ Coins
- Wirtschaftskalender mit Finnhub
- Trade‑Historie mit Winrate‑Zusammenfassung
- Backtesting: bis zu 730 Tage, Walk‑Forward, Sharpe Ratio, gebührenbereinigt
- Multi‑Symbol‑Backtest‑Vergleich
- Korrelations‑Matrix als Heatmap — Korrelation der Tagesrenditen deiner Signal‑Bot‑Coins, damit du auf einen Blick siehst, ob deine Positionen wirklich diversifiziert sind oder sich alle gemeinsam bewegen
- Trade‑Timing‑Analyse als Heatmap
- Markt‑Regime & Derivate‑Tab: BTC‑/ETH‑Dominanz, Gesamt‑Market‑Cap und Trending‑Coins (CoinGecko, kostenlos, ohne Key) sowie Open Interest, Funding Rate, Long/Short‑Verhältnis und Liquidationen (Coinalyze, kostenloser API‑Key nötig; fällt sauber aus, wenn kein Key gesetzt ist)
- Alerts via Telegram und/oder Discord
- Zweisprachig: Deutsch / Englisch

---

## Korrelations‑Integration (technisch)
- Der Code berechnet paarweise Pearson‑Korrelationen der täglichen Renditen aus öffentlichen Schlusskursen (compute_correlation).
- Der Signal‑Bot lädt diese Matrix regelmäßig (wenn aktiviert) und nutzt _correlation_conflict(), um Einstiege zu blockieren, die zu stark mit bereits offenen Positionen korrelieren. Das Verhalten wird über die Einstellungen `bots.signal.use_correlation_filter` und `bots.signal.max_correlation` gesteuert.
- Im Dashboard gibt es eine Korrelation‑Ansicht (Korrelation‑Tab), die die Matrix als farbkodierte Tabelle/Heatmap darstellt, sodass du visuell prüfen kannst, ob deine Positionen wirklich diversifiziert sind.
- Relevante Code‑Stellen (platform.py): compute_correlation(...), _correlation_conflict(...), run_signal(...) und die Dashboard‑JS/HTML, die die Matrix rendert (renderCorrelation()).

---

## Installation

### Voraussetzungen
- Python 3.9+
- Windows, Linux oder macOS

### Windows
```bash
pip install requests
python platform.py
```
Öffne `http://localhost:5000`

### Linux / VPS
```bash
bash setup.sh
sudo systemctl start trading-platform
```
Dashboard unter `http://deine-server-ip:5000`

---

## Konfiguration

1. Gehe im Dashboard zu **Settings**
2. Erstelle Sub‑Accounts auf Bitget (einer pro Bot empfohlen)
3. Generiere API‑Keys: nur **Read + Trade** – niemals Withdraw
4. Keys eintragen, **Test Connection** klicken, dann **Save**
5. Im **Demo‑Modus** starten (Standard)

platform_config.json speichert Keys und ist gitignored — niemals committen.

---

## Systemd / Headless Betrieb
- Das Repo enthält trading‑platform.service — kopiere es nach /etc/systemd/system und aktiviere es.
- Headless First Start schreibt das Dashboard‑Passwort einmalig in platform.log (gitignored). Behandle diese Datei als geheim.

---

## Sicherheit

### Warum diese Plattform sicher ist: 100% Open Source + lokale Ausführung

Diese Plattform unterscheidet sich **grundlegend** von cloud‑basierten Trading‑Diensten:

#### ✅ Volle Transparenz
- **Vollständiger Quellcode auf GitHub.** Jede Zeile Code ist auditierbar. Keine versteckten Algorithmen, keine Black Boxes, kein Cloud‑Backend, das Daten sammelt.
- **Eine einzige Python‑Datei (~5200 Zeilen).** Die gesamte Logik steckt in einer lesbaren Datei (`platform.py`). Du kannst genau nachlesen, was sie tut.
- **MIT‑Lizenz.** Komplett frei nutzbar, veränderbar und weiterverteilbar. Du besitzt es.

#### ✅ Verlässt nie deinen Rechner
- **Alle Verarbeitung läuft lokal.** Backtesting, Berechnungen, Bot‑Logik, Dashboard – alles läuft auf *deiner* Maschine.
- **API‑Keys verlassen nie deinen PC.** Sie werden lokal in `platform_config.json` gespeichert (gitignored). Deine Keys werden nur direkt an Bitgets offiziellen API‑Endpunkt (`api.bitget.com`) gesendet.
- **Kein Account nötig.** Keine Anmeldung, keine Telefon‑Verifizierung, kein Risiko einer Kontoschließung, keine sich über Nacht ändernden AGB.
- **Keine Abhängigkeit von externen Diensten für das Kern‑Trading.** Die einzigen externen Aufrufe sind:
  - `api.bitget.com` – deine Exchange‑API
  - `finnhub.io` – kostenlose Marktdaten (optional, für den Wirtschaftskalender)
  - `api.coingecko.com` – Sentiment‑Daten (optional)
  - `api.alternative.me` – Fear & Greed Index (optional)

  Alle optionalen Integrationen lassen sich deaktivieren. **Das Kern‑Trading funktioniert offline, bis auf die Exchange‑Verbindung.**

#### ✅ Keine Überwachung, keine Gebühren, kein Mittelsmann
- Du handelst direkt mit Bitget – keine Middleware, kein Provisionsaufschlag, keine Datensammlung.
- Keine Werbung, kein Upselling, keine Premium‑Stufen.
- Betreibe es auf einem lokalen Rechner, einem Heimserver, einem günstigen VPS – deine Wahl. Kein Vendor‑Lock‑In.

### Kritische Regeln
- **Nutze niemals deinen Haupt‑Bitget‑Account.** Verwende Sub‑Accounts mit begrenztem Guthaben.
- **API‑Keys: nur Read + Trade.** Withdraw niemals aktivieren.
- **Port 5000 nicht öffentlich exponieren**, ohne den Zugriff einzuschränken.
- **`platform_config.json` enthält API‑Keys.** Sie ist gitignored – niemals committen.
- **`platform.log` enthält einmalig beim ersten Start das automatisch generierte Dashboard‑Passwort.** Ebenfalls gitignored – genauso sorgfältig behandeln wie die Config‑Datei.

### Dashboard‑Login
Das Dashboard ist mit HTTP Basic Auth geschützt.

- **Erster Start (interaktives Terminal):** Du wirst gebeten, direkt in der Konsole deinen eigenen Benutzernamen und dein Passwort festzulegen. Passwort leer lassen, um stattdessen eines automatisch erstellen zu lassen.
- **Jeder weitere Start (interaktives Terminal):** `python platform.py` verlangt einen Login in der Konsole (3 Versuche), *bevor* das Dashboard hochfährt – als zusätzliche Hürde neben dem Browser‑Prompt.
- **Start im Hintergrund/headless (systemd, kein angehängtes Terminal):** Es wird nichts abgefragt – ein zufälliges Passwort wird beim ersten Start automatisch generiert und einmalig in `platform.log` protokolliert.

Benutzername/Passwort jederzeit änderbar unter **Settings → Dashboard‑Zugang** im Web‑UI.

### Dashboard‑Zugriff einschränken
```bash
# Nur deine IP erlauben
ufw allow from DEINE.IP.HIER to any port 5000
ufw deny 5000
```

Oder nutze [Tailscale](https://tailscale.com) für private VPN‑Zugriffe ohne Konfigurationsaufwand.

### Was diese Plattform NICHT tut
- Überträgt niemals Keys an externe Dienste
- Tätigt niemals Trades außerhalb der konfigurierten Bot‑Logik
- Alle API‑Aufrufe gehen ausschließlich an `api.bitget.com`
- Meldet sich niemals für Lizenzierung, Telemetrie oder Analytics nach Hause
- Benötigt keine Internetverbindung außer für die Exchange‑Kommunikation

---

## Haftungsausschluss

**Nur für Bildungs‑ und Experimentierzwecke.**

Krypto‑Trading birgt erhebliche finanzielle Risiken. Du kannst dein gesamtes eingesetztes Kapital verlieren. Die Autoren übernehmen keine Verantwortung für finanzielle Verluste. Starte immer im Demo‑Modus.

---

## Architektur

```
platform.py             Single‑File‑Anwendung (~5200 Zeilen)
platform_config.json    API‑Keys und Einstellungen (gitignored)
platform.db             SQLite: Trade‑Historie + PnL‑Snapshots
platform.log            Rotierendes Log (5 MB)
```

---

## Lizenz

MIT – frei nutzbar, veränderbar und weiterverteilbar.

Copyright (c) 2026 Trading Platform Contributors

---

## Wichtiges Setup: One‑Way‑Modus für den Grid Bot

Bevor du den Grid Bot laufen lässt, **musst** du deinen Bitget‑Sub‑Account von Hedge‑Modus auf **One‑Way‑Modus** umstellen.

**Warum:** Bitget Futures ist standardmäßig im Hedge‑Modus (gleichzeitige Long‑ und Short‑Positionen erlaubt). Im Hedge‑Modus eröffnen die Sell‑Orders des Grid Bots neue Short‑Positionen, statt Long‑Positionen zu schließen.

**So stellst du um:**
1. Bitget‑App oder ‑Website öffnen
2. Zum Futures‑Trading auf dem Grid‑Bot‑Sub‑Account gehen
3. Oben rechts → Settings → Position Mode → **One‑Way Mode**

Das ist eine einmalige Einrichtung pro Sub‑Account. Der Signal Bot ist davon nicht betroffen (er verwaltet Positionen explizit über `tradeSide`).

---

## Exchange‑Unterstützung

Aktuell ist die Plattform ausschließlich für **Bitget** (Futures + Spot) gebaut. Die Klasse `BitgetClient` übernimmt Authentifizierung, Order‑Platzierung und Marktdaten direkt über Bitgets REST‑API.

### Weitere Exchanges hinzufügen (Roadmap)

Die Plattform ist so gestaltet, dass die `BitgetClient`‑Klasse durch einen universellen Exchange‑Wrapper mittels [CCXT](https://github.com/ccxt/ccxt) ersetzt werden kann – eine Python‑Bibliothek, die 100+ Exchanges unterstützt.

Geplante Exchanges für zukünftige Unterstützung:

| Exchange | Futures | Spot DCA | Demo / Testnet |
|---|---|---|---|
| **Bitget** | Ja (aktuell) | Ja | Ja (`paptrading`‑Header) |
| **Bybit** | Ja | Ja | Ja (Testnet‑URL) |
| **OKX** | Ja | Ja | Ja (Simulated Trading) |
| **Binance** | Ja | Ja | Ja (Testnet‑URL) |
| **Gate.io** | Ja | Ja | Nein |

### Was sich mit Multi‑Exchange‑Support ändern würde

- Eine neue `ExchangeClient`‑Basisklasse, die `BitgetClient` ersetzt
- Exchange‑Auswahl‑Dropdown in den Settings
- Pro‑Exchange Demo‑Modus‑Handling (jede Exchange implementiert das anders)
- Alles andere – alle Bots, Dashboard, Backtest, Alerts – bleibt identisch

### Exchange‑Support beitragen

Wenn du Support für eine bestimmte Exchange hinzufügen möchtest, sind das die zu implementierenden Kernfunktionen:

```python
client.balance()          # Futures‑Kontostand
client.spot_balance(coin) # Spot‑Guthaben
client.price(symbol)      # Aktueller Marktpreis
client.position(symbol)   # Offene Position für ein Symbol
client.funding_rate(symbol) # Aktuelle Funding Rate
client.klines(symbol, limit) # OHLCV‑Kerzendaten
client.place_order(...)   # Eine Market‑Order platzieren
client.set_leverage(...)  # Hebel für ein Symbol setzen
```

Sobald diese für eine neue Exchange implementiert sind, funktionieren alle vier Bots ohne weitere Änderungen.
