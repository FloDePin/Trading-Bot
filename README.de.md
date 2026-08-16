<div align="center">

# 📈 Trading Platform v1.0

**Selbst gehostete Multi-Bot-Trading-Plattform für Bitget Futures & Spot, von FloDePin**

[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE) [![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://python.org) [![Version](https://img.shields.io/badge/version-1.0-brightgreen)](#changelog)

🇬🇧 [English](README.md) | 🇩🇪 **Deutsch**

🍓 **Raspberry-Pi-Installationsanleitung:** [PI_SETUP.de.md](PI_SETUP.de.md)

*Eine quelloffene, selbst gehostete Multi-Bot-Trading-Plattform für Bitget Futures & Spot mit Echtzeit-Web-Dashboard. In reinem Python gebaut – keine Cloud, kein Abo, kein Mittelsmann.*

</div>

---

## Changelog

### v1.0 (2026-08) — Erstes stabiles Release 🎉

Das erste getaggte, stabile Release — alles darunter ist enthalten.

**Bitget Unified Account (UTA) Unterstützung**
- Erkennt pro API-Key automatisch Classic vs. Unified und routet jeden Aufruf entsprechend (Balance, Positionen, Orders, Hebel, TP/SL, Spot). Neue Bitget-Konten (und Demo-Konten) sind standardmäßig UTA — die Plattform läuft jetzt auf **beiden ohne Config-Änderung** und weiterhin auf Classic-Konten.

**Zuverlässigkeit & Wiederanlauf**
- **Auto-Start** (opt-in pro Bot): nach Neustart/Stromausfall starten die markierten Bots automatisch wieder. Offene Positionen sind zwischenzeitlich durch ihr SL/TP auf Bitget geschützt.
- **Grid-State-Persistenz**: der Grid-Bot (und jede Instanz) läuft nach einem Neustart korrekt weiter (gefüllte Level bleiben erhalten).
- **Live-Reconfig**: Schwelle / Risk / Budget / Faktoren / SL-TP des Signal-Bots in den Settings ändern — greift im nächsten Zyklus, **ohne Stop/Neustart**.
- Thread-sichere State-Dateien (Grid/DCA) und **keine Key-Verluste** mehr beim Seiten-Reload/Update.

**Budget pro Bot** für den Signal-Bot — harte Margin-Obergrenze, damit ein gemeinsames Konto auf die Bots aufgeteilt werden kann.

**Fixes in diesem Release**
- Notfall-Stopp funktioniert jetzt auf Unified-Konten (listete Positionen vorher über den klassischen Endpunkt).
- Der Signal-Bot bucht den Trade jetzt in die DB und aktualisiert die Win/Loss-Streak, wenn er eine Position dreht.
- Ein Dashboard-Crash („Verbindung unterbrochen") entfernt (Zugriff auf ein nicht existierendes Element).
- DCA-Käufe werden jetzt in der Datenbank erfasst (Historie/Timing).

**Setup:** Browser-Setup-Assistent für headless-Installationen, universelles Raspberry-Pi-`setup.sh` (erkennt den Benutzer automatisch) und EN/DE-Pi-Anleitungen — siehe [PI_SETUP.de.md](PI_SETUP.de.md).

---

**Frühere Entwicklungs-Meilensteine (vor 1.0):**

### v1.2 (2026-08)

**Browser-basierte Erst-Einrichtung**
- Beim headless-Start (systemd, kein Terminal) generiert das Dashboard **kein Zufallspasswort mehr**. Stattdessen erscheint beim ersten Aufruf ein **Setup-Assistent**, in dem du Benutzername + Passwort (min. 8 Zeichen) selbst festlegst. Danach gilt normale HTTP Basic Auth mit deinen Zugangsdaten, und der Setup-Endpunkt ist dauerhaft gesperrt.
- Der manuelle Start im Terminal behält die interaktive Erst-Abfrage.
- Login zurücksetzen: Dienst stoppen, in `platform_config.json` `"dashboard_password"` auf `""` setzen, neu starten — der Assistent erscheint erneut.

**Universelles Raspberry-Pi-Setup**
- `setup.sh` erkennt jetzt **Benutzer und Home-Verzeichnis automatisch** (auch unter `sudo`) — nicht mehr fest auf den Nutzer `pi` verdrahtet, funktioniert also auf jedem Pi/Linux unabhängig vom Benutzernamen.
- Neue Schritt-für-Schritt-Anleitungen: [PI_SETUP.de.md](PI_SETUP.de.md) (DE) / [PI_SETUP.md](PI_SETUP.md) (EN), inkl. Problembehebung (Windows-Zeilenumbrüche, fehlendes `pip3`, Rechte).

### v1.1.1 (2026-08)

- Der `pnl_below`-Alarm bezieht jetzt **Multi-Grid-Instanzen** in die Summe ein (wurden vorher stillschweigend ausgelassen).
- Grid-Instanzen senden jetzt **Telegram-/Discord-Benachrichtigungen** bei Trades/Fehlern, wie die Haupt-Bots.
- Alle SQLite-Verbindungen werden per `try/finally` geschlossen — kein Verbindungsleck, wenn eine Abfrage fehlschlägt.

### v1.1 (2026-08)

Ein großes Zuverlässigkeits- und Feature-Release, vor Veröffentlichung vollständig lokal getestet.

**Trading-Logik-Fixes**
- Die Ordergröße formatiert BTC/ETH-Mengen nicht mehr auf `"0"` herunter (was Orders ablehnte) — die Nachkommastellen kommen jetzt dynamisch aus der Mindestmenge des Marktes.
- Grid Bot auf **Crossing-Logik** umgebaut: kauft beim Kreuzen eines Levels nach unten, verkauft (schließt) beim Kreuzen nach oben — kein endloses Nachkaufen mehr bei Oszillation um ein einzelnes Level.
- **Notfall-Stopp** schließt/storniert jetzt auch jede Multi-Grid-Instanz auf ihrem **eigenen Sub-Account** (eigene API-Keys) und storniert nur die tatsächlich gehandelten Symbole (die alte ~250-Symbol-Schleife lief ins Rate-Limit und ließ den Panic scheitern).
- **DCA-Bot-Stand** (investiert / Menge / Käufe / letzter Kauf) wird jetzt auf Platte persistiert — übersteht Neustarts und kauft nicht mehr bei jedem Start sofort.
- **Order-Sanity-Check** vor jedem Einstieg (lehnt 0-Mengen / überdimensionierte Orders ab) und ein **SL/TP-Wächter**, der Stop-Loss/Take-Profit nachrüstet, falls die Börse sie nicht angehängt hat (inspiriert von einem MT5-Community-Bot).

**Neue Signal-Werkzeuge**
- **Korrelations-Matrix** + korrelationsbewusster Einstiegs-Filter (überspringt eine Position, die zu stark mit einer offenen korreliert).
- **ADX-Trendfilter** und **Order-Book-Kauf-/Verkaufsdruck** als Signal-Faktoren.
- **Markt-Regime (CoinGecko)** + **Derivate (Coinalyze)** Dashboard-Tab.
- **Faktor-An/Aus-Tabelle** für den Signal-Bot + **Live-Score-Aufschlüsselung** pro Coin (du siehst genau, welcher Faktor was beiträgt), plus optionaler Langfrist-EMA-Trendfilter.

**Realistischerer Backtest**
- Trifft eine Kerze SL **und** TP, zählt das jetzt als **Verlust** (entfernt den optimistischen Look-Ahead-Bias).
- Gebühren auf das **Notional statt auf den Profit** — die Trade-Historie sieht nicht mehr zu optimistisch aus.
- **Konfigurierbare Positionsgröße %**, und der Multi-Symbol-Backtest berücksichtigt jetzt SL/TP aus dem UI.

**UI / Aufräumen**
- Settings als **zweispaltiges Layout** neu gestaltet; **vollständige Deutsch/Englisch-Abdeckung** über alle Tabs.
- Delistetes **MATIC durch POL** ersetzt (Polygon-Umbenennung).
- Thread-Safety-, XSS- und Gebühren-Buchhaltungs-Fixes; die Backtest-Hilfe weist jetzt darauf hin, dass der Volatilitäts-Circuit-Breaker nicht simuliert wird.

### v1.0 (2026-07)

Erste Härtung / Sicherheitsüberprüfung:

- **Dashboard-Login.** Dashboard und gesamte API verlangen jetzt einen Login (HTTP Basic Auth). Erster interaktiver Start: eigenes Benutzername/Passwort in der Konsole; jeder weitere Start fragt erneut (3 Versuche) oder generiert bei Headless-Start ein Passwort in `platform.log`.
- **Order-Sicherheit.** Orders tragen einen Idempotenz-Schlüssel (`clientOid`) gegen doppelte Orders nach Netzwerk-Hängern.
- **Grid-Bot-Buchhaltung korrigiert.** Der Grid Bot verfolgt, was er gekauft hat, und schließt nur echte Positionen.
- **Signal-Bot Win/Loss-Streak-Tracking korrigiert.** Ein toter Codepfad verhinderte das Protokollieren von Streaks und der Trade-Historie für SL/TP-Closes.
- **Funding Bot klar als Monitoring gekennzeichnet.** Schätzt den Ertrag, platziert aber keine echten Orders; PnL separat ausgewiesen.
- **Robusterer Notfall-Stopp**, plus **Stored-XSS-Fixes** und Input-Validierung in der API.

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
- Signal Bot: Wilder RSI, EMA‑Cross (8/20), MACD, Bollinger Bands, Volume Ratio, ADX (Trendstärke), Order‑Book‑Kaufdruck, Funding Rate, Fear & Greed, CoinGecko News‑Sentiment, Makro‑Blackout
- ADX‑Trendfilter: dämpft das Signal bei fehlendem Trend (weniger Handel im Seitwärts‑Gezappel); abschaltbar, fail‑open
- Order‑Book‑Kaufdruck als Signal‑Faktor: Kauf‑/Verkaufsdruck aus dem Live‑Orderbuch fließt in die Bewertung ein; abschaltbar, fail‑open
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
- Order‑Book‑Druck‑Panel: Live‑Kauf‑/Verkaufsdruck pro Coin aus dem Bitget‑Orderbuch (öffentlich, ohne Key)
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
