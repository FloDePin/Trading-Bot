"""
Trading Platform v1
Signal | Grid | Funding | DCA
Bitget Sub-Account Support | Demo & Live
"""

import time, json, hmac, hashlib, base64, logging, requests
import urllib.parse, threading, os, math, sqlite3, sys, secrets, uuid, getpass
import signal as _signal
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

# ─────────────────────────────────────────────
#  LOGGING  (max 5 MB, 2 Backups = max 15 MB gesamt)
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        RotatingFileHandler(
            "platform.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("Platform")

# Zeitpunkt des Prozess-Starts (fuer die Laufzeit-Anzeige im Dashboard).
PLATFORM_START = time.time()

# ─────────────────────────────────────────────
#  KONFIGURATION
# ─────────────────────────────────────────────
CONFIG_FILE    = "platform_config.json"
DCA_STATE_FILE = "dca_state.json"
GRID_STATE_FILE = "grid_state.json"
DB_FILE        = "platform.db"
DASHBOARD_PORT = 5000
BASE_URL       = "https://api.bitget.com"
PRODUCT_TYPE   = "USDT-FUTURES"
MARGIN_COIN    = "USDT"

# ─────────────────────────────────────────────
#  SQLITE – PERSISTENTE DATEN
# ─────────────────────────────────────────────
_db_lock = threading.Lock()
_state_lock = threading.Lock()   # schuetzt dca_state.json / grid_state.json vor gleichzeitigen Thread-Zugriffen

def init_db():
    with _db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER, bot TEXT, symbol TEXT, side TEXT,
            entry REAL, exit_price REAL, pnl REAL, fee REAL, size REAL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS pnl_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER, bot TEXT, pnl REAL, balance REAL
        )''')
        conn.commit(); conn.close()

def db_save_trade(bot, symbol, side, entry, exit_price, pnl, fee=0.0, size=0.0):
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            try:
                conn.execute('INSERT INTO trades (ts,bot,symbol,side,entry,exit_price,pnl,fee,size) VALUES (?,?,?,?,?,?,?,?,?)',
                    (int(time.time()*1000), bot, symbol, side, entry, exit_price,
                     round(pnl,4), round(fee,6), size))
                conn.commit()
            finally:
                conn.close()   # Verbindung IMMER schliessen, auch bei Fehler (kein Leak)
    except Exception as e:
        log.debug(f"db_save_trade: {e}")

def db_save_pnl(bot, pnl, balance):
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            try:
                conn.execute('INSERT INTO pnl_snapshots (ts,bot,pnl,balance) VALUES (?,?,?,?)',
                    (int(time.time()*1000), bot, round(pnl,4), round(balance,2)))
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        log.debug(f"db_save_pnl: {e}")

def db_get_pnl_history(bot, days=30):
    try:
        since = int((time.time() - days*86400)*1000)
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            try:
                rows = conn.execute(
                    'SELECT ts,pnl FROM pnl_snapshots WHERE bot=? AND ts>? ORDER BY ts',
                    (bot, since)).fetchall()
            finally:
                conn.close()
        return [{"ts": r[0], "pnl": r[1]} for r in rows]
    except: return []

def db_get_trades(bot=None, limit=200):
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            try:
                if bot and bot != "all":
                    rows = conn.execute(
                        'SELECT ts,bot,symbol,side,entry,exit_price,pnl,fee,size FROM trades WHERE bot=? ORDER BY ts DESC LIMIT ?',
                        (bot, limit)).fetchall()
                else:
                    rows = conn.execute(
                        'SELECT ts,bot,symbol,side,entry,exit_price,pnl,fee,size FROM trades ORDER BY ts DESC LIMIT ?',
                        (limit,)).fetchall()
            finally:
                conn.close()
        cols = ['ts','bot','symbol','side','entry','exit','pnl','fee','size']
        return [dict(zip(cols, r)) for r in rows]
    except: return []

def db_trade_timing():
    """PnL nach Stunde des Tages fuer Trade-Timing-Analyse."""
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            try:
                rows = conn.execute('SELECT ts, pnl FROM trades').fetchall()
            finally:
                conn.close()
        buckets = {h: [] for h in range(24)}
        for ts, pnl in rows:
            hour = datetime.fromtimestamp(ts/1000).hour
            buckets[hour].append(pnl)
        return [{"hour": h,
                 "count": len(v),
                 "avg_pnl": round(sum(v)/len(v),4) if v else 0,
                 "win_rate": round(sum(1 for p in v if p>0)/len(v)*100,1) if v else 0}
                for h, v in buckets.items()]
    except: return []

DEFAULT_CONFIG = {
    "finnhub_key":     "",
    "cryptopanic_key": "",
    "coinalyze_key":   "",
    "live_mode":        False,
    "telegram_token":  "",
    "telegram_chat_id":"",
    "discord_webhook": "",
    "dashboard_user":     "admin",
    "dashboard_password": "",
    "alerts":          [],
    "grid_instances":  [],
    "bots": {
        "signal": {
            "name": "Signal Bot", "enabled": False, "autostart": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "tokens": ["SOLUSDT","ETHUSDT","XRPUSDT","DOGEUSDT"],
            "leverage": 3, "usdt_per_trade": 30, "budget_usdt": 0,
            "risk_pct": 3.0,
            "use_risk_pct": True,
            "stop_loss_pct": 0.010, "take_profit_pct": 0.020,
            "use_atr_sl": True,
            "atr_sl_mult": 1.5, "atr_tp_mult": 2.5,
            "max_concurrent": 2,
            "use_correlation_filter": True, "max_correlation": 0.85,
            "use_adx_filter": True, "min_adx": 20, "use_adx_gate": True,
            "use_orderbook_signal": True,
            "use_sltp_guard": True,
            "use_ema": True, "use_rsi": True, "use_macd": True, "use_bb": True,
            "use_volume": True, "use_funding": True, "use_fg": True, "use_news": True,
            "use_macro": True, "use_trend": False, "trend_len": 50, "use_delta": True,
            "signal_threshold": 3, "check_interval": 30,
            "daily_loss_limit_pct": 0.0,  # Tages-Verlustlimit in % (0 = aus). >0 = pausiert bis zum naechsten UTC-Tag.
            "use_trend_gate": True,       # harter Trend-Filter: ueber EMA nur Long, darunter nur Short
            "use_htf_trend": True,        # Trend-EMA auf dem 1h-Zeitrahmen (statt 1m-Rauschen)
            "trade_cooldown_min": 20,     # Sperre pro Coin nach dem Schliessen (Minuten, 0 = aus) - Anti-Churn
            "use_trailing": True,         # Trailing-Stop: Stop zieht mit dem Gewinn nach (statt festem TP)
            "trail_atr_mult": 2.0,        # Trailing-Abstand = ATR * dieser Faktor
        },
        "grid": {
            "name": "Grid Bot", "enabled": False, "autostart": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "symbol": "BTCUSDT", "upper_price": 0.0, "lower_price": 0.0,
            "grid_count": 10, "investment": 100.0, "check_interval": 10,
            "step_size": 0.0,   # Ziel-Stufengroesse USDT (0 = aus -> upper/lower bzw. Smart-Range)
            "seed_position": True,  # beim Start Grundbestand aufbauen (echtes Grid, tradet in beide Richtungen)
            "smart_range_hours": 24,  # Rueckblick fuer die Smart-Range (Hoch/Tief der letzten N Stunden)
            "leverage": 0,      # 0 = Konto-Hebel unveraendert lassen; >0 = Grid setzt diesen Hebel beim Start
            "stop_loss_pct": 0.0,  # 0 = aus; >0 = schliesst+stoppt, wenn Preis X% unter die Untergrenze faellt
        },
        "dca": {
            "name": "DCA Bot", "enabled": False, "autostart": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "symbol": "BTCUSDT", "interval_hours": 24,
            "amount_per_buy": 20.0, "check_interval": 300,
        },
    }
}

_credentials_just_created = False  # verhindert doppelte Abfrage direkt nach der Ersteinrichtung
_setup_notice_logged      = False  # headless: Setup-Hinweis nur einmal loggen (nicht pro Request)

def _prompt_first_run_credentials():
    """Interaktive Ersteinrichtung: laesst den Nutzer Benutzername/Passwort selbst waehlen.
    Nur moeglich wenn ein echtes Terminal angehaengt ist. Ohne TTY (systemd/Hintergrund-
    Dienste) laeuft die Ersteinrichtung stattdessen ueber den Web-Setup-Assistenten."""
    print("="*55)
    print("  Ersteinrichtung: Dashboard-Zugang festlegen")
    print("="*55)
    try:
        user = input("  Benutzername [admin]: ").strip() or "admin"
        pw1  = getpass.getpass("  Passwort (leer = automatisch generieren): ").strip()
        if not pw1:
            pw1 = secrets.token_urlsafe(12)
            print(f"  Generiertes Passwort: {pw1}")
        else:
            pw2 = getpass.getpass("  Passwort wiederholen: ").strip()
            if pw2 != pw1:
                pw1 = secrets.token_urlsafe(12)
                print(f"  Passwoerter stimmten nicht ueberein - generiere stattdessen eins: {pw1}")
        print("="*55)
        return user, pw1
    except (EOFError, KeyboardInterrupt):
        pw = secrets.token_urlsafe(12)
        print(f"\n  Eingabe abgebrochen - generiertes Passwort: {pw}")
        return "admin", pw

def load_config():
    global _credentials_just_created, _setup_notice_logged
    is_new = not os.path.exists(CONFIG_FILE)
    if is_new:
        data = DEFAULT_CONFIG.copy()
    else:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        # Ensure top-level defaults exist
        for k, v in DEFAULT_CONFIG.items():
            if k != "bots":
                data.setdefault(k, v)
        for k, v in DEFAULT_CONFIG["bots"].items():
            data.setdefault("bots", {}).setdefault(k, {})
            for field, default in v.items():
                data["bots"][k].setdefault(field, default)

    needs_save = is_new
    if not data.get("dashboard_password"):
        if sys.stdin.isatty():
            # Interaktiver Start (manuell im Terminal): Zugang direkt abfragen.
            user, pw = _prompt_first_run_credentials()
            data["dashboard_user"]     = user
            data["dashboard_password"] = pw
            needs_save = True
            _credentials_just_created = True
            log.warning("="*55)
            log.warning(f"  Dashboard-Zugang gesetzt: user='{data.get('dashboard_user','admin')}'")
            log.warning("="*55)
        elif not _setup_notice_logged:
            # Headless (systemd o.ae.): KEIN Auto-Passwort mehr. Das Dashboard bleibt
            # gesperrt und zeigt beim ersten Browser-Aufruf einen Setup-Assistenten,
            # ueber den Benutzername + Passwort selbst vergeben werden.
            # Nur einmal loggen - load_config() wird pro Web-Request aufgerufen.
            _setup_notice_logged = True
            log.warning("="*55)
            log.warning("  Noch kein Dashboard-Zugang eingerichtet.")
            log.warning(f"  Erst-Einrichtung im Browser: http://<PI-IP>:{DASHBOARD_PORT}")
            log.warning("  Dort Benutzername + Passwort festlegen (Setup-Assistent).")
            log.warning("="*55)
    if needs_save:
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
        if is_new:
            log.info(f"Config erstellt: {CONFIG_FILE}")
    return data

def _verify_login_at_startup(cfg):
    """Fragt bei jedem Start (ausser direkt nach der Ersteinrichtung) Benutzername/Passwort
    im Terminal ab, bevor die Plattform hochfaehrt. Nur bei angehaengtem Terminal aktiv -
    Hintergrund-Dienste (systemd etc.) ohne TTY starten weiterhin ohne Abfrage."""
    user = cfg.get("dashboard_user", "admin")
    pw   = cfg.get("dashboard_password", "")
    print("="*55)
    print("  Login")
    print("="*55)
    for attempt in range(3):
        try:
            u = input("  Benutzername: ").strip()
            p = getpass.getpass("  Passwort: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Abgebrochen.")
            sys.exit(1)
        if hmac.compare_digest(u, user) and hmac.compare_digest(p, pw):
            print("  Login OK.")
            print("="*55)
            return
        remaining = 2 - attempt
        if remaining > 0:
            print(f"  Falsch. Noch {remaining} Versuch(e).")
    print("  Zu viele Fehlversuche - Start abgebrochen.")
    sys.exit(1)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def _setup_required():
    """True, solange kein Dashboard-Passwort gesetzt ist (Erst-Einrichtung offen).
    In diesem Zustand ist nur der Setup-Assistent erreichbar, alles andere gesperrt."""
    try:
        return not load_config().get("dashboard_password")
    except Exception:
        return False

def dca_load_state(symbol):
    """Laedt den persistierten DCA-Stand (invested/qty/buys/last_buy) fuer ein Symbol.
    So verliert der DCA-Bot nach Neustart/Absturz seine Statistik nicht - und kauft
    NICHT sofort erneut (last_buy bleibt erhalten)."""
    try:
        with _state_lock:
            if os.path.exists(DCA_STATE_FILE):
                with open(DCA_STATE_FILE) as f:
                    s = json.load(f).get(symbol, {})
                return (float(s.get("total_inv", 0)), float(s.get("total_qty", 0)),
                        int(s.get("buy_count", 0)), float(s.get("last_buy", 0)))
    except Exception as e:
        log.debug(f"dca_load_state: {e}")
    return 0.0, 0.0, 0, 0.0

def dca_save_state(symbol, total_inv, total_qty, buy_count, last_buy):
    try:
        with _state_lock:
            data = {}
            if os.path.exists(DCA_STATE_FILE):
                with open(DCA_STATE_FILE) as f:
                    data = json.load(f)
            data[symbol] = {"total_inv": round(total_inv, 6), "total_qty": round(total_qty, 8),
                            "buy_count": buy_count, "last_buy": last_buy}
            with open(DCA_STATE_FILE, "w") as f:
                json.dump(data, f, indent=2)
    except Exception as e:
        log.debug(f"dca_save_state: {e}")

def grid_load_state(key):
    """Laedt den persistierten Grid-Stand (held/net_qty/current_idx/...) fuer 'key'
    ('grid' oder eine Instanz-ID). So laeuft ein Grid nach Neustart/Stromausfall korrekt
    weiter statt seinen Merker zu verlieren. Gibt dict oder None zurueck."""
    try:
        with _state_lock:
            if os.path.exists(GRID_STATE_FILE):
                with open(GRID_STATE_FILE) as f:
                    return json.load(f).get(key)
    except Exception as e:
        log.debug(f"grid_load_state: {e}")
    return None

def grid_save_state(key, data):
    try:
        with _state_lock:
            d = {}
            if os.path.exists(GRID_STATE_FILE):
                with open(GRID_STATE_FILE) as f:
                    d = json.load(f)
            if data is None:
                d.pop(key, None)
            else:
                d[key] = data
            with open(GRID_STATE_FILE, "w") as f:
                json.dump(d, f, indent=2)
    except Exception as e:
        log.debug(f"grid_save_state: {e}")

# ─────────────────────────────────────────────
#  BITGET API CLIENT
# ─────────────────────────────────────────────
_ACCT_CACHE = {}  # api_key -> 'uta'/'classic' (global, damit neue Clients nicht neu erkennen+loggen)

class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase, live_mode=False):
        self.key   = api_key
        self.sec   = api_secret
        self.pass_ = passphrase
        self.live  = live_mode
        self._acct = None   # 'classic' | 'uta' | None (noch nicht erkannt)

    def _sign(self, ts, method, path, body=""):
        msg = str(ts) + method.upper() + path + (body or "")
        mac = hmac.new(self.sec.encode(), msg.encode(), hashlib.sha256)
        return base64.b64encode(mac.digest()).decode()

    def _hdrs(self, ts, sign):
        h = {
            "ACCESS-KEY": self.key, "ACCESS-SIGN": sign,
            "ACCESS-TIMESTAMP": ts, "ACCESS-PASSPHRASE": self.pass_,
            "Content-Type": "application/json", "locale": "en-US",
        }
        if not self.live:
            h["paptrading"] = "1"
        return h

    def get(self, path, params=None, retries=3):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        full  = path + query
        for attempt in range(retries):
            try:
                ts = str(int(time.time() * 1000))
                r  = requests.get(BASE_URL + full,
                    headers=self._hdrs(ts, self._sign(ts,"GET",full)), timeout=10)
                if r.status_code == 429:
                    log.warning("Rate limit erreicht – warte 5s")
                    time.sleep(5); continue
                return r.json()
            except Exception:
                if attempt < retries - 1: time.sleep(2)
        return {}

    def post(self, path, body: dict, retries=3):
        # clientOid macht Order-Platzierungen idempotent: schlaegt eine Order-Response
        # durch Timeout/Netzwerkfehler fehl obwohl Bitget sie bereits angenommen hat,
        # verhindert der gleichbleibende clientOid beim Retry eine Dopplung der Order.
        if "place-order" in path and "clientOid" not in body:
            body = {**body, "clientOid": uuid.uuid4().hex}
        for attempt in range(retries):
            try:
                b  = json.dumps(body)
                ts = str(int(time.time() * 1000))
                r  = requests.post(BASE_URL + path,
                    headers=self._hdrs(ts, self._sign(ts,"POST",path,b)),
                    data=b, timeout=10)
                return r.json()
            except Exception:
                if attempt < retries - 1: time.sleep(2)
        return {}

    # ── UTA (Unified Trading Account) SUPPORT ─────────────────
    # Bitget stellt Konten zunehmend auf den Unified Trading Account (UTA) um.
    # UTA-Keys koennen die klassischen /api/v2/mix-Endpunkte NICHT aufrufen (und
    # umgekehrt). Auth/Signatur sind bei v2 und v3 identisch - nur die Pfade
    # unterscheiden sich. Wir erkennen den Kontotyp EINMALIG und routen die
    # Konto-/Positions-Abfragen passend. So laeuft der Bot fuer Classic UND UTA.
    UTA_CATEGORY = "USDT-FUTURES"

    def is_uta(self):
        """Erkennt (und cacht pro Client) ob dieser Key ein Unified Trading Account ist.
        Strategie: UTA-Assets-Endpunkt probieren - code 00000 => UTA, sonst Classic."""
        if self._acct is None:
            cached = _ACCT_CACHE.get(self.key)
            if cached:
                self._acct = cached          # schon fuer diesen Key erkannt -> kein erneutes Loggen
            else:
                try:
                    r = self.get("/api/v3/account/assets", {}, retries=1)
                    self._acct = "uta" if str(r.get("code")) == "00000" else "classic"
                except Exception:
                    self._acct = "classic"
                _ACCT_CACHE[self.key] = self._acct
                log.info(f"Bitget-Kontotyp erkannt: {self._acct.upper()} "
                         f"({'DEMO' if not self.live else 'LIVE'})")
        return self._acct == "uta"

    def _uta_assets(self, retries=3):
        """Liste der Unified-Assets: [{coin, available, balance, equity, ...}]."""
        for _ in range(retries):
            r = self.get("/api/v3/account/assets", {})
            d = r.get("data", {})
            arr = d.get("assets", d) if isinstance(d, dict) else d
            if isinstance(arr, list):
                return arr
            time.sleep(1)
        return []

    def _uta_positions(self, symbol=None):
        params = {"category": self.UTA_CATEGORY}
        if symbol:
            params["symbol"] = symbol
        r = self.get("/api/v3/position/current-position", params)
        d = r.get("data", {})
        lst = d.get("list", d) if isinstance(d, dict) else d
        return lst if isinstance(lst, list) else []

    @staticmethod
    def _norm_uta_pos(p):
        """UTA-Positionsfelder auf die KLASSISCHEN Feldnamen mappen, damit der restliche
        Code (holdSide/total/openPriceAvg/unrealizedPL/...) unveraendert weiterlaeuft."""
        return {
            "symbol":           p.get("symbol", ""),
            "holdSide":         p.get("posSide", ""),
            "total":            p.get("total", "0"),
            "openPriceAvg":     p.get("avgPrice", "0"),
            "unrealizedPL":     p.get("unrealisedPnl", "0"),
            "leverage":         p.get("leverage", ""),
            "liquidationPrice": p.get("liquidationPrice", "0"),
            "marginSize":       p.get("positionBalance", "0"),
        }

    def all_positions(self):
        """Alle offenen Positionen (Classic ODER UTA), auf klassische Felder normalisiert."""
        if self.is_uta():
            return [self._norm_uta_pos(p) for p in self._uta_positions()
                    if float(p.get("total", 0) or 0) > 0]
        r = self.get("/api/v2/mix/position/all-position",
                     {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN})
        return [p for p in r.get("data", []) if float(p.get("total", 0) or 0) > 0]

    def balance(self, retries=4):
        if self.is_uta():
            for _ in range(retries):
                for a in self._uta_assets(retries=1):
                    if a.get("coin") == MARGIN_COIN:
                        return float(a.get("available", 0) or 0)
                time.sleep(2)
            log.warning(f"balance(): UTA, kein {MARGIN_COIN}-Guthaben in den Unified-Assets gefunden")
            return 0.0
        last = None
        for _ in range(retries):
            r = self.get("/api/v2/mix/account/accounts",
                {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN})
            last = r
            try:
                for acc in r.get("data", []):
                    if acc.get("marginCoin") == MARGIN_COIN:
                        return float(acc.get("available", 0))
            except: pass
            time.sleep(2)
        # Kein passendes Konto gefunden -> Roh-Antwort loggen (enthaelt KEINE Secrets).
        # Haeufigste Ursache: im DEMO-Modus wurde ein LIVE-API-Key eingetragen. Bitget-Demo
        # braucht einen SEPARATEN Demo-API-Key (sonst 'exchange environment is incorrect'
        # bzw. leere Daten). Sonst: fehlende Read-Rechte / falscher Sub-Account.
        try:
            log.warning(f"balance(): kein {MARGIN_COIN}-Guthaben gefunden "
                        f"({'DEMO' if not self.live else 'LIVE'}) | "
                        f"code={last.get('code') if isinstance(last,dict) else '?'} "
                        f"msg={last.get('msg') if isinstance(last,dict) else '?'} "
                        f"resp={str(last)[:200]}")
        except Exception:
            pass
        return 0.0

    def price(self, symbol):
        r = self.get("/api/v2/mix/market/ticker",
            {"symbol": symbol, "productType": PRODUCT_TYPE})
        try: return float(r["data"][0]["lastPr"])
        except: return 0.0

    def klines(self, symbol, limit=100, granularity="1m"):
        r = self.get("/api/v2/mix/market/candles", {
            "symbol": symbol, "productType": PRODUCT_TYPE,
            "granularity": granularity, "limit": str(limit),
        })
        opens, highs, lows, closes, vols = [], [], [], [], []
        for c in reversed(r.get("data", [])):
            try:
                opens.append(float(c[1])); highs.append(float(c[2]))
                lows.append(float(c[3]));  closes.append(float(c[4]))
                vols.append(float(c[5]))
            except: pass
        return opens, highs, lows, closes, vols

    def funding_rate(self, symbol):
        r = self.get("/api/v2/mix/market/current-fund-rate",
            {"symbol": symbol, "productType": PRODUCT_TYPE})
        try: return float(r["data"][0].get("fundingRate", 0))
        except: return 0.0

    def position(self, symbol):
        if self.is_uta():
            for p in self._uta_positions(symbol):
                if float(p.get("total", 0) or 0) > 0:
                    return self._norm_uta_pos(p)
            return None
        r = self.get("/api/v2/mix/position/single-position", {
            "symbol": symbol, "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN,
        })
        for pos in r.get("data", []):
            if float(pos.get("total", 0)) > 0: return pos
        return None

    def set_leverage(self, symbol, leverage):
        if self.is_uta():
            for side in ("long", "short"):
                self.post("/api/v3/account/set-leverage", {
                    "category": self.UTA_CATEGORY, "symbol": symbol,
                    "leverage": str(leverage), "marginMode": "isolated",
                    "posSide": side,
                })
            return
        for side in ["long","short"]:
            self.post("/api/v2/mix/account/set-leverage", {
                "symbol": symbol, "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN, "leverage": str(leverage),
                "holdSide": side,
            })

    def place_futures_order(self, symbol, side, size, close=False, tp=None, sl=None,
                            margin_mode="isolated"):
        """Futures Market-Order fuer Classic ODER UTA (Auto-Erkennung). close=True schliesst
        die Position (UTA One-Way: reduceOnly). tp/sl werden nur beim Oeffnen an die Order
        gehaengt. Gibt die rohe API-Antwort zurueck (mit 'code'/'msg')."""
        if self.is_uta():
            body = {
                "category": self.UTA_CATEGORY, "symbol": symbol,
                "side": side, "orderType": "market", "qty": str(size),
                "timeInForce": "ioc", "marginMode": margin_mode,
                "reduceOnly": "yes" if close else "no",
            }
            if not close and tp is not None: body["takeProfit"] = fmt_p(symbol, tp)
            if not close and sl is not None: body["stopLoss"]   = fmt_p(symbol, sl)
            return self.post("/api/v3/trade/place-order", body)
        body = {
            "symbol": symbol, "productType": PRODUCT_TYPE,
            "marginMode": margin_mode, "marginCoin": MARGIN_COIN,
            "size": str(size), "side": side,
            "tradeSide": "close" if close else "open",
            "orderType": "market", "force": "ioc",
        }
        if not close and tp is not None: body["presetStopSurplusPrice"] = fmt_p(symbol, tp)
        if not close and sl is not None: body["presetStopLossPrice"]    = fmt_p(symbol, sl)
        return self.post("/api/v2/mix/order/place-order", body)

    def cancel_all(self, symbol):
        """Offene Orders stornieren. Classic: pro Symbol. UTA: kategorieweit (der Bot nutzt
        Market-IOC-Orders, es liegen selten offene Orders - dies ist ein Sicherheitsnetz)."""
        if self.is_uta():
            return self.post("/api/v3/trade/cancel-all-order",
                             {"category": self.UTA_CATEGORY})
        return self.post("/api/v2/mix/order/cancel-all",
                         {"symbol": symbol, "productType": PRODUCT_TYPE,
                          "marginCoin": MARGIN_COIN})

    def fetch_market_precision(self, tick_dec: dict, min_qty: dict):
        """Holt Tick-Size und Min-Qty dynamisch von Bitget und aktualisiert die uebergebenen Dicts."""
        try:
            r = self.get("/api/v2/mix/market/contracts", {"productType": PRODUCT_TYPE})
            if r.get("code") != "00000":
                log.warning("fetch_market_precision: API-Fehler, nutze Fallback-Werte")
                return
            for contract in r.get("data", []):
                sym        = contract.get("symbol","")
                price_place = contract.get("pricePlace")
                min_trade   = contract.get("minTradeNum")
                if price_place is not None:
                    tick_dec[sym] = int(price_place)
                if min_trade is not None:
                    min_qty[sym]  = float(min_trade)
            log.info("Markt-Precision geladen: " +
                ", ".join(f"{s.replace('USDT','')}={d}dp" for s,d in tick_dec.items()))
        except Exception as e:
            log.warning(f"fetch_market_precision Fehler: {e} – nutze Fallback-Werte")

    def validate(self):
        """Testet die API-Verbindung und wertet den ECHTEN Bitget-Code aus, damit Fehler
        (falscher Key, Demo/Live-Mismatch, Classic/Unified-Mismatch, fehlende Rechte)
        sichtbar werden statt 'OK: 0.00'."""
        env = "DEMO" if not self.live else "LIVE"
        try:
            if self.is_uta():
                bal = self.balance(retries=1)
                return True, f"[{env}/UTA] Verbindung OK - Guthaben: {bal:.2f} {MARGIN_COIN}"
            r = self.get("/api/v2/mix/account/accounts",
                         {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN}, retries=1)
            code = str(r.get("code", "")) if isinstance(r, dict) else ""
            if code and code != "00000":
                msg = str(r.get("msg", "?"))
                hint = ""
                if any(k in msg.lower() for k in ("unified", "uta", "environment")):
                    hint = (" | Konto ist ein UNIFIED ACCOUNT - der Bot nutzt die klassische "
                            "Futures-API. Konto auf CLASSIC umstellen + Classic-API-Key nutzen.")
                return False, f"[{env}] API-Fehler {code}: {msg}{hint}"
            found, bal = False, 0.0
            for acc in (r.get("data", []) if isinstance(r, dict) else []):
                if acc.get("marginCoin") == MARGIN_COIN:
                    bal = float(acc.get("available", 0)); found = True
            if not found:
                return False, (f"[{env}] Verbunden, aber KEIN {MARGIN_COIN}-Futures-Guthaben gefunden. "
                               f"Meist: Unified Account (Bot braucht CLASSIC) oder falscher Sub-Account.")
            return True, f"[{env}/CLASSIC] Verbindung OK - Futures-Balance: {bal:.2f} USDT"
        except Exception as e:
            return False, f"[{env}] Verbindungsfehler: {e}"

    # ── SPOT-MARKT METHODEN ───────────────────────────────────
    def spot_price(self, symbol):
        """Aktueller Spot-Preis (kein Auth noetig, aber Client-Methode fuer Konsistenz)."""
        r = self.get("/api/v2/spot/market/tickers", {"symbol": symbol})
        try: return float(r["data"][0]["lastPr"])
        except: return 0.0

    def spot_balance(self, coin):
        """Verfuegbares Guthaben einer Coin. UTA: aus den Unified-Assets (Spot+Futures
        gemeinsam), Classic: separates Spot-Wallet."""
        if self.is_uta():
            for a in self._uta_assets(retries=2):
                if a.get("coin") == coin:
                    return float(a.get("available", 0) or 0)
            return 0.0
        r = self.get("/api/v2/spot/account/assets", {"coin": coin})
        try: return float(r["data"][0].get("available", 0))
        except: return 0.0

    def spot_buy(self, symbol, usdt_amount):
        """
        Spot Market-Kauf fuer einen fixen USDT-Betrag.
        Classic: 'size' = Quote-Currency (USDT). UTA: 'qty' = Basiswaehrung (USDT/Preis),
        Kategorie SPOT. Gibt (ok, qty_bought, error_msg) zurueck.
        """
        if self.is_uta():
            px = self.spot_price(symbol)
            if px <= 0:
                return False, 0.0, "kein Spot-Preis"
            qty = fmt_q(symbol, usdt_amount / px)
            resp = self.post("/api/v3/trade/place-order", {
                "category": "SPOT", "symbol": symbol, "side": "buy",
                "orderType": "market", "qty": str(qty), "timeInForce": "ioc",
            })
            if resp.get("code") == "00000":
                return True, float(qty), ""
            return False, 0.0, resp.get("msg", "Unbekannter Fehler")
        # Auf 2 Nachkommastellen runden reicht fuer USDT-Betrag
        size_str = f"{usdt_amount:.2f}"
        resp = self.post("/api/v2/spot/trade/place-order", {
            "symbol":    symbol,
            "side":      "buy",
            "orderType": "market",
            "force":     "gtc",
            "size":      size_str,
        })
        if resp.get("code") == "00000":
            # Tatsaechlich gekaufte Menge aus der Response lesen (falls vorhanden)
            qty = float(resp.get("data", {}).get("baseVolume", 0) or 0)
            return True, qty, ""
        return False, 0.0, resp.get("msg", "Unbekannter Fehler")

# ─────────────────────────────────────────────
#  TECHNISCHE INDIKATOREN
# ─────────────────────────────────────────────
def ema(closes, period):
    k = 2 / (period + 1); val = closes[0]
    for p in closes[1:]: val = p * k + val * (1 - k)
    return val

def rsi(closes, period=14):
    """Wilder RSI – exponentiell geglaettet, nicht einfacher Durchschnitt."""
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i]-closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    # Initiale Averages
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    # Wilder-Glaettung
    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i]) / period
        al = (al * (period-1) + losses[i]) / period
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag/al))

def atr(highs, lows, closes, period=14):
    """Average True Range – Volatilitaetsmass."""
    if len(closes) < 2: return 0.0
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    if not trs: return 0.0
    val = sum(trs[:period]) / min(period, len(trs))
    for tr in trs[min(period, len(trs)):]:
        val = (val * (period-1) + tr) / period
    return val

def bollinger(closes, period=20, mult=2.0):
    """Bollinger Bands. Gibt (upper, mid, lower) zurueck."""
    if len(closes) < period:
        p = closes[-1]; return p, p, p
    rec = closes[-period:]
    mid = sum(rec) / period
    std = math.sqrt(sum((x-mid)**2 for x in rec) / period)
    return mid + mult*std, mid, mid - mult*std

def macd_calc(closes):
    ml = ema(closes,12) - ema(closes,26)
    vals = [ema(closes[:i+1],12)-ema(closes[:i+1],26) for i in range(26,len(closes))]
    return ml, (ema(vals,9) if len(vals)>=9 else 0.0)

def vol_ratio(volumes, period=20):
    if len(volumes) < period+1: return 1.0
    avg = sum(volumes[-period-1:-1]) / period
    return volumes[-1] / avg if avg > 0 else 1.0

def delta_ratio(highs, lows, closes, volumes, period=20):
    """Order-Flow-Naeherung ohne Tick-Daten (Idee: Close-Location-Delta).
    Pro Kerze: wo im Range schliesst sie? clv = (2*close-high-low)/(high-low) in [-1,1].
    clv*Volumen = geschaetztes Kauf-/Verkaufs-Delta. Summe der letzten `period` Kerzen,
    normiert aufs Gesamtvolumen -> Wert in [-1,1]: >0 = Kaeufer aggressiv, <0 = Verkaeufer."""
    n = min(period, len(closes), len(highs), len(lows), len(volumes))
    if n < 5:
        return 0.0
    net = 0.0; tot = 0.0
    for i in range(-n, 0):
        rng = highs[i] - lows[i]
        clv = ((2*closes[i] - highs[i] - lows[i]) / rng) if rng > 0 else 0.0
        net += clv * volumes[i]; tot += volumes[i]
    return (net / tot) if tot > 0 else 0.0

def adx(highs, lows, closes, period=14):
    """Average Directional Index (Wilder) – Trendstaerke 0..100.
    >25 = klarer Trend, <20 = Seitwaerts/Gezappel. Bei zu wenig Daten -> 0.0 (unbekannt)."""
    n = len(closes)
    if n < period * 2 + 1:
        return 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up = highs[i] - highs[i-1]
        dn = lows[i-1] - lows[i]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    def _wilder(vals):
        if len(vals) < period: return []
        s = sum(vals[:period]); out = [s]
        for v in vals[period:]:
            s = s - s/period + v; out.append(s)
        return out
    atr_s, pdm_s, mdm_s = _wilder(trs), _wilder(plus_dm), _wilder(minus_dm)
    if not atr_s:
        return 0.0
    dxs = []
    for i in range(len(atr_s)):
        a = atr_s[i]
        if a <= 0:
            dxs.append(0.0); continue
        pdi = 100 * pdm_s[i] / a
        mdi = 100 * mdm_s[i] / a
        tot = pdi + mdi
        dxs.append(100 * abs(pdi - mdi) / tot if tot > 0 else 0.0)
    if len(dxs) < period:
        return round(sum(dxs)/len(dxs), 1) if dxs else 0.0
    val = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        val = (val * (period-1) + dx) / period
    return round(val, 1)

# ─────────────────────────────────────────────
#  GETEILTE DATEN (Fear&Greed, News, Makro)
# ─────────────────────────────────────────────
_fg_cache   = {"val": 50, "ts": 0}
_news_cache = {}
_macro_cache = {"events":[], "ts":0, "blackout":False, "score":0, "soft_score":0}

# CoinGecko coin-ID Mapping (kostenlos, kein API-Key noetig)
_COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple",  "DOGE": "dogecoin", "ADA": "cardano",
    "BNB": "binancecoin", "POL": "polygon-ecosystem-token", "DOT": "polkadot",
    "AVAX": "avalanche-2", "LINK": "chainlink", "LTC": "litecoin",
}

US_FED_KW = ["fed","fomc","powell","bowman","waller","jefferson","kugler",
             "cook","barr","mester","kashkari","daly","williams","bostic",
             "barkin","logan","goolsbee"]
HIGH_KW   = ["interest rate","cpi","inflation","nonfarm","nfp","unemployment","gdp"]

def fear_greed():
    if time.time() - _fg_cache["ts"] < 300: return _fg_cache["val"]
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        v = int(r.json()["data"][0]["value"])
        _fg_cache.update({"val": v, "ts": time.time()}); return v
    except: return 50

def news_sentiment(currency):
    """
    Sentiment via CoinGecko Community-Daten (kostenlos, kein API-Key).
    sentiment_votes_up_percentage > 60% = bullish, < 40% = bearish.
    Cache: 10 Minuten (CoinGecko Rate-Limit: 30 Calls/Min im Free-Tier).
    """
    now = time.time()
    if currency in _news_cache and now - _news_cache[currency]["ts"] < 600:
        return _news_cache[currency]["val"]
    try:
        coin_id = _COINGECKO_IDS.get(currency.upper(), currency.lower())
        r = requests.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}"
            f"?localization=false&tickers=false&market_data=false"
            f"&community_data=true&developer_data=false",
            headers={"accept": "application/json"},
            timeout=8
        )
        if r.status_code != 200:
            return "neutral"
        data = r.json()
        up_pct = data.get("sentiment_votes_up_percentage") or 50.0
        res = "bullish" if up_pct > 60 else "bearish" if up_pct < 40 else "neutral"
        _news_cache[currency] = {"val": res, "ts": now}
        return res
    except:
        return "neutral"

def _us_high(name, country, impact):
    if any(k in name for k in US_FED_KW): return True
    if country == "US" and (impact=="high" or any(k in name for k in HIGH_KW)): return True
    return False

def fetch_macro(finnhub_key):
    if time.time() - _macro_cache["ts"] < 1800:
        return (_macro_cache["blackout"], _macro_cache["score"],
                _macro_cache["soft_score"], _macro_cache["events"])
    if not finnhub_key: return False, 0, 0, []
    try:
        now = datetime.now(timezone.utc)
        r   = requests.get(
            f"https://finnhub.io/api/v1/calendar/economic"
            f"?from={now.strftime('%Y-%m-%d')}"
            f"&to={(now+timedelta(hours=48)).strftime('%Y-%m-%d')}"
            f"&token={finnhub_key}", timeout=8)
        if r.status_code != 200: return False, 0, 0, []
        evs = r.json().get("economicCalendar", [])
        soon, blackout, mscore, soft_n = [], False, 0, 0
        for ev in evs:
            name    = (ev.get("event") or "").lower()
            country = (ev.get("country") or "").upper()
            impact  = ev.get("impact","low").lower()
            ev_time = ev.get("time","")
            us_hi   = _us_high(name, country, impact)
            ot_hi   = not us_hi and (impact=="high" or any(k in name for k in HIGH_KW))
            if not (us_hi or ot_hi): continue
            try:
                dt   = datetime.strptime(ev_time[:16], "%Y-%m-%d %H:%M")
                hrs  = (dt - now).total_seconds() / 3600
            except: hrs = 99
            if -2 <= hrs <= 24:
                if us_hi: blackout = True
                else: soft_n += 1
            if hrs <= 48:
                soon.append({"event":   ev.get("event",""),
                             "time":    ev_time[11:16] if len(ev_time) > 11 else ev_time,
                             "date":    ev_time[:10]   if len(ev_time) > 9  else "",
                             "impact":  "high" if us_hi else "medium",
                             "country": country})
            if us_hi:
                act, est = ev.get("actual"), ev.get("estimate")
                if act is not None and est is not None:
                    try:
                        a, e = float(str(act).replace("%","")), float(str(est).replace("%",""))
                        if "cpi" in name or "inflation" in name: mscore += -1 if a>e else 1
                        elif "nonfarm" in name or "employ" in name: mscore += 1 if a>e else -1
                        elif "rate" in name: mscore += 1 if a<e else -1
                    except: pass
        soft = -min(soft_n, 2)
        _macro_cache.update({"events":soon[:8],"ts":time.time(),
                              "blackout":blackout,"score":mscore,"soft_score":soft})
        return blackout, mscore, soft, soon[:8]
    except Exception as e:
        log.warning(f"Makro: {e}"); return False, 0, 0, []

# ─────────────────────────────────────────────
#  MARKT-UEBERSICHT (oeffentlich, kein Auth)
# ─────────────────────────────────────────────
MARKET_SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","DOGEUSDT",
                  "BNBUSDT","ADAUSDT","AVAXUSDT","DOTUSDT","LINKUSDT",
                  "POLUSDT","LTCUSDT","ATOMUSDT","NEARUSDT","AAVEUSDT"]
_market_cache  = {"data": [], "ts": 0}

def fetch_market_overview():
    if time.time() - _market_cache["ts"] < 30:
        return _market_cache["data"]
    try:
        r = requests.get(f"{BASE_URL}/api/v2/mix/market/tickers",
            params={"productType": PRODUCT_TYPE}, timeout=10)
        if r.status_code != 200: return _market_cache["data"]
        tickers = {t["symbol"]: t for t in r.json().get("data", [])}
        result  = []
        for sym in MARKET_SYMBOLS:
            t = tickers.get(sym)
            if not t: continue
            result.append({
                "symbol":   sym.replace("USDT",""),
                "price":    float(t.get("lastPr", 0)),
                "change24": round(float(t.get("change24h", 0)) * 100, 2),
                "vol24":    round(float(t.get("usdtVolume", 0)) / 1e6, 1),
                "high24":   float(t.get("high24h", 0)),
                "low24":    float(t.get("low24h", 0)),
                "funding":  round(float(t.get("fundingRate", 0)) * 100, 4),
            })
        _market_cache.update({"data": result, "ts": time.time()})
        return result
    except Exception as e:
        log.debug(f"Market: {e}"); return _market_cache["data"]

# ─────────────────────────────────────────────
#  KORRELATIONS-MATRIX (Diversifikations-Check)
# ─────────────────────────────────────────────
_corr_cache = {"data": None, "ts": 0, "key": ""}

def _public_daily_closes(symbol, limit):
    """Holt taegliche Schlusskurse ueber die oeffentliche Bitget-API (kein Auth noetig)."""
    try:
        r = requests.get(f"{BASE_URL}/api/v2/mix/market/candles",
            params={"symbol": symbol, "productType": PRODUCT_TYPE,
                    "granularity": "1D", "limit": str(limit)}, timeout=10)
        return [float(c[4]) for c in r.json().get("data", [])]
    except Exception:
        return []

def _pearson(a, b):
    """Pearson-Korrelationskoeffizient zweier gleich langer Listen."""
    n = min(len(a), len(b))
    if n < 3: return 0.0
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n; mb = sum(b) / n
    cov = sum((a[i]-ma) * (b[i]-mb) for i in range(n))
    va  = sum((x-ma)**2 for x in a)
    vb  = sum((x-mb)**2 for x in b)
    denom = (va * vb) ** 0.5
    return round(cov / denom, 3) if denom > 0 else 0.0

def compute_correlation(symbols=None, period_days=30):
    """Korrelationsmatrix der taeglichen Renditen fuer die gewaehlten Symbole.
    Basiert auf oeffentlichen Marktdaten - funktioniert auch ohne API-Keys/im Demo."""
    period_days = max(7, min(180, int(period_days)))
    if not symbols:
        cfg  = load_config()
        base = ["BTCUSDT", "ETHUSDT"]
        toks = cfg.get("bots", {}).get("signal", {}).get("tokens", [])
        seen, symbols = set(), []
        for s in base + list(toks):
            if s and s not in seen:
                seen.add(s); symbols.append(s)
    symbols = [str(s).upper() for s in symbols][:10]

    key = f"{','.join(symbols)}|{period_days}"
    if _corr_cache["data"] and _corr_cache["key"] == key and time.time() - _corr_cache["ts"] < 300:
        return _corr_cache["data"]

    # Schlusskurse -> logarithmische Tagesrenditen
    returns, valid = {}, []
    for sym in symbols:
        closes = _public_daily_closes(sym, period_days + 1)
        if len(closes) >= 4:
            rets = [math.log(closes[i] / closes[i-1])
                    for i in range(1, len(closes))
                    if closes[i-1] > 0 and closes[i] > 0]   # closes[i]>0 verhindert math.log(0)-Crash
            returns[sym] = rets
            valid.append(sym)
        time.sleep(0.05)

    matrix = []
    for a in valid:
        row = [_pearson(returns[a], returns[b]) for b in valid]
        matrix.append(row)

    labels = [s.replace("USDT", "") for s in valid]
    result = {"symbols": labels, "matrix": matrix,
              "period_days": period_days, "count": len(valid)}
    _corr_cache.update({"data": result, "ts": time.time(), "key": key})
    return result

def _correlation_conflict(cand_sym, direction, corr_data, open_positions, max_corr):
    """Gibt das Symbol einer bereits offenen Position zurueck, die zu stark mit
    cand_sym korreliert (geballtes Risiko) - sonst None.
    Fail-open: bei fehlenden/kaputten Daten wird NIE blockiert (return None)."""
    try:
        labels = (corr_data or {}).get("symbols") or []
        matrix = (corr_data or {}).get("matrix") or []
        cand = cand_sym.replace("USDT", "")
        if cand not in labels:
            return None
        ci = labels.index(cand)
        for osym, odir in open_positions:
            o = osym.replace("USDT", "")
            if o == cand or o not in labels:
                continue
            oi = labels.index(o)
            corr = matrix[ci][oi]
            # gleiche Richtung + hohe positive Korrelation  -> effektiv dieselbe Wette
            # gegensaetzliche Richtung + stark negative Korr -> ebenfalls dieselbe Wette
            if odir == direction and corr >= max_corr:
                return o
            if odir != direction and corr <= -max_corr:
                return o
        return None
    except Exception:
        return None

# ─────────────────────────────────────────────
#  MARKT-REGIME (CoinGecko) + DERIVATE (Coinalyze)
# ─────────────────────────────────────────────
_regime_cache = {"data": None, "ts": 0}
_deriv_cache  = {"data": None, "ts": 0}

def fetch_orderbook_pressure(symbol, band=0.01):
    """Kauf-/Verkaufsdruck aus dem oeffentlichen Bitget-Orderbuch.
    ratio = Bid-Notional / Ask-Notional innerhalb +-band (Standard 1%) um den Mittelpreis.
    >1 = mehr Kaufdruck, <1 = mehr Verkaufsdruck. Fail-safe: bei Fehler -> None."""
    c = _ob_cache.get(symbol)
    if c and time.time() - c["ts"] < 15:
        return c["data"]
    try:
        r = requests.get(f"{BASE_URL}/api/v2/mix/market/merge-depth",
                         params={"symbol": symbol, "productType": PRODUCT_TYPE, "limit": "100"},
                         timeout=8)
        d = r.json().get("data", {})
        bids, asks = d.get("bids", []), d.get("asks", [])
        if not bids or not asks:
            return None
        best_bid, best_ask = float(bids[0][0]), float(asks[0][0])
        mid = (best_bid + best_ask) / 2
        if mid <= 0:
            return None
        lo, hi = mid * (1 - band), mid * (1 + band)
        bid_vol = sum(float(p) * float(s) for p, s in ((b[0], b[1]) for b in bids) if float(p) >= lo)
        ask_vol = sum(float(p) * float(s) for p, s in ((a[0], a[1]) for a in asks) if float(p) <= hi)
        ratio = round(bid_vol / ask_vol, 2) if ask_vol > 0 else None
        out = {
            "ratio":      ratio,
            "bid_vol":    round(bid_vol, 0),
            "ask_vol":    round(ask_vol, 0),
            "spread_bps": round((best_ask - best_bid) / mid * 10000, 1),
        }
        _ob_cache[symbol] = {"ts": time.time(), "data": out}
        return out
    except Exception:
        return None

_ob_cache = {}                              # (versehentlich beim Tab-Ausbau geloescht, wieder da)
_trades_cache = {"data": [], "ts": 0}       # Cache fuer /api/trades
_htf_trend_cache = {}                       # sym -> (ema_wert, ts, period) fuer den 1h-Trend-Filter

def fetch_all_trades(limit=100):
    if time.time() - _trades_cache["ts"] < 60:
        return _trades_cache["data"]
    cfg       = load_config()
    live      = cfg.get("live_mode", False)
    all_fills = []
    for bot_id in ("signal","grid","dca"):
        bc = cfg["bots"].get(bot_id, {})
        if not bc.get("api_key") or not bc.get("api_secret"): continue
        try:
            client = BitgetClient(bc["api_key"], bc["api_secret"],
                                  bc["passphrase"], live)
            r = client.get("/api/v2/mix/order/fills-history", {
                "productType": PRODUCT_TYPE, "limit": str(limit)
            })
            for f in r.get("data", {}).get("fillList", []):
                ts = f.get("cTime","")
                dt = ""
                try:
                    dt = datetime.fromtimestamp(int(ts)/1000).strftime("%d.%m %H:%M")
                except: dt = ts[:16] if len(ts) > 15 else ts
                side = f.get("side","").lower()
                trade_side = f.get("tradeSide","").lower()
                all_fills.append({
                    "bot":        bot_id,
                    "time":       int(ts) if ts else 0,
                    "time_str":   dt,
                    "symbol":     f.get("symbol","").replace("USDT",""),
                    "side":       side,
                    "trade_side": trade_side,
                    "price":      float(f.get("price", 0)),
                    "size":       float(f.get("size", 0)),
                    "pnl":        round(float(f.get("profit", 0)), 4),
                    "fee":        round(abs(float(f.get("fee", 0))), 4),
                })
        except Exception as e:
            log.debug(f"Trades {bot_id}: {e}")
    all_fills.sort(key=lambda x: x["time"], reverse=True)
    result = all_fills[:200]
    _trades_cache.update({"data": result, "ts": time.time()})
    return result

# ─────────────────────────────────────────────
#  OFFENE POSITIONEN (alle Sub-Accounts)
# ─────────────────────────────────────────────
def fetch_all_positions():
    cfg      = load_config()
    live     = cfg.get("live_mode", False)
    all_pos  = []
    for bot_id in ("signal","grid","dca"):
        bc = cfg["bots"].get(bot_id, {})
        if not bc.get("api_key") or not bc.get("api_secret"): continue
        try:
            client = BitgetClient(bc["api_key"], bc["api_secret"],
                                  bc["passphrase"], live)
            # all_positions() erkennt Classic/UTA automatisch und normalisiert die Felder
            for pos in client.all_positions():
                all_pos.append({
                    "bot":    bot_id,
                    "symbol": pos.get("symbol","").replace("USDT",""),
                    "side":   pos.get("holdSide",""),
                    "size":   float(pos.get("total", 0)),
                    "entry":  float(pos.get("openPriceAvg", 0)),
                    "upnl":   round(float(pos.get("unrealizedPL", 0)), 4),
                    "liq":    float(pos.get("liquidationPrice", 0)),
                    "lever":  pos.get("leverage",""),
                    "margin": round(float(pos.get("marginSize", 0)), 2),
                })
        except Exception as e:
            log.debug(f"Positions {bot_id}: {e}")
    return all_pos

# ─────────────────────────────────────────────
#  FEAR & GREED HISTORIE
# ─────────────────────────────────────────────
def fetch_fg_history():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=30", timeout=8)
        data = r.json().get("data", [])
        return [{"date": datetime.fromtimestamp(int(d["timestamp"])).strftime("%d.%m"),
                 "value": int(d["value"]),
                 "label": d.get("value_classification","?")}
                for d in reversed(data)]
    except Exception as e:
        log.debug(f"FG history: {e}"); return []

# ─────────────────────────────────────────────
#  BACKTESTING ENGINE
# ─────────────────────────────────────────────
def _sharpe(returns):
    """Annualisierte Sharpe Ratio aus einer Liste von Trade-Returns."""
    if len(returns) < 2: return 0.0
    avg = sum(returns) / len(returns)
    std = math.sqrt(sum((r-avg)**2 for r in returns) / len(returns))
    if std == 0: return 0.0
    return round((avg / std) * math.sqrt(252), 2)

def _run_backtest_on_candles(raw, leverage=3, threshold=2,
                              sl_pct=0.010, tp_pct=0.020,
                              fee_rate=0.0004, pos_frac=0.10):
    """Core Backtest-Logik auf einem Candle-Array. pos_frac = Anteil des Kapitals
    als Margin pro Trade (Standard 10%). Wichtig: bei gleichzeitigem SL+TP in einer
    Kerze wird pessimistisch als SL gewertet (kein geschoenter Win)."""
    closes_all  = [float(c[4]) for c in raw]
    highs_all   = [float(c[2]) for c in raw]
    lows_all    = [float(c[3]) for c in raw]
    volumes_all = [float(c[5]) for c in raw]

    trades, equity, peak, max_dd = [], 1000.0, 1000.0, 0.0
    equity_curve, returns = [], []
    position = None

    for i in range(30, len(closes_all)):
        closes  = closes_all[max(0,i-99): i+1]
        highs   = highs_all[max(0,i-99): i+1]
        lows    = lows_all[max(0,i-99): i+1]
        volumes = volumes_all[max(0,i-99): i+1]
        if len(closes) < 30: continue

        rv         = rsi(closes, 14)
        ef         = ema(closes, 8)
        es         = ema(closes, 20)
        ml,ms      = macd_calc(closes)
        vr         = vol_ratio(volumes)
        atr_val    = atr(highs, lows, closes, 14)
        bb_u,_,bb_l = bollinger(closes, 20)
        price      = closes[-1]

        sc = 0
        sc += 1 if ef > es else -1
        sc += 1 if rv < 38 else (-1 if rv > 62 else 0)
        sc += 1 if ml > ms else -1
        if price < bb_l:  sc += 1
        elif price > bb_u: sc -= 1
        if vr > 1.2: sc += 1 if ef > es else -1
        elif vr < 0.5: sc = int(sc * 0.5)

        sig = "LONG" if sc >= threshold else "SHORT" if sc <= -threshold else "NEUTRAL"

        if position:
            # Fix: Intra-Candle High/Low nutzen, nicht nur Schlusskurs
            # Ein SL oder TP kann innerhalb der Kerze getroffen worden sein
            high_pct = (highs[-1] - position["entry"]) / position["entry"]
            low_pct  = (lows[-1]  - position["entry"]) / position["entry"]
            if position["side"] == "SHORT":
                max_gain = -low_pct   # Short profitiert wenn Kurs faellt
                max_loss = -high_pct  # Short verliert wenn Kurs steigt
            else:
                max_gain = high_pct
                max_loss = low_pct

            sl_d = atr_val * 1.5 if atr_val > 0 else price * sl_pct
            tp_d = atr_val * 2.5 if atr_val > 0 else price * tp_pct
            sl_pct_actual = sl_d / position["entry"]
            tp_pct_actual = tp_d / position["entry"]

            hit_sl = max_loss <= -sl_pct_actual
            hit_tp = max_gain >= tp_pct_actual

            if hit_sl or hit_tp:
                # Pessimistisch: trifft eine Kerze SL UND TP, laesst sich der Pfad nicht
                # bestimmen -> als Stop-Loss werten (verhindert geschoente Win-Rate/Sharpe).
                took_tp = hit_tp and not hit_sl
                gross   = equity * pos_frac * leverage * (tp_pct_actual if took_tp else -sl_pct_actual)
                fees    = equity * pos_frac * leverage * fee_rate * 2  # entry + exit
                net_pnl = gross - fees
                equity += net_pnl
                equity_curve.append(round(equity, 2))
                returns.append(net_pnl / (equity - net_pnl) if (equity - net_pnl) > 0 else 0)
                trades.append({
                    "entry":  round(position["entry"], 4),
                    "exit":   round(price, 4),
                    "side":   position["side"],
                    "pnl":    round(net_pnl, 2),
                    "fee":    round(fees, 4),
                    "result": "WIN" if took_tp else "LOSS",
                })
                peak   = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                position = None

        if not position and sig != "NEUTRAL":
            position = {"side": sig, "entry": price}

    wins   = sum(1 for t in trades if t["result"]=="WIN")
    losses = len(trades) - wins
    return {
        "trades":       len(trades),
        "wins":         wins,
        "losses":       losses,
        "win_rate":     round(wins / len(trades) * 100, 1) if trades else 0,
        "total_pnl":    round(sum(t["pnl"] for t in trades), 2),
        "total_fees":   round(sum(t["fee"] for t in trades), 4),
        "final_equity": round(equity, 2),
        "max_drawdown": round(max_dd, 1),
        "sharpe":       _sharpe(returns),
        "equity_curve": equity_curve[-80:],
        "trade_list":   trades[-30:],
    }

def run_backtest(symbol="BTCUSDT", period_days=14, leverage=3,
                 threshold=2, sl_pct=0.010, tp_pct=0.020,
                 walk_forward=False, pos_frac=0.10):
    try:
        needed  = period_days * 24
        raw_all = []
        end_time = None

        while len(raw_all) < needed:
            remaining = needed - len(raw_all)
            params    = {"symbol":symbol,"productType":PRODUCT_TYPE,
                         "granularity":"1H","limit":str(min(remaining,1000))}
            if end_time: params["endTime"] = str(end_time)
            r     = requests.get(f"{BASE_URL}/api/v2/mix/market/candles",
                                 params=params, timeout=15)
            batch = r.json().get("data",[])
            if not batch: break
            raw_all  = batch + raw_all
            end_time = int(batch[-1][0]) - 1
            if len(batch) < 1000: break

        raw = list(reversed(raw_all))
        if len(raw) < 50:
            return {"error":"Nicht genug historische Daten."}

        if walk_forward and len(raw) >= 100:
            split      = int(len(raw) * 0.7)
            test_raw   = raw[split:]
            result     = _run_backtest_on_candles(test_raw, leverage, threshold, sl_pct, tp_pct, pos_frac=pos_frac)
            result["walk_forward"] = True
            result["train_pct"]    = 70
            result["test_pct"]     = 30
            result["test_candles"] = len(test_raw)
        else:
            result = _run_backtest_on_candles(raw, leverage, threshold, sl_pct, tp_pct, pos_frac=pos_frac)
            result["walk_forward"] = False

        result["symbol"]      = symbol
        result["period_days"] = period_days
        result["candles"]     = len(raw)
        return result
    except Exception as e:
        return {"error": str(e)}

def run_multi_backtest(symbols, period_days=14, leverage=3,
                       threshold=2, sl_pct=0.010, tp_pct=0.020, pos_frac=0.10):
    """Backtest auf mehreren Symbolen gleichzeitig."""
    results = {}
    for sym in symbols:
        results[sym] = run_backtest(sym, period_days, leverage, threshold, sl_pct, tp_pct, pos_frac=pos_frac)
    return results

# ─────────────────────────────────────────────
#  VOLATILITAETS-CIRCUIT-BREAKER
# ─────────────────────────────────────────────
_circuit_open   = False
_circuit_until  = 0
_btc_prices_cb  = []

def volatility_circuit_breaker():
    """BTC 1h-Bewegung > 5% --> alle Bots kurz pausieren."""
    global _circuit_open, _circuit_until
    while True:
        try:
            r = requests.get(f"{BASE_URL}/api/v2/mix/market/ticker",
                params={"symbol":"BTCUSDT","productType":PRODUCT_TYPE}, timeout=5)
            px = float(r.json()["data"][0]["lastPr"])
            _btc_prices_cb.append(px)
            if len(_btc_prices_cb) > 60: _btc_prices_cb.pop(0)

            now = time.time()
            if now < _circuit_until:
                _circuit_open = True
            elif len(_btc_prices_cb) >= 12:
                oldest = _btc_prices_cb[-12]  # ~60 min ago
                move   = abs(px - oldest) / oldest * 100
                if move >= 5.0 and not _circuit_open:
                    _circuit_open  = True
                    _circuit_until = now + 1800  # 30 min Pause
                    msg = f"CIRCUIT BREAKER: BTC {move:.1f}% in 1h. Alle Bots pausiert fuer 30 Min."
                    log.warning(msg); notify("[!] " + msg, True)
                    with plock:
                        for b in pstate["bots"].values():
                            if b.get("status") == "RUNNING":
                                b["circuit_paused"] = True
                elif move < 3.0 and _circuit_open and now >= _circuit_until:
                    _circuit_open = False
                    with plock:
                        for b in pstate["bots"].values():
                            b.pop("circuit_paused", None)
                    log.info("Circuit Breaker zurueckgesetzt – Bots fortgesetzt.")
        except Exception as e:
            log.debug(f"Circuit Breaker: {e}")
        time.sleep(300)  # alle 5 min pruefen

def is_circuit_open():
    return _circuit_open

# ─────────────────────────────────────────────
#  GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────
def _graceful_shutdown(signum, frame):
    log.info("Graceful Shutdown eingeleitet...")
    for bid in list(bot_flags.keys()):
        bot_flags[bid]["stop"] = True
    for iid in list(grid_inst_flags.keys()):
        grid_inst_flags[iid]["stop"] = True
    time.sleep(3)
    log.info("Platform gestoppt. Auf Wiedersehen.")
    sys.exit(0)

_signal.signal(_signal.SIGTERM, _graceful_shutdown)
_signal.signal(_signal.SIGINT,  _graceful_shutdown)

# ─────────────────────────────────────────────
#  ALERT SYSTEM
# ─────────────────────────────────────────────
_alert_log  = []
_alert_lock = threading.Lock()

def _alert_note(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    with _alert_lock:
        _alert_log.insert(0, {"t": ts, "m": msg})
        if len(_alert_log) > 50: _alert_log.pop()
    notify("[!] ALERT: " + msg, True)
    log.info(f"[ALERT] {msg}")

def alert_check_thread():
    while True:
        try:
            cfg    = load_config()
            alerts = cfg.get("alerts", [])
            dirty  = False
            for a in alerts:
                if not a.get("enabled"): continue
                atype = a.get("type","")
                try:
                    if atype in ("price_above","price_below"):
                        sym   = a.get("symbol","BTC").upper() + "USDT"
                        val   = float(a.get("value", 0))
                        r2    = requests.get(f"{BASE_URL}/api/v2/mix/market/ticker",
                                    params={"symbol":sym,"productType":PRODUCT_TYPE},timeout=5)
                        price = float(r2.json()["data"][0]["lastPr"])
                        cond  = price > val if atype=="price_above" else price < val
                        if cond and not a.get("triggered"):
                            _alert_note(f"{sym.replace('USDT','')} {'ueber' if atype=='price_above' else 'unter'} {val} (aktuell {price:.2f})")
                            a["triggered"] = True; dirty = True
                        elif not cond and a.get("triggered"):
                            a["triggered"] = False; dirty = True

                    elif atype == "pnl_below":
                        val = float(a.get("value", -50))
                        with plock:
                            total = sum(pstate["bots"][b].get("pnl",0)
                                        for b in pstate["bots"])
                            # Multi-Grid-Instanzen mit einrechnen (eigene Threads/Sub-Accounts)
                            total += sum(g.get("pnl",0) for g in pstate.get("grid_instances", {}).values())
                        if total < val and not a.get("triggered"):
                            _alert_note(f"Gesamt-PnL unter {val} USDT (aktuell {total:.2f})")
                            a["triggered"] = True; dirty = True
                        elif total >= val and a.get("triggered"):
                            a["triggered"] = False; dirty = True

                    elif atype == "funding_above":
                        sym = a.get("symbol","ETH").upper() + "USDT"
                        val = float(a.get("value", 0.05))
                        r2  = requests.get(f"{BASE_URL}/api/v2/mix/market/current-fund-rate",
                                    params={"symbol":sym,"productType":PRODUCT_TYPE},timeout=5)
                        fr  = float(r2.json()["data"][0].get("fundingRate",0)) * 100
                        if abs(fr) >= val and not a.get("triggered"):
                            _alert_note(f"{sym.replace('USDT','')} Funding Rate {fr:.4f}% (Schwelle {val}%)")
                            a["triggered"] = True; dirty = True
                        elif abs(fr) < val and a.get("triggered"):
                            a["triggered"] = False; dirty = True
                except Exception as e:
                    log.debug(f"Alert {a.get('id')}: {e}")

            if dirty:
                save_config(cfg)
        except Exception as e:
            log.debug(f"Alert thread: {e}")
        time.sleep(60)

# ─────────────────────────────────────────────
#  PLATTFORM STATE
# ─────────────────────────────────────────────
# Fallback-Werte – werden beim Bot-Start dynamisch von Bitget ueberschrieben
TICK_DEC = {"SOLUSDT":3,"ETHUSDT":2,"XRPUSDT":4,"DOGEUSDT":5,"BTCUSDT":1}
MIN_QTY  = {"SOLUSDT":0.1,"ETHUSDT":0.01,"XRPUSDT":1.0,"DOGEUSDT":1.0,"BTCUSDT":0.001}

def fmt_p(sym, p): return f"{p:.{TICK_DEC.get(sym,3)}f}"

def _qty_decimals(mq):
    """Nachkommastellen aus der Mindestmenge ableiten (z.B. 0.001 -> 3, 0.01 -> 2, 1 -> 0)."""
    s = ("%.10f" % mq).rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0

def fmt_q(sym, q):
    mq = MIN_QTY.get(sym, 0.1)
    if q < mq: q = mq
    # Nachkommastellen DYNAMISCH aus der Mindestmenge, nicht hart 1 Stelle:
    # sonst wird z.B. 0.03 ETH oder 0.0015 BTC zu "0.0" -> Order von Bitget abgelehnt.
    dec = _qty_decimals(mq)
    return str(int(q)) if dec <= 0 else f"{q:.{dec}f}"

def _size_check(qty_str, px, want_notional, tol=1.5):
    """Order-Sanity-Check VOR dem Senden (Lehre vom MT5-Bot): faengt 0-Mengen
    (Rundungs-/Formatierungsfehler) und stark ueberdimensionierte Orders ab.
    Gibt (ok, grund) zurueck."""
    try:
        q = float(qty_str)
    except Exception:
        return False, "Menge nicht numerisch"
    if q <= 0:
        return False, "Menge 0 (Rundungs-/Formatierungsfehler)"
    if want_notional > 0 and px > 0 and q * px > want_notional * tol:
        return False, f"Notional {q*px:.2f} USDT > {tol}x Ziel {want_notional:.2f}"
    return True, ""

def _ensure_sltp(client, sym, direction, sl, tp, size):
    """SL/TP-WAECHTER (Lehre vom MT5-Bot): prueft NACH dem Open, ob die Position
    Stop-Loss UND Take-Profit hat. Fehlt etwas -> per TPSL-Order nachruesten
    (idempotent: dasselbe SL/TP erneut zu setzen schadet nicht). Vollstaendig
    fail-safe: bei Fehlern nur Log, nie Absturz, KEIN Auto-Close (vermeidet
    Fehl-Schliessungen bei API-Eigenheiten)."""
    cur = sym.replace("USDT", "")
    if client.is_uta():
        # UTA: SL/TP haengen bereits an der Open-Order (takeProfit/stopLoss) - ein
        # separater TPSL-Nachtrag entfaellt hier.
        return
    try:
        time.sleep(1)  # Bitget kurz Zeit geben, die Position zu registrieren
        pos = client.position(sym)
        if not pos:
            return  # keine offene Position (z.B. IOC nicht gefuellt) -> nichts zu schuetzen
        def _has(*keys):
            return any(float(pos.get(k, 0) or 0) > 0 for k in keys)
        has_sl = _has("presetStopLossPrice", "stopLoss", "slTriggerPrice")
        has_tp = _has("presetStopSurplusPrice", "takeProfit", "tpTriggerPrice")
        if has_sl and has_tp:
            blog("signal", f"{cur}: SL/TP beim Broker bestaetigt", "INFO")
            return
        hold = "long" if direction == "LONG" else "short"
        for plan, trig, missing in (("pos_loss", sl, not has_sl), ("pos_profit", tp, not has_tp)):
            if not missing or trig is None:   # trig None = Trailing aktiv (kein festes TP) -> nicht nachruesten
                continue
            r = client.post("/api/v2/mix/order/place-tpsl-order", {
                "symbol": sym, "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN,
                "planType": plan, "triggerPrice": fmt_p(sym, trig),
                "holdSide": hold, "size": str(size),
            })
            okp = r.get("code") == "00000"
            blog("signal", f"{cur}: {plan} nachgeruestet @ {trig:.2f} {'OK' if okp else r.get('msg','FEHLER')}",
                 "WARN" if okp else "ERROR")
        if not has_sl:
            notify(f"[!] {cur}: Position ohne Stop-Loss erkannt – Waechter hat nachgeruestet. Bitte pruefen.", True)
    except Exception as e:
        blog("signal", f"{cur}: SL/TP-Waechter Fehler (Position bleibt bestehen): {e}", "WARN")

pstate = {
    "bots": {
        "signal":  {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,"pnl_pct":0.0,
                    "trade_count":0,"wins":0,"logs":[],"tokens":{},"blackout":False,
                    "macro_events":[],"last_update":"","started_at":0},
        "grid":    {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,
                    "trade_count":0,"filled":0,"logs":[],"grid_orders":[],
                    "symbol":"","upper":0,"lower":0,"last_update":"","started_at":0},
        "dca":     {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,
                    "invested":0.0,"buys":0,"avg_price":0.0,"next_buy":"","logs":[],"last_update":"","started_at":0},
    },
    "grid_instances": {},
    "live_mode": False,
}
plock            = threading.Lock()
_start_lock      = threading.Lock()  # verhindert Race-Condition bei doppeltem Bot-Start
bot_threads      = {}
bot_flags        = {}
grid_inst_threads = {}
grid_inst_flags   = {}

# ─────────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────────
_tg = {"token": "", "chat": ""}

def tg_init(token, chat_id):
    _tg["token"] = str(token).strip()
    _tg["chat"]  = str(chat_id).strip()

def send_telegram(msg):
    if not _tg["token"] or not _tg["chat"]:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{_tg['token']}/sendMessage",
            json={"chat_id": _tg["chat"], "text": msg, "parse_mode": "HTML"},
            timeout=5
        )
    except Exception as e:
        log.debug(f"Telegram: {e}")

def send_discord(msg, color=0x00d68f):
    """Sendet eine Nachricht via Discord Webhook (Embed-Format)."""
    cfg = load_config()
    wh  = cfg.get("discord_webhook","")
    if not wh: return False
    try:
        payload = {"embeds": [{"description": msg[:4000], "color": color}]}
        r = requests.post(wh, json=payload, timeout=8)
        return r.status_code in (200, 204)
    except Exception as e:
        log.debug(f"Discord: {e}"); return False

def notify(msg, is_alert=False):
    """Sendet Nachricht an alle konfigurierten Kanaele (Telegram + Discord)."""
    color = 0xf87171 if is_alert else 0x00d68f
    send_telegram(msg)
    send_discord(msg, color)

def blog(bot_id, msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    with plock:
        pstate["bots"][bot_id]["logs"].insert(0, {"t":ts,"l":level,"m":msg})
        if len(pstate["bots"][bot_id]["logs"]) > 60:
            pstate["bots"][bot_id]["logs"].pop()
    getattr(log, level.lower() if level in ("INFO","ERROR") else "warning")(f"[{bot_id}] {msg}")
    if level == "TRADE":
        notify(f"[OK] {bot_id.upper()}: {msg}")
    elif level == "ERROR":
        notify(f"[FEHLER] {bot_id.upper()}: {msg}", True)
    elif level == "MACRO" and "BLACKOUT" in msg.upper():
        notify(f"[MAKRO BLACKOUT] {msg}", True)

# ─────────────────────────────────────────────
#  SIGNAL BOT
# ─────────────────────────────────────────────
def run_signal(flag):
    cfg      = load_config()
    bc       = cfg["bots"]["signal"]
    client   = BitgetClient(bc["api_key"], bc["api_secret"], bc["passphrase"],
                            cfg.get("live_mode", False))
    tokens       = bc.get("tokens", ["SOLUSDT","ETHUSDT","XRPUSDT","DOGEUSDT"])
    lever        = bc.get("leverage", 3)
    usdt_pt      = bc.get("usdt_per_trade", 30)
    budget_usdt  = float(bc.get("budget_usdt", 0))   # 0 = kein Limit (volle Balance)
    risk_pct     = bc.get("risk_pct", 3.0)
    use_risk_pct = bc.get("use_risk_pct", True)
    sl_pct       = bc.get("stop_loss_pct", 0.010)
    tp_pct       = bc.get("take_profit_pct", 0.020)
    use_atr_sl   = bc.get("use_atr_sl", True)
    atr_sl_mult  = bc.get("atr_sl_mult", 1.5)
    atr_tp_mult  = bc.get("atr_tp_mult", 2.5)
    max_conc     = bc.get("max_concurrent", 2)
    use_corr_filter = bc.get("use_correlation_filter", True)
    max_corr     = float(bc.get("max_correlation", 0.85))
    use_adx      = bc.get("use_adx_filter", True)
    min_adx      = float(bc.get("min_adx", 20))
    use_ob       = bc.get("use_orderbook_signal", True)
    use_sltp_guard = bc.get("use_sltp_guard", True)
    # Einzelne Score-Faktoren an/aus (Standard: alle bestehenden an, Trendfilter aus)
    use_ema     = bc.get("use_ema", True)
    use_rsi     = bc.get("use_rsi", True)
    use_macd    = bc.get("use_macd", True)
    use_bb      = bc.get("use_bb", True)
    use_volume  = bc.get("use_volume", True)
    use_funding = bc.get("use_funding", True)
    use_fg      = bc.get("use_fg", True)
    use_news    = bc.get("use_news", True)
    use_macro   = bc.get("use_macro", True)
    use_trend   = bc.get("use_trend", False)
    trend_len   = max(20, int(bc.get("trend_len", 50)))
    thresh       = bc.get("signal_threshold", 3)
    check        = bc.get("check_interval", 30)
    fkey         = cfg.get("finnhub_key","")
    fee_rate     = 0.0004  # Bitget Taker Fee (0.04%)
    win_streak   = 0
    loss_streak  = 0
    realized_pnl = 0.0   # PnL aus abgeschlossenen Trades DIESES Laufs (kontounabhaengig)
    last_unreal  = 0.0   # unrealisierter PnL der offenen Signal-Positionen (vom letzten Zyklus)
    _day         = datetime.now(timezone.utc).strftime("%Y-%m-%d")  # aktueller UTC-Handelstag
    _day_anchor  = 0.0   # PnL zu Tagesbeginn (fuer das echte Tages-Verlustlimit)
    cooldown_until = {}  # sym -> Zeitpunkt, ab dem wieder gehandelt werden darf (Anti-Churn)
    trail          = {}  # sym -> {side, peak, stop} fuer den nachziehenden Trailing-Stop
    close_warn_at  = {}  # sym -> Zeit der letzten "Schliessen fehlgeschlagen"-Warnung (Throttle)
    degen_at       = {}  # sym -> Zeit der letzten "Kursdaten unbrauchbar"-Warnung (Throttle)

    with plock:
        for t in tokens:
            pstate["bots"]["signal"]["tokens"][t] = {
                "signal":"NEUTRAL","score":0,"rsi":0,"ema_fast":0,"ema_slow":0,
                "macd":0,"macd_signal":0,"volume_ratio":1,"funding_rate":0,
                "bb_upper":0,"bb_lower":0,"atr":0,"adx":0,"ob_ratio":None,
                "fear_greed":50,"sentiment":"neutral","position":None,"score_parts":{},
            }
        pstate["bots"]["signal"]["win_streak"]  = 0
        pstate["bots"]["signal"]["loss_streak"] = 0

    client.fetch_market_precision(TICK_DEC, MIN_QTY)

    for sym in tokens:
        client.set_leverage(sym, lever)
        blog("signal", f"Hebel {lever}x: {sym.replace('USDT','')}")
        time.sleep(0.3)

    start_bal = client.balance(retries=5)
    with plock:
        pstate["bots"]["signal"].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,"started_at":time.time()})
    blog("signal", f"Start: {start_bal:.2f} USDT | ATR-SL: {'ja' if use_atr_sl else 'nein'} | Risk: {risk_pct if use_risk_pct else usdt_pt} {'%' if use_risk_pct else 'USDT'}")

    while not flag["stop"]:
        try:
            # Live-Reconfig: weiche Parameter jeden Zyklus neu einlesen, damit Aenderungen
            # in den Settings OHNE Bot-Neustart greifen. (Tokens, Hebel und API-Keys bleiben
            # bewusst wie beim Start - die brauchen einen Neustart.)
            _cfg = load_config(); bc = _cfg["bots"]["signal"]
            thresh          = bc.get("signal_threshold", 3)
            check           = bc.get("check_interval", 30)
            risk_pct        = bc.get("risk_pct", 3.0)
            use_risk_pct    = bc.get("use_risk_pct", True)
            usdt_pt         = bc.get("usdt_per_trade", 30)
            budget_usdt     = float(bc.get("budget_usdt", 0))
            sl_pct          = bc.get("stop_loss_pct", 0.010)
            tp_pct          = bc.get("take_profit_pct", 0.020)
            use_atr_sl      = bc.get("use_atr_sl", True)
            atr_sl_mult     = bc.get("atr_sl_mult", 1.5)
            atr_tp_mult     = bc.get("atr_tp_mult", 2.5)
            max_conc        = bc.get("max_concurrent", 2)
            use_corr_filter = bc.get("use_correlation_filter", True)
            max_corr        = float(bc.get("max_correlation", 0.85))
            use_adx         = bc.get("use_adx_filter", True)
            min_adx         = float(bc.get("min_adx", 20))
            use_adx_gate    = bc.get("use_adx_gate", True)   # hart sperren unter min_adx
            use_ob          = bc.get("use_orderbook_signal", True)
            use_sltp_guard  = bc.get("use_sltp_guard", True)
            use_ema=bc.get("use_ema",True);       use_rsi=bc.get("use_rsi",True)
            use_macd=bc.get("use_macd",True);     use_bb=bc.get("use_bb",True)
            use_volume=bc.get("use_volume",True); use_funding=bc.get("use_funding",True)
            use_fg=bc.get("use_fg",True);         use_news=bc.get("use_news",True)
            use_macro=bc.get("use_macro",True);   use_trend=bc.get("use_trend",False)
            use_delta=bc.get("use_delta",True)    # Order-Flow/Delta-Faktor
            trend_len=max(20,int(bc.get("trend_len",50)))
            daily_limit = max(0.0, float(bc.get("daily_loss_limit_pct", 0))) / 100.0  # 0 = aus
            use_trend_gate = bc.get("use_trend_gate", True)        # harter Trend-Filter
            use_htf_trend  = bc.get("use_htf_trend", True)         # Trend-EMA auf 1h-Zeitrahmen
            cooldown_min   = max(0, int(bc.get("trade_cooldown_min", 20)))  # Anti-Churn (Minuten)
            use_trailing   = bc.get("use_trailing", True)          # Trailing-Stop
            trail_mult     = max(0.3, float(bc.get("trail_atr_mult", 2.0)))  # Trailing-Abstand in ATR
            fkey            = _cfg.get("finnhub_key","")

            bal = client.balance(retries=3) or start_bal
            # PnL aus EIGENEN Trades (realisiert + unrealisiert), NICHT aus der Kontostand-
            # Differenz - sonst leckt bei geteiltem Konto der PnL anderer Bots hier rein
            # (Signal zeigte sonst "0 Trades, aber +X"). realized/last_unreal werden unten gepflegt.
            pnl = realized_pnl + last_unreal
            pct = pnl / start_bal if start_bal > 0 else 0
            db_save_pnl("signal", pnl, bal)
            with plock:
                pstate["bots"]["signal"].update({
                    "balance":round(bal,2),"pnl":round(pnl,2),
                    "pnl_pct":round(pct*100,2),
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
            # Tages-Verlustlimit (opt-in, Standard aus). Echter Tages-Reset um 00:00 UTC:
            # neuer Tag -> Anker = aktueller PnL. Reisst der Tagesverlust das Limit, pausiert
            # der Bot NUR bis zum naechsten UTC-Tag (kein stuendliches Endlos-Pausen mehr).
            _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if _today != _day:
                _day = _today; _day_anchor = pnl
                blog("signal", f"Neuer Handelstag ({_day}) - Tageslimit zurueckgesetzt")
            daily_pnl = pnl - _day_anchor
            if daily_limit > 0 and start_bal > 0 and (daily_pnl / start_bal) <= -daily_limit:
                blog("signal", f"Tages-Verlustlimit {daily_limit*100:.1f}% erreicht "
                               f"({daily_pnl:.2f} USDT heute). Pause bis morgen (UTC).", "WARN")
                with plock: pstate["bots"]["signal"]["status"] = "PAUSED"
                while datetime.now(timezone.utc).strftime("%Y-%m-%d") == _day and not flag["stop"]:
                    time.sleep(30)
                with plock: pstate["bots"]["signal"]["status"] = "RUNNING"
                continue

            blackout, mscore, ssoft, mevents = fetch_macro(fkey)
            with plock:
                pstate["bots"]["signal"]["blackout"]     = blackout
                pstate["bots"]["signal"]["macro_events"] = mevents
            if blackout:
                blog("signal","BLACKOUT aktiv (US High-Impact)","MACRO")
            if is_circuit_open():
                blog("signal","CIRCUIT BREAKER aktiv – Pause","WARN")
                time.sleep(check); continue
            elif ssoft < 0:
                blog("signal",f"Soft-Penalty: {ssoft:+d} (Non-US)","MACRO")

            # Aktuell offene Positionen (Symbol + Richtung) fuer Limit- und Korrelations-Check.
            # Lesezugriff auf die geteilte pstate-Struktur unter plock (Thread-Safety).
            open_positions = []
            with plock:
                for s in tokens:
                    p = pstate["bots"]["signal"]["tokens"].get(s,{}).get("position")
                    if p:
                        open_positions.append((s, "LONG" if p.get("holdSide")=="long" else "SHORT"))
            open_pos_count = len(open_positions)

            # Korrelations-Daten einmal pro Zyklus (5-Min-Cache). Fail-open: bei Fehler None.
            corr_data = None
            if use_corr_filter:
                try:
                    corr_data = compute_correlation()
                except Exception as e:
                    corr_data = None
                    blog("signal", f"Korrelations-Check nicht verfuegbar (handle normal weiter): {e}", "WARN")

            cycle_unreal = 0.0   # unrealisierter PnL aller offenen Signal-Positionen dieses Zyklus
            for sym in tokens:
                try:
                    _, highs, lows, closes, vols = client.klines(sym, 100)
                    if len(closes) < 30: continue
                    rv       = rsi(closes, 14)
                    ef       = ema(closes, 8)
                    es       = ema(closes, 20)
                    ml,ms    = macd_calc(closes)
                    vr       = vol_ratio(vols)
                    dratio   = delta_ratio(highs, lows, closes, vols) if use_delta else 0.0
                    atr_val  = atr(highs, lows, closes, 14)
                    bb_u, bb_m, bb_l = bollinger(closes, 20, 2.0)
                    fr       = client.funding_rate(sym)
                    fg       = fear_greed()
                    cur      = sym.replace("USDT","")
                    sent     = news_sentiment(cur)

                    price_now = closes[-1]
                    # Degenerate-Daten-Schutz (Demo-Glitch, s. XRP RSI=100/Preis eingefroren):
                    # eingefrorene Kerzen ODER RSI am Anschlag -> Coin diesen Zyklus ueberspringen,
                    # damit NICHT auf kaputten Daten gehandelt wird. Gedrosselt warnen (10 min).
                    if (max(closes[-10:]) == min(closes[-10:])) or rv >= 99.9 or rv <= 0.1:
                        if time.time() - degen_at.get(sym, 0) > 1800:
                            degen_at[sym] = time.time()
                            blog("signal", f"{cur}: Kursdaten unbrauchbar (RSI={rv:.0f}, Preis {price_now}) - uebersprungen", "WARN")
                        continue
                    # Trend-EMA: 1h-Zeitrahmen (robust, spiegelt den echten Trend) wenn use_htf_trend,
                    # sonst wie frueher auf den 1m-Kerzen. trend_len ist dann die Periode in Stunden.
                    if use_trend or use_trend_gate:
                        ema_long = htf_trend_ema(client, sym, trend_len) if use_htf_trend else ema(closes, trend_len)
                    else:
                        ema_long = 0.0
                    adx_val   = adx(highs, lows, closes, 14)
                    ob_ratio  = None
                    if use_ob:
                        ob = fetch_orderbook_pressure(sym)
                        if ob and ob.get("ratio"):
                            ob_ratio = ob["ratio"]

                    # Jeder Faktor einzeln per Settings abschaltbar. Beitrag 0 = aus/neutral.
                    # So sieht man im Dashboard genau, WELCHER Faktor wie viel beitraegt.
                    parts = {
                        "ema":      (1 if ef > es else -1) if use_ema else 0,
                        "rsi":      (1 if rv < 38 else -1 if rv > 62 else 0) if use_rsi else 0,
                        "macd":     (1 if ml > ms else -1) if use_macd else 0,
                        "bb":       (1 if price_now < bb_l else -1 if price_now > bb_u else 0) if use_bb else 0,
                        "volume":   ((1 if ef > es else -1) if vr > 1.2 else 0) if use_volume else 0,
                        "funding":  (-1 if fr > 0.0003 else 1 if fr < -0.0003 else 0) if use_funding else 0,
                        "fear_greed": (1 if fg < 30 else -1 if fg > 70 else 0) if use_fg else 0,
                        "news":     (1 if sent == "bullish" else -1 if sent == "bearish" else 0) if use_news else 0,
                        "orderbook": (1 if (ob_ratio and ob_ratio >= 1.3) else -1 if (ob_ratio and ob_ratio <= 0.77) else 0) if use_ob else 0,
                        "macro":    (max(-1, min(1, mscore)) + max(-2, min(0, ssoft))) if use_macro else 0,
                        "trend":    (1 if price_now > ema_long else -1) if use_trend else 0,
                        "delta":    (1 if dratio > 0.15 else -1 if dratio < -0.15 else 0) if use_delta else 0,
                    }
                    sc = sum(parts.values())
                    # Modifier (kein additiver Faktor): sehr niedriges Volumen daempft das Signal
                    if use_volume and vr < 0.7:
                        sc = int(sc * 0.5)
                    # ADX-Trendfilter: schwacher Trend -> Signal daempfen (fail-open bei ADX==0)
                    if use_adx and 0 < adx_val < min_adx:
                        sc = int(sc * 0.5)

                    sig = "LONG" if sc >= thresh else "SHORT" if sc <= -thresh else "NEUTRAL"
                    # Harter Trend-Filter: ueber der langen EMA nur LONG, darunter nur SHORT.
                    # Gegen-Trend-Signal -> NEUTRAL (statt zu drehen -> trend-konforme Position
                    # wird gehalten). Genau das stoppt das "Short in die Rallye"-Problem.
                    sig_raw = sig
                    if use_trend_gate and ema_long > 0:
                        if sig == "LONG"  and price_now < ema_long: sig = "NEUTRAL"
                        elif sig == "SHORT" and price_now > ema_long: sig = "NEUTRAL"
                    # Harter ADX-Filter: nur bei echtem Trend handeln. Unter min_adx (schwacher
                    # Trend / Seitwaerts) -> NEUTRAL. Vermeidet die teuren Chop-Fehlsignale.
                    if use_adx_gate and 0 < adx_val < min_adx and sig != "NEUTRAL":
                        sig = "NEUTRAL"
                    _gate = " [Gate]" if sig != sig_raw else ""
                    with plock:
                        pstate["bots"]["signal"]["tokens"][sym].update({
                            "signal":sig,"score":sc,"rsi":round(rv,1),
                            "ema_fast":round(ef,4),"ema_slow":round(es,4),
                            "macd":round(ml,6),"macd_signal":round(ms,6),
                            "volume_ratio":round(vr,2),"funding_rate":round(fr,6),
                            "bb_upper":round(bb_u,4),"bb_lower":round(bb_l,4),
                            "atr":round(atr_val,4),"adx":round(adx_val,1),
                            "ob_ratio":ob_ratio,
                            "fear_greed":fg,"sentiment":sent,
                            "score_parts":{k:v for k,v in parts.items() if v != 0},
                        })
                    blog("signal",f"{cur}: RSI={rv:.1f} ADX={adx_val:.0f} OB={ob_ratio if ob_ratio else '-'} BB={'low' if price_now<bb_l else 'high' if price_now>bb_u else 'mid'} Score={sc:+d} -> {sig}{_gate}")

                    pos = client.position(sym)
                    with plock:
                        pstate["bots"]["signal"]["tokens"][sym]["position"] = pos

                    # Dynamische Position-Groesse (budget_usdt begrenzt die Basis, falls gesetzt)
                    base_cap   = bal if budget_usdt <= 0 else min(bal, budget_usdt)
                    trade_usdt = (base_cap * risk_pct / 100) if use_risk_pct else usdt_pt
                    if budget_usdt > 0:
                        trade_usdt = min(trade_usdt, budget_usdt)

                    # ATR-basierter SL/TP
                    def calc_sl_tp(px, direction):
                        if use_atr_sl and atr_val > 0:
                            sl_dist = atr_val * atr_sl_mult
                            tp_dist = atr_val * atr_tp_mult
                        else:
                            sl_dist = px * sl_pct
                            tp_dist = px * tp_pct
                        if direction == "LONG":
                            return px - sl_dist, px + tp_dist
                        return px + sl_dist, px - tp_dist

                    def _open(direction):
                        nonlocal open_pos_count
                        # Anti-Churn: nach dem Schliessen ist der Coin fuer cooldown_min Minuten
                        # gesperrt - stoppt das staendige Rein/Raus auf demselben Coin.
                        if cooldown_min > 0 and time.time() < cooldown_until.get(sym, 0):
                            return
                        if open_pos_count >= max_conc:
                            blog("signal",f"{cur}: Max. Positionen ({max_conc}) erreicht – kein neuer Trade","WARN")
                            return
                        # Budget-Limit pro Bot: bereits gebundene Margin summieren und keinen
                        # Trade eroeffnen, der das eingestellte Budget sprengt (harte Obergrenze).
                        this_trade = trade_usdt
                        if budget_usdt > 0:
                            with plock:
                                used = sum(float((pstate["bots"]["signal"]["tokens"].get(s,{})
                                            .get("position") or {}).get("marginSize", 0) or 0)
                                           for s in tokens)
                            free = budget_usdt - used
                            if free <= 1:
                                blog("signal",f"{cur}: Budget {budget_usdt:.0f} USDT ausgeschoepft ({used:.0f} gebunden) – kein neuer Trade","WARN")
                                return
                            this_trade = min(trade_usdt, free)
                        # Korrelations-Filter: keine neue Position, die zu stark mit einer
                        # bereits offenen zusammenhaengt (geballtes Risiko). Fail-open.
                        if use_corr_filter and corr_data:
                            conflict = _correlation_conflict(sym, direction, corr_data, open_positions, max_corr)
                            if conflict:
                                blog("signal",f"{cur}: uebersprungen – zu stark korreliert mit offener {conflict}-Position (r>={max_corr:.2f})","WARN")
                                return
                        px = client.price(sym)
                        if px <= 0: return
                        qs   = fmt_q(sym, (this_trade * lever) / px)
                        # Order-Sanity-Check (Lehre vom MT5-Bot): keine 0-Menge, keine
                        # stark ueberdimensionierte Order senden.
                        ok_sz, why = _size_check(qs, px, this_trade * lever)
                        if not ok_sz:
                            blog("signal",f"{cur}: Order abgebrochen – {why} (Menge '{qs}')","ERROR")
                            return
                        sl, tp = calc_sl_tp(px, direction)
                        if use_trailing: tp = None   # Trailing uebernimmt den Gewinn-Exit -> kein festes TP
                        resp = client.place_futures_order(
                            sym, "buy" if direction == "LONG" else "sell", qs,
                            close=False, tp=tp, sl=sl)
                        if resp.get("code") == "00000":
                            open_pos_count += 1
                            # neue Position in die Zyklus-Liste aufnehmen, damit spaetere
                            # Symbole im selben Zyklus den Korrelations-Check gegen sie sehen
                            open_positions.append((sym, direction))
                            trail[sym] = {"side": direction, "peak": px, "stop": sl}  # Trailing-Start am Einstiegs-SL
                            with plock:
                                pstate["bots"]["signal"]["trade_count"] += 1
                            _tp_s = f"{tp:.2f}" if tp is not None else "Trailing"
                            blog("signal",f"{cur}: {direction} @ {px:.2f} | SL={sl:.2f} TP={_tp_s} ({this_trade:.0f} USDT)","TRADE")
                            # SL/TP-Waechter: sicherstellen dass die Position wirklich geschuetzt ist
                            if use_sltp_guard:
                                _ensure_sltp(client, sym, direction, sl, tp, qs)

                    if pos:
                        ps = "LONG" if pos["holdSide"]=="long" else "SHORT"
                        entry = float(pos.get("openPriceAvg", 0))
                        # --- Trailing-Stop: Stop zieht mit dem Gewinn nach, nie zurueck. ---
                        trail_hit = False
                        if use_trailing and atr_val > 0 and entry > 0:
                            tdist = atr_val * trail_mult
                            st = trail.get(sym)
                            if not st or st.get("side") != ps:
                                # Fallback-Init (z.B. Bot-Neustart mit offener Position): Start am ATR-SL.
                                _isl = (entry - atr_val*atr_sl_mult) if ps=="LONG" else (entry + atr_val*atr_sl_mult)
                                st = {"side": ps, "peak": price_now, "stop": _isl}
                            if ps == "LONG":
                                st["peak"] = max(st["peak"], price_now)
                                st["stop"] = max(st["stop"], st["peak"] - tdist)
                                trail_hit = price_now <= st["stop"]
                            else:
                                st["peak"] = min(st["peak"], price_now)
                                st["stop"] = min(st["stop"], st["peak"] + tdist)
                                trail_hit = price_now >= st["stop"]
                            trail[sym] = st
                        _flip = (ps=="LONG" and sig=="SHORT") or (ps=="SHORT" and sig=="LONG")
                        if trail_hit or _flip:
                            # NUR die Schliessen-Order senden. Verbucht wird AUSSCHLIESSLICH, wenn die
                            # Position naechsten Zyklus wirklich verschwunden ist (prev_pos-Zweig unten).
                            # Das ist robust gegen "Order akzeptiert (00000), aber nicht gefuellt"
                            # (dann bleibt sie offen -> kein Phantom-Verlust) und verhindert Doppelbuchung.
                            if trail_hit:
                                trail.pop(sym, None)   # neu ermitteln, falls sie doch offen bleibt
                            resp = client.place_futures_order(
                                sym, "sell" if pos["holdSide"] == "long" else "buy",
                                str(pos["total"]), close=True)
                            if resp.get("code") != "00000" and (time.time() - close_warn_at.get(sym, 0) > 300):
                                close_warn_at[sym] = time.time()
                                blog("signal",f"{cur}: Schliessen abgelehnt ({ps} {pos.get('total')}) - {resp.get('msg','')}","WARN")
                            cycle_unreal += float(pos.get("unrealizedPL", 0) or 0)
                        else:
                            # Position bleibt offen -> unrealisierten PnL fuer die Anzeige mitzaehlen
                            cycle_unreal += float(pos.get("unrealizedPL", 0) or 0)
                    else:
                        # Pruefe ob eine zuvor offene Position seit dem letzten Zyklus
                        # durch SL/TP (oder manuell) geschlossen wurde
                        prev_pos = pstate["bots"]["signal"]["tokens"][sym].get("_last_pos")
                        if prev_pos:
                            ps    = "LONG" if prev_pos["holdSide"]=="long" else "SHORT"
                            entry = float(prev_pos.get("openPriceAvg",0))
                            upnl  = float(prev_pos.get("unrealizedPL",0))
                            psize = float(prev_pos.get("total",0))
                            # Fee aufs NOTIONAL (Entry+Exit), nicht auf den Profit -
                            # sonst zieht die DB fast keine Gebuehren ab (zu optimistischer PnL).
                            fee   = (entry + price_now) * psize * fee_rate
                            net   = upnl - fee
                            realized_pnl += net
                            db_save_trade("signal", cur, ps, entry, round(price_now,4),
                                          round(net,4), fee=round(fee,6), size=psize)
                            if net > 0:
                                win_streak += 1; loss_streak = 0
                            else:
                                loss_streak += 1; win_streak = 0
                            with plock:
                                pstate["bots"]["signal"].update({
                                    "win_streak":win_streak, "loss_streak":loss_streak})
                            open_pos_count = max(0, open_pos_count - 1)
                            open_positions[:] = [(s,d) for (s,d) in open_positions if s != sym]
                            if cooldown_min > 0: cooldown_until[sym] = time.time() + cooldown_min*60
                            trail.pop(sym, None)   # Position weg -> Trail verwerfen
                            blog("signal", f"{cur}: {ps} geschlossen | PnL {net:+.2f}", "TRADE")
                        if sig in ("LONG","SHORT") and not blackout:
                            _open(sig)
                    with plock:
                        pstate["bots"]["signal"]["tokens"][sym]["_last_pos"] = pos
                    time.sleep(0.5)
                except Exception as e:
                    blog("signal",f"{sym}: {e}","ERROR")
            # Zyklus-Ende: PnL = realisiert (diesen Lauf) + unrealisiert (offene Positionen).
            # Kontounabhaengig -> kein Leck mehr von anderen Bots auf demselben Konto.
            last_unreal = cycle_unreal
            with plock:
                _pnl = round(realized_pnl + cycle_unreal, 2)
                pstate["bots"]["signal"]["pnl"]     = _pnl
                pstate["bots"]["signal"]["pnl_pct"] = round((_pnl/start_bal*100) if start_bal > 0 else 0, 2)
        except Exception as e:
            blog("signal",f"Loop: {e}","ERROR")
        time.sleep(check)

    with plock:
        pstate["bots"]["signal"]["status"] = "STOPPED"
        pstate["bots"]["signal"]["started_at"] = 0
    blog("signal","Gestoppt.")

# ─────────────────────────────────────────────
#  GRID BOT
# ─────────────────────────────────────────────
def htf_trend_ema(client, sym, period):
    """Trend-EMA auf dem 1h-Zeitrahmen statt auf 1-Minuten-Rauschen.
    Die 1m-EMA kippt bei jedem Mini-Dip und erlaubt so Gegen-Trend-Trades
    (z.B. Short mitten in einer Rallye). Die 1h-EMA spiegelt den ECHTEN Trend:
    solange der 1h-Trend steigt, laesst der Trend-Gate nur Longs zu.
    Pro Coin ~20 min gecacht (ein 1h-Trend aendert sich nicht in Sekunden) ->
    keine 1h-Kerzen-Abfrage in jedem Zyklus. Fail-safe: bei Fehler alten Wert
    behalten, sonst 0.0 (Gate faellt dann auf 'kein Filter' zurueck)."""
    period = max(5, int(period))
    c = _htf_trend_cache.get(sym)
    if c and c[2] == period and time.time() - c[1] < 1200:
        return c[0]
    try:
        _o, _h, _l, closes_1h, _v = client.klines(sym, limit=max(period + 5, 60), granularity="1H")
        val = ema(closes_1h, period) if len(closes_1h) >= 5 else (c[0] if c else 0.0)
    except Exception:
        val = c[0] if c else 0.0
    _htf_trend_cache[sym] = (val, time.time(), period)
    return val

def grid_smart_range(client, sym, hours, cur_price):
    """Smart-Range: leitet die Grid-Range aus dem echten Hoch/Tief der letzten `hours`
    Stunden ab (1H-Kerzen) statt einer stumpfen +-5%-Spanne. Fuegt 10% Puffer je Seite an
    und garantiert, dass der aktuelle Preis mit etwas Luft in der Range liegt.
    Gibt (lower, upper) zurueck, oder (0.0, 0.0) wenn keine sinnvollen Daten da sind."""
    try:
        hours = max(6, min(168, int(hours)))
        _o, highs, lows, _c, _v = client.klines(sym, limit=hours, granularity="1H")
        if len(highs) < 3 or len(lows) < 3:
            return 0.0, 0.0
        hi, lo = max(highs), min(lows)
        if hi <= lo:
            return 0.0, 0.0
        pad = (hi - lo) * 0.10
        lo -= pad; hi += pad
        if cur_price > 0:                      # Preis mit etwas Luft einschliessen
            lo = min(lo, cur_price * 0.98)
            hi = max(hi, cur_price * 1.02)
        return lo, hi
    except Exception:
        return 0.0, 0.0

def run_grid(flag):
    cfg    = load_config()
    bc     = cfg["bots"]["grid"]
    client = BitgetClient(bc["api_key"], bc["api_secret"], bc["passphrase"],
                          cfg.get("live_mode", False))
    sym    = bc.get("symbol","BTCUSDT")
    upper  = float(bc.get("upper_price",0))
    lower  = float(bc.get("lower_price",0))
    n      = max(2, int(bc.get("grid_count",10)))
    invest = float(bc.get("investment",100))
    check  = int(bc.get("check_interval",10))
    step_size = float(bc.get("step_size", 0))       # Ziel-Stufengroesse USDT (0 = aus)
    seed_pos  = bc.get("seed_position", True)         # Grundbestand beim Start aufbauen
    sr_hours  = int(bc.get("smart_range_hours", 24))  # Rueckblick fuer die Smart-Range
    grid_lev  = int(bc.get("leverage", 0))            # 0 = Konto-Hebel lassen; >0 = selbst setzen
    sl_pct    = float(bc.get("stop_loss_pct", 0))     # 0 = aus; >0 = Notausstieg unter der Untergrenze

    start_bal = client.balance(retries=5)
    client.fetch_market_precision(TICK_DEC, MIN_QTY)
    if grid_lev > 0:
        try:
            client.set_leverage(sym, grid_lev)
            blog("grid", f"Hebel {grid_lev}x gesetzt")
        except Exception as e:
            blog("grid", f"Hebel setzen fehlgeschlagen ({e}) - Konto-Hebel bleibt", "WARN")
    cur_price = client.price(sym)

    if upper == 0 or lower == 0 or upper <= lower:
        if step_size > 0 and cur_price > 0:
            # Range so waehlen, dass jede Stufe genau step_size USDT gross ist (um den akt. Preis zentriert).
            span  = step_size * n
            lower = max(cur_price - span/2, cur_price * 0.05)
            upper = cur_price + span/2
            blog("grid",f"Range aus Stufengroesse {step_size:g} USDT: {lower:.2f} - {upper:.2f}")
        else:
            # Smart-Range: echtes Hoch/Tief der letzten sr_hours Stunden (statt stumpf +-5%).
            lo, hi = grid_smart_range(client, sym, sr_hours, cur_price)
            if hi > lo > 0:
                lower, upper = lo, hi
                blog("grid",f"Smart-Range aus {sr_hours}h Hoch/Tief: {lower:.2f} - {upper:.2f}")
            elif cur_price > 0:
                upper = cur_price * 1.05
                lower = cur_price * 0.95
                blog("grid",f"Auto-Range (Fallback): {lower:.2f} - {upper:.2f}")

    step    = (upper - lower) / n
    levels  = [lower + i * step for i in range(n + 1)]
    qty_lvl = (invest / n) / ((upper + lower) / 2)
    fee_rate = 0.0004  # Bitget Taker-Fee (Schaetzung fuer den PnL-Zaehler)
    pnl     = 0.0
    net_qty = 0.0     # aktuell gehaltene Long-Menge (lokale Buchhaltung)
    held    = []      # Stack der Level-Indizes mit offenem Kauf (Anzeige + LIFO-Close)
    trades  = 0
    # Crossing-Logik: current_idx = zuletzt erreichtes Grid-Level. Gekauft wird beim
    # Kreuzen NACH UNTEN, verkauft beim Kreuzen NACH OBEN. So wird pro Zelle nur einmal
    # gehandelt - kein endloses Nachkaufen bei Oszillation um dasselbe Level.
    current_idx = min(range(len(levels)), key=lambda i: abs(levels[i]-cur_price)) if cur_price > 0 else n // 2

    # Persistierten Grid-Stand laden - nur wenn die Konfiguration identisch ist (sonst passen
    # die gespeicherten Level-Indizes nicht mehr). So laeuft der Grid nach Neustart weiter.
    sig_key = f"{sym}|{round(upper,4)}|{round(lower,4)}|{n}"
    resumed = False
    _st = grid_load_state("grid")
    if _st and _st.get("key") == sig_key:
        net_qty     = float(_st.get("net_qty", 0))
        held        = list(_st.get("held", []))
        current_idx = int(_st.get("current_idx", current_idx))
        pnl         = float(_st.get("pnl", 0))
        trades      = int(_st.get("trades", 0))
        resumed     = True
        blog("grid", f"Fortgesetzt aus grid_state.json: {len(held)} offene Level, net_qty={net_qty:.6f}")

    def _persist_grid():
        grid_save_state("grid", {"key": sig_key, "net_qty": net_qty, "held": held,
                                 "current_idx": current_idx, "pnl": round(pnl, 6), "trades": trades})

    # Startbestand aufbauen: beim frischen Start (kein gespeicherter Stand) einmalig den
    # Bestand fuer alle Levels UEBER dem aktuellen Preis kaufen. So kann der Grid sofort in
    # BEIDE Richtungen handeln - verkaufen beim Steigen, nachkaufen beim Fallen. Ohne diesen
    # Grundbestand tradet ein von unten startender Grid in einem steigenden Markt gar nicht.
    if seed_pos and not resumed and cur_price > 0 and current_idx < n:
        seed_lvls = list(range(current_idx + 1, n + 1))   # Levels, in die hinein verkauft wird
        seed_qty  = qty_lvl * len(seed_lvls)
        qss = fmt_q(sym, seed_qty)
        ok_sz, why = _size_check(qss, cur_price, seed_qty * cur_price)
        if not ok_sz:
            blog("grid", f"Startbestand uebersprungen ({why}) - Grid handelt zunaechst nur bei fallendem Preis", "WARN")
        else:
            resp = client.place_futures_order(sym, "buy", qss, close=False)
            if resp.get("code") == "00000":
                net_qty = seed_qty
                held    = list(seed_lvls)
                trades += 1
                _persist_grid()
                blog("grid", f"Startbestand aufgebaut: {seed_qty:.6f} {sym.replace('USDT','')} ueber {len(seed_lvls)} Levels @ {cur_price:.2f}", "TRADE")
            else:
                blog("grid", f"Startbestand fehlgeschlagen: {resp.get('msg','')} - Grid handelt zunaechst nur bei fallendem Preis", "WARN")

    _held0 = set(held)
    with plock:
        pstate["bots"]["grid"].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,"started_at":time.time(),
            "symbol":sym,"upper":round(upper,2),"lower":round(lower,2),"step":round(step,2),
            "grid_orders":[{"price":round(l,2),"filled":(i in _held0),"side":"BUY" if l<=(upper+lower)/2 else "SELL"} for i,l in enumerate(levels)],
        })
    blog("grid",f"Grid aktiv: {sym} | {n} Levels | {lower:.2f} - {upper:.2f} USDT | Stufe {step:.2f}")

    while not flag["stop"]:
        try:
            px = client.price(sym)
            if px <= 0: time.sleep(check); continue

            # Notausstieg (opt-in): faellt der Preis stop_loss_pct unter die Untergrenze,
            # Bestand schliessen und Grid stoppen (statt ins Bodenlose zu halten).
            if sl_pct > 0 and net_qty > 0 and px <= lower * (1 - sl_pct):
                qcl = fmt_q(sym, net_qty)
                resp = client.place_futures_order(sym, "sell", qcl, close=True)
                ok = resp.get("code") == "00000"
                blog("grid", f"STOP-LOSS: Preis {px:.2f} unter {lower*(1-sl_pct):.2f} "
                             f"({sl_pct*100:.1f}% unter Untergrenze) -> Bestand {'geschlossen' if ok else 'schliessen FEHLGESCHLAGEN: '+resp.get('msg','')}, Grid stoppt", "ERROR")
                if ok:
                    net_qty = 0.0; held = []; _persist_grid()
                break

            # Preis faellt auf das naechste Level DARUNTER -> KAUFEN (open). Nur ein Schritt
            # pro Zyklus (bei schnellen Moves bewusst nur das naechste Level, s. DEPLOYMENT.md).
            if current_idx > 0 and px <= levels[current_idx - 1]:
                current_idx -= 1
                qsg = fmt_q(sym, qty_lvl)
                ok_sz, why = _size_check(qsg, px, qty_lvl * px)
                if not ok_sz:
                    blog("grid",f"Grid BUY @ {levels[current_idx]:.2f} abgebrochen – {why}","ERROR")
                    resp = {}
                else:
                    resp = client.place_futures_order(sym, "buy", qsg, close=False)
                ok = resp.get("code") == "00000"
                if ok:
                    net_qty += qty_lvl; held.append(current_idx); trades += 1
                    _persist_grid()
                status = "✓" if ok else f"Fehler {resp.get('msg','')}"
                blog("grid",f"Grid BUY @ {levels[current_idx]:.2f} [Level {current_idx+1}/{n}] {status}",
                     "TRADE" if ok else "ERROR")

            # Preis steigt auf das naechste Level DARUEBER -> VERKAUFEN (close) - nur mit Bestand
            elif current_idx < n and px >= levels[current_idx + 1]:
                current_idx += 1
                if net_qty <= 0 or not held:
                    blog("grid",f"Grid SELL @ {levels[current_idx]:.2f} [Level {current_idx+1}/{n}] uebersprungen - kein Bestand","WARN")
                else:
                    qty_trade = min(qty_lvl, net_qty)
                    qss = fmt_q(sym, qty_trade)
                    ok_sz, why = _size_check(qss, px, qty_trade * px)
                    if not ok_sz:
                        blog("grid",f"Grid SELL @ {levels[current_idx]:.2f} abgebrochen – {why}","ERROR")
                        resp = {}
                    else:
                        resp = client.place_futures_order(sym, "sell", qss, close=True)
                    ok = resp.get("code") == "00000"
                    if ok:
                        # Schaetzung: Level-Abstand minus geschaetzte Round-Trip-Gebuehren.
                        # (Kein echter Fill-Preis -> Slippage nicht beruecksichtigt, s. Doku.)
                        pnl += qty_trade * step - qty_trade * px * fee_rate * 2
                        net_qty = max(0.0, net_qty - qty_trade)
                        if held: held.pop()
                        trades += 1
                        _persist_grid()
                    elif resp and "no position" in str(resp.get("msg","")).lower():
                        # Konto hat keine Position -> lokale Buchhaltung war stale (z.B. nach manuellem
                        # "Close all"). Auf 0 syncen, damit der Grid nicht weiter Phantom-Bestand verkauft.
                        net_qty = 0.0; held = []; _persist_grid()
                        blog("grid","Bestand auf 0 synchronisiert (Konto hat keine Position)","WARN")
                    elif resp:
                        blog("grid",f"Grid SELL @ {levels[current_idx]:.2f} [Level {current_idx+1}/{n}] Fehler {resp.get('msg','')}","ERROR")
                    if ok:
                        blog("grid",f"Grid SELL @ {levels[current_idx]:.2f} [Level {current_idx+1}/{n}] ✓","TRADE")

            bal = client.balance(retries=2) or start_bal
            with plock:
                go = pstate["bots"]["grid"]["grid_orders"]
                hs = set(held)
                for i in range(len(go)):
                    go[i]["filled"] = (i in hs)
                pstate["bots"]["grid"].update({
                    "filled":len(held), "trade_count":trades, "pnl":round(pnl,4),
                    "balance":round(bal,2),
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
        except Exception as e:
            blog("grid",f"Loop: {e}","ERROR")
        time.sleep(check)

    with plock:
        pstate["bots"]["grid"]["status"] = "STOPPED"
        pstate["bots"]["grid"]["started_at"] = 0
    blog("grid","Gestoppt.")

# ─────────────────────────────────────────────
#  MULTI-GRID INSTANZEN
# ─────────────────────────────────────────────
def _ilog(inst_id, name, msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    with plock:
        inst = pstate.get("grid_instances",{}).get(inst_id,{})
        logs = inst.get("logs",[])
        logs.insert(0, {"t":ts,"l":level,"m":msg})
        if len(logs) > 40: logs.pop()
    getattr(log, level.lower() if level in ("INFO","ERROR") else "warning")(f"[grid:{name}] {msg}")
    # Telegram/Discord wie bei blog() - Multi-Grid-Instanzen waren bisher stumm
    if level == "TRADE":
        notify(f"[OK] GRID {name}: {msg}")
    elif level == "ERROR":
        notify(f"[FEHLER] GRID {name}: {msg}", True)

def run_grid_instance(flag, inst_cfg, inst_id):
    name   = inst_cfg.get("name","Grid")
    live   = load_config().get("live_mode", False)
    client = BitgetClient(inst_cfg["api_key"], inst_cfg["api_secret"],
                          inst_cfg["passphrase"], live)
    sym    = inst_cfg.get("symbol","BTCUSDT")
    upper  = float(inst_cfg.get("upper_price",0))
    lower  = float(inst_cfg.get("lower_price",0))
    n      = max(2, int(inst_cfg.get("grid_count",10)))
    invest = float(inst_cfg.get("investment",100))
    check  = int(inst_cfg.get("check_interval",10))
    step_size = float(inst_cfg.get("step_size", 0))
    seed_pos  = inst_cfg.get("seed_position", True)
    sr_hours  = int(inst_cfg.get("smart_range_hours", 24))
    grid_lev  = int(inst_cfg.get("leverage", 0))
    sl_pct    = float(inst_cfg.get("stop_loss_pct", 0))

    start_bal = client.balance(retries=5)
    client.fetch_market_precision(TICK_DEC, MIN_QTY)
    if grid_lev > 0:
        try:
            client.set_leverage(sym, grid_lev)
            _ilog(inst_id, name, f"Hebel {grid_lev}x gesetzt")
        except Exception as e:
            _ilog(inst_id, name, f"Hebel setzen fehlgeschlagen ({e}) - Konto-Hebel bleibt", "WARN")
    cur_price = client.price(sym)

    if upper == 0 or lower == 0 or upper <= lower:
        if step_size > 0 and cur_price > 0:
            span  = step_size * n
            lower = max(cur_price - span/2, cur_price * 0.05)
            upper = cur_price + span/2
            _ilog(inst_id, name, f"Range aus Stufengroesse {step_size:g} USDT: {lower:.2f} - {upper:.2f}")
        else:
            lo, hi = grid_smart_range(client, sym, sr_hours, cur_price)
            if hi > lo > 0:
                lower, upper = lo, hi
                _ilog(inst_id, name, f"Smart-Range aus {sr_hours}h Hoch/Tief: {lower:.2f} - {upper:.2f}")
            elif cur_price > 0:
                upper = cur_price * 1.05
                lower = cur_price * 0.95
                _ilog(inst_id, name, f"Auto-Range (Fallback): {lower:.2f} - {upper:.2f}")

    step   = (upper - lower) / n
    levels = [lower + i * step for i in range(n+1)]
    qty_l  = (invest / n) / ((upper + lower) / 2)
    fee_rate = 0.0004  # Bitget Taker-Fee (Schaetzung fuer den PnL-Zaehler)
    pnl    = 0.0
    net_qty = 0.0
    held   = []       # Stack der Level-Indizes mit offenem Kauf
    trades = 0
    # Crossing-Logik (wie run_grid): kaufen beim Kreuzen nach unten, verkaufen nach oben.
    current_idx = min(range(len(levels)), key=lambda i: abs(levels[i]-cur_price)) if cur_price > 0 else n // 2

    # Persistierten Stand dieser Instanz laden (nur bei identischer Konfiguration).
    sig_key = f"{sym}|{round(upper,4)}|{round(lower,4)}|{n}"
    resumed = False
    _st = grid_load_state(inst_id)
    if _st and _st.get("key") == sig_key:
        net_qty     = float(_st.get("net_qty", 0))
        held        = list(_st.get("held", []))
        current_idx = int(_st.get("current_idx", current_idx))
        pnl         = float(_st.get("pnl", 0))
        trades      = int(_st.get("trades", 0))
        resumed     = True
        _ilog(inst_id, name, f"Fortgesetzt aus grid_state.json: {len(held)} offene Level")

    def _persist_grid():
        grid_save_state(inst_id, {"key": sig_key, "net_qty": net_qty, "held": held,
                                  "current_idx": current_idx, "pnl": round(pnl, 6), "trades": trades})

    # Startbestand aufbauen (wie run_grid): einmalig beim frischen Start, damit die Instanz
    # sofort in beide Richtungen handelt.
    if seed_pos and not resumed and cur_price > 0 and current_idx < n:
        seed_lvls = list(range(current_idx + 1, n + 1))
        seed_qty  = qty_l * len(seed_lvls)
        qss = fmt_q(sym, seed_qty)
        ok_sz, why = _size_check(qss, cur_price, seed_qty * cur_price)
        if not ok_sz:
            _ilog(inst_id, name, f"Startbestand uebersprungen ({why}) - handelt zunaechst nur bei fallendem Preis", "WARN")
        else:
            resp = client.place_futures_order(sym, "buy", qss, close=False)
            if resp.get("code") == "00000":
                net_qty = seed_qty
                held    = list(seed_lvls)
                trades += 1
                _persist_grid()
                _ilog(inst_id, name, f"Startbestand aufgebaut: {seed_qty:.6f} ueber {len(seed_lvls)} Levels @ {cur_price:.2f}", "TRADE")
            else:
                _ilog(inst_id, name, f"Startbestand fehlgeschlagen: {resp.get('msg','')} - handelt zunaechst nur bei fallendem Preis", "WARN")

    _held0 = set(held)
    with plock:
        pstate["grid_instances"][inst_id].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,"started_at":time.time(),
            "symbol":sym,"upper":round(upper,2),"lower":round(lower,2),"step":round(step,2),
            "grid_orders":[{"price":round(l,2),"filled":(i in _held0),
                            "side":"BUY" if l<=(upper+lower)/2 else "SELL"}
                           for i,l in enumerate(levels)],
        })
    _ilog(inst_id, name, f"Grid aktiv: {sym} | {n} Levels | {lower:.2f}-{upper:.2f} | Stufe {step:.2f}")

    while not flag["stop"]:
        try:
            px = client.price(sym)
            if px <= 0: time.sleep(check); continue

            # Notausstieg (opt-in) wie run_grid.
            if sl_pct > 0 and net_qty > 0 and px <= lower * (1 - sl_pct):
                qcl = fmt_q(sym, net_qty)
                resp = client.place_futures_order(sym, "sell", qcl, close=True)
                ok = resp.get("code") == "00000"
                _ilog(inst_id, name, f"STOP-LOSS: Preis {px:.2f} unter {lower*(1-sl_pct):.2f} -> "
                                     f"Bestand {'geschlossen' if ok else 'schliessen FEHLGESCHLAGEN: '+resp.get('msg','')}, Grid stoppt", "ERROR")
                if ok:
                    net_qty = 0.0; held = []; _persist_grid()
                break

            if current_idx > 0 and px <= levels[current_idx - 1]:
                current_idx -= 1
                qsg = fmt_q(sym, qty_l)
                ok_sz, why = _size_check(qsg, px, qty_l * px)
                if not ok_sz:
                    _ilog(inst_id, name, f"Grid BUY @ {levels[current_idx]:.2f} abgebrochen – {why}","ERROR")
                    resp = {}
                else:
                    resp = client.place_futures_order(sym, "buy", qsg, close=False)
                ok = resp.get("code") == "00000"
                if ok:
                    net_qty += qty_l; held.append(current_idx); trades += 1
                    _persist_grid()
                if resp:
                    istatus = "OK" if ok else f"Fehler {resp.get('msg','')}"
                    _ilog(inst_id, name, f"Grid BUY @ {levels[current_idx]:.2f} L{current_idx+1}/{n} {istatus}",
                          "TRADE" if ok else "ERROR")

            elif current_idx < n and px >= levels[current_idx + 1]:
                current_idx += 1
                if net_qty <= 0 or not held:
                    _ilog(inst_id, name, f"Grid SELL @ {levels[current_idx]:.2f} L{current_idx+1}/{n} uebersprungen - kein Bestand","WARN")
                else:
                    qty_trade = min(qty_l, net_qty)
                    qss = fmt_q(sym, qty_trade)
                    ok_sz, why = _size_check(qss, px, qty_trade * px)
                    if not ok_sz:
                        _ilog(inst_id, name, f"Grid SELL @ {levels[current_idx]:.2f} abgebrochen – {why}","ERROR")
                        resp = {}
                    else:
                        resp = client.place_futures_order(sym, "sell", qss, close=True)
                    ok = resp.get("code") == "00000"
                    if ok:
                        # Schaetzung: Level-Abstand minus geschaetzte Round-Trip-Gebuehren.
                        # (Kein echter Fill-Preis -> Slippage nicht beruecksichtigt, s. Doku.)
                        pnl += qty_trade * step - qty_trade * px * fee_rate * 2
                        net_qty = max(0.0, net_qty - qty_trade)
                        if held: held.pop()
                        trades += 1
                        _persist_grid()
                        _ilog(inst_id, name, f"Grid SELL @ {levels[current_idx]:.2f} L{current_idx+1}/{n} OK","TRADE")
                    elif resp and "no position" in str(resp.get("msg","")).lower():
                        net_qty = 0.0; held = []; _persist_grid()
                        _ilog(inst_id, name, "Bestand auf 0 synchronisiert (Konto hat keine Position)","WARN")
                    else:
                        _ilog(inst_id, name, f"Grid SELL @ {levels[current_idx]:.2f} L{current_idx+1}/{n} Fehler {resp.get('msg','')}","ERROR")

            bal = client.balance(retries=2) or start_bal
            with plock:
                gi = pstate["grid_instances"].get(inst_id,{})
                if "grid_orders" in gi:
                    hs = set(held)
                    for i in range(len(gi["grid_orders"])):
                        gi["grid_orders"][i]["filled"] = (i in hs)
                gi["filled"]      = len(held)
                gi["trade_count"] = trades
                gi["pnl"]         = round(pnl,4)
                gi["balance"]     = round(bal,2)
                gi["last_update"] = datetime.now().strftime("%H:%M:%S")
        except Exception as e:
            _ilog(inst_id, name, f"Loop: {e}", "ERROR")
        time.sleep(check)

    with plock:
        pstate["grid_instances"].get(inst_id,{}).update({"status":"STOPPED","started_at":0})
    _ilog(inst_id, name, "Gestoppt.")

def start_grid_instance(inst_id):
    with _start_lock:
        if inst_id in grid_inst_threads and grid_inst_threads[inst_id].is_alive():
            return False, "Laeuft bereits"
        cfg  = load_config()
        inst = next((i for i in cfg.get("grid_instances",[]) if i["id"]==inst_id), None)
        if not inst:     return False, "Instanz nicht gefunden"
        if not inst.get("api_key") or not inst.get("api_secret"):
            return False, "API Key / Secret fehlt"
        with plock:
            pstate["grid_instances"][inst_id] = {
                "status":"STARTING","name":inst.get("name","Grid"),
                "balance":0.0,"start_bal":0.0,"pnl":0.0,
                "trade_count":0,"filled":0,"logs":[],"grid_orders":[],
                "symbol":inst.get("symbol","BTCUSDT"),"upper":0,"lower":0,"last_update":"",
            }
        f = {"stop": False}
        grid_inst_flags[inst_id]   = f
        t = threading.Thread(target=run_grid_instance, args=(f,inst,inst_id), daemon=True,
                             name=f"grid-{inst_id}")
        grid_inst_threads[inst_id] = t
        t.start()
        return True, "Gestartet"

def stop_grid_instance(inst_id):
    if inst_id in grid_inst_flags: grid_inst_flags[inst_id]["stop"] = True
    with plock:
        pstate["grid_instances"].get(inst_id,{}).update({"status":"STOPPING"})
    return True, "Stoppbefehl gesendet"

# ─────────────────────────────────────────────
#  DCA BOT
# ─────────────────────────────────────────────
def run_dca(flag):
    cfg      = load_config()
    bc       = cfg["bots"]["dca"]
    client   = BitgetClient(bc["api_key"], bc["api_secret"], bc["passphrase"],
                            cfg.get("live_mode", False))
    sym      = bc.get("symbol","BTCUSDT")
    interval = float(bc.get("interval_hours",24)) * 3600
    amount   = float(bc.get("amount_per_buy",20))
    check    = int(bc.get("check_interval",300))

    # DCA ist ein reiner Spot-Bot. Wir starten mit Spot-Balance,
    # auch wenn sie 0 ist – kein Fallback auf Futures (wuerde PnL verfaelschen).
    start_bal = client.spot_balance("USDT")
    # Persistierten Stand laden -> keine Amnesie nach Neustart, und kein Sofort-Kauf
    # bei jedem Start (last_buy bleibt erhalten).
    total_inv, total_qty, buy_count, last_buy = dca_load_state(sym)
    pnl       = 0.0
    if buy_count > 0:
        blog("dca", f"Fortgesetzt: {buy_count} Kaeufe, {total_inv:.2f} USDT investiert (aus dca_state.json)")

    with plock:
        pstate["bots"]["dca"].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,"started_at":time.time(),
            "next_buy":"Sofort beim naechsten Zyklus",
        })
    blog("dca",f"Aktiv | SPOT | {sym} | {amount} USDT alle {interval/3600:.0f}h | Spot-Balance: {start_bal:.2f} USDT")

    while not flag["stop"]:
        try:
            now = time.time()
            if now >= last_buy + interval:
                px = client.spot_price(sym)
                if px > 0:
                    ok, qty_bought, err = client.spot_buy(sym, amount)
                    if ok:
                        # Falls Bitget die tatsaechliche Menge nicht zurueckgibt,
                        # schaetzen wir sie aus Preis und Betrag
                        qty = qty_bought if qty_bought > 0 else amount / px
                        total_inv += amount
                        total_qty += qty
                        buy_count += 1
                        last_buy   = now
                        dca_save_state(sym, total_inv, total_qty, buy_count, last_buy)
                        # Kauf in die DB schreiben, damit DCA in Historie/Timing auftaucht
                        # (Spot-Kauf hat keinen realisierten PnL -> 0).
                        db_save_trade("dca", sym.replace("USDT",""), "buy", px, px, 0.0, fee=0.0, size=qty)
                        avg = total_inv / total_qty if total_qty > 0 else 0
                        blog("dca",
                            f"Spot-Kauf: ~{qty:.6f} {sym.replace('USDT','')} "
                            f"@ {px:.2f} | Avg: {avg:.2f} | Inv: {total_inv:.2f}","TRADE")
                        with plock:
                            pstate["bots"]["dca"].update({
                                "buys":buy_count,"invested":round(total_inv,2),
                                "avg_price":round(avg,2),"trade_count":buy_count,
                            })
                    else:
                        blog("dca",f"Spot-Order fehlgeschlagen: {err}","ERROR")

            # PnL: aktueller Spot-Wert minus investiertes USDT
            px = client.spot_price(sym)
            if px > 0 and total_qty > 0:
                pnl = total_qty * px - total_inv

            next_ts  = last_buy + interval
            next_str = datetime.fromtimestamp(next_ts).strftime("%d.%m %H:%M") if last_buy > 0 else "Sofort"
            # Spot-Balance direkt anzeigen – kein Futures-Fallback
            bal = client.spot_balance("USDT")

            with plock:
                pstate["bots"]["dca"].update({
                    "balance":round(bal,2),"pnl":round(pnl,2),
                    "next_buy":next_str,
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
        except Exception as e:
            blog("dca",f"Loop: {e}","ERROR")
        time.sleep(check)

    with plock:
        pstate["bots"]["dca"]["status"] = "STOPPED"
        pstate["bots"]["dca"]["started_at"] = 0
    blog("dca","Gestoppt.")

# ─────────────────────────────────────────────
#  NOTFALL-STOPP (PANIC BUTTON)
# ─────────────────────────────────────────────
def _panic_close_account(client, label, cancel_syms):
    """Schliesst alle offenen Positionen und storniert Orders fuer EIN Konto (client).
    Gibt (geschlossen, fehler) zurueck. Wird pro Haupt-Bot UND pro Grid-Instanz genutzt."""
    c, e = 0, 0
    # all_positions() erkennt Classic/UTA automatisch und liefert normalisierte Felder -
    # der frühere klassische Direktaufruf fand auf UTA-Konten nichts (bzw. warf).
    for pos in client.all_positions():
        sym = pos.get("symbol", "")
        ok, last_msg = False, ""
        for _ in range(3):
            resp = client.place_futures_order(
                sym, "sell" if pos["holdSide"] == "long" else "buy",
                str(pos["total"]), close=True)
            if resp.get("code") == "00000":
                ok = True; break
            last_msg = resp.get("msg", ""); time.sleep(1)
        if ok:
            c += 1; log.info(f"[PANIC] {label}: {sym} {pos['holdSide']} geschlossen")
        else:
            e += 1
            log.error(f"[PANIC] {label}: {sym} Fehler nach 3 Versuchen: {last_msg}")
            notify(f"[NOTFALL-STOPP] FEHLER: {label} {sym} konnte nicht geschlossen werden: {last_msg}", True)
    for csym in {s for s in cancel_syms if s}:
        client.cancel_all(csym)
        time.sleep(0.15)
    return c, e

def emergency_stop():
    """Stoppt alle Bots, storniert offene Orders und schliesst alle Positionen per Market."""
    log.warning("!!! NOTFALL-STOPP AUSGELOEST !!!")
    notify("[NOTFALL-STOPP] Alle Positionen werden geschlossen.", True)

    # Alle Bot-Threads stoppen
    for bid in ("signal","grid","dca"):
        if bid in bot_flags:
            bot_flags[bid]["stop"] = True
        with plock:
            pstate["bots"][bid]["status"] = "EMERGENCY STOP"
    # Auch alle Multi-Grid-Instanzen stoppen (eigene Threads/Sub-Accounts)
    for iid, f in list(grid_inst_flags.items()):
        f["stop"] = True
        with plock:
            if iid in pstate.get("grid_instances", {}):
                pstate["grid_instances"][iid]["status"] = "EMERGENCY STOP"

    cfg  = load_config()
    live = cfg.get("live_mode", False)
    closed, errors = 0, 0

    for bid in ("signal","grid","dca"):
        bc = cfg["bots"].get(bid, {})
        if not bc.get("api_key") or not bc.get("api_secret"):
            continue
        # Nur relevante Symbole stornieren (nicht ~250 aus TICK_DEC -> 429).
        if bid == "signal":  csyms = list(bc.get("tokens", []))
        elif bid == "grid":  csyms = [bc.get("symbol", "")]
        else:                csyms = []   # funding handelt nicht, dca ist Spot
        try:
            client = BitgetClient(bc["api_key"], bc["api_secret"], bc["passphrase"], live)
            c, e = _panic_close_account(client, bid, csyms)
            closed += c; errors += e
        except Exception as ex:
            errors += 1
            log.error(f"[PANIC] {bid}: {ex}")

    # Multi-Grid-Instanzen: jede laeuft auf ihrem EIGENEN Sub-Account mit eigenen Keys ->
    # separater Client, sonst bleiben ihre Positionen/Orders beim Notfall-Stopp offen.
    for inst in cfg.get("grid_instances", []):
        if not inst.get("api_key") or not inst.get("api_secret"):
            continue
        label = f"grid:{inst.get('name','?')}"
        try:
            iclient = BitgetClient(inst["api_key"], inst["api_secret"], inst["passphrase"], live)
            c, e = _panic_close_account(iclient, label, [inst.get("symbol", "")])
            closed += c; errors += e
        except Exception as ex:
            errors += 1
            log.error(f"[PANIC] {label}: {ex}")

    summary = f"Notfall-Stopp abgeschlossen: {closed} Positionen geschlossen, {errors} Fehler."
    log.warning(summary)
    notify(f"[NOTFALL-STOPP] {summary}", errors > 0)
    return {"closed": closed, "errors": errors}

# ─────────────────────────────────────────────
#  TAGES-ZUSAMMENFASSUNG (Telegram, 22:00 Uhr)
# ─────────────────────────────────────────────
def daily_summary_thread():
    while True:
        try:
            now    = datetime.now()
            target = now.replace(hour=22, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            time.sleep((target - now).total_seconds())

            with plock:
                bots = pstate["bots"]
                total_pnl = sum(bots[b].get("pnl",0) for b in bots)
                active    = sum(1 for b in bots if bots[b].get("status")=="RUNNING")
                trades    = sum(bots[b].get("trade_count",0) for b in bots)
                # Multi-Grid-Instanzen mit einrechnen (eigene Threads/Sub-Accounts)
                insts     = pstate.get("grid_instances", {})
                inst_pnl  = sum(g.get("pnl",0) for g in insts.values())
                inst_act  = sum(1 for g in insts.values() if g.get("status")=="RUNNING")
                inst_trd  = sum(g.get("trade_count",0) for g in insts.values())
                total_pnl += inst_pnl
                active    += inst_act
                trades    += inst_trd

            inst_line = f"davon Grid-Instanzen: {inst_pnl:+.2f} USDT, {inst_act} aktiv\n" if insts else ""
            notify(
                f"[DAILY SUMMARY] {datetime.now().strftime('%d.%m.%Y')}\n"
                f"Modus: {'LIVE' if pstate.get('live_mode') else 'DEMO'}\n"
                f"PnL gesamt (Signal/Grid/DCA + Instanzen): {total_pnl:+.2f} USDT\n"
                f"{inst_line}"
                f"Aktive Bots/Grids: {active}\n"
                f"Trades heute: {trades}"
            )
        except Exception as e:
            log.debug(f"daily_summary: {e}")
        time.sleep(60)  # Verhindert Doppelsendung in derselben Minute

# ─────────────────────────────────────────────
#  BOT MANAGER
# ─────────────────────────────────────────────
RUNNERS = {"signal":run_signal,"grid":run_grid,"dca":run_dca}

def start_bot(bot_id):
    if bot_id not in RUNNERS: return False, "Unbekannter Bot"
    with _start_lock:
        if bot_id in bot_threads and bot_threads[bot_id].is_alive():
            return False, "Bot laeuft bereits"
        cfg = load_config()
        bc  = cfg["bots"].get(bot_id, {})
        if not bc.get("api_key") or not bc.get("api_secret"):
            return False, "API Key / Secret fehlt. Bitte erst in SETTINGS eintragen und SPEICHERN klicken."
        if not bc.get("passphrase"):
            return False, "Passphrase fehlt. Bitte in SETTINGS eintragen und SPEICHERN klicken."
        f = {"stop": False}
        bot_flags[bot_id] = f
        t = threading.Thread(target=RUNNERS[bot_id], args=(f,), daemon=True, name=f"bot-{bot_id}")
        bot_threads[bot_id] = t
        t.start()
        return True, "Gestartet"

def stop_bot(bot_id):
    if bot_id in bot_flags: bot_flags[bot_id]["stop"] = True
    with plock: pstate["bots"][bot_id]["status"] = "STOPPING"
    return True, "Stoppbefehl gesendet"

# ─────────────────────────────────────────────
#  DASHBOARD HTML
# ─────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Platform</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#070708;--bg2:#0e0e10;--bg3:#141416;--border:#1e1e22;
  --text:#d4d4d8;--muted:#52525b;--dim:#27272a;
  --signal:#00d68f;--grid:#4da6ff;--funding:#a78bfa;--dca:#fbbf24;
  --red:#f87171;--white:#f4f4f5;--mode-bg:transparent;
}
body{background:var(--bg);color:var(--text);font-family:'JetBrains Mono',monospace;
     font-size:12px;min-height:100vh;overflow-x:hidden}
body.demo-mode{--mode-bg:rgba(77,166,255,.03)}
body.live-mode{--mode-bg:rgba(248,113,113,.03)}
body.demo-mode::after{content:'DEMO';position:fixed;bottom:16px;right:16px;
  font-size:10px;font-weight:700;letter-spacing:.15em;color:var(--grid);
  background:rgba(77,166,255,.12);border:1px solid rgba(77,166,255,.25);
  padding:4px 10px;border-radius:4px;pointer-events:none;z-index:999}
body.live-mode::after{content:'LIVE';position:fixed;bottom:16px;right:16px;
  font-size:10px;font-weight:700;letter-spacing:.15em;color:var(--red);
  background:rgba(248,113,113,.12);border:1px solid rgba(248,113,113,.25);
  padding:4px 10px;border-radius:4px;pointer-events:none;z-index:999}

/* NAV */
.nav{display:flex;align-items:center;padding:0 20px;height:48px;
     border-bottom:1px solid var(--border);background:var(--bg2);gap:4px;position:sticky;top:0;z-index:100}
.nav-brand{font-size:11px;font-weight:700;letter-spacing:.15em;color:var(--white);
           margin-right:20px;opacity:.9}
.tab{background:none;border:none;color:var(--muted);font-family:inherit;font-size:11px;
     font-weight:500;letter-spacing:.08em;padding:6px 14px;cursor:pointer;
     border-radius:4px;transition:all .15s;position:relative}
.tab:hover{color:var(--text);background:var(--dim)}
.tab.active{color:var(--white);background:var(--dim)}
.tab.active::after{content:'';position:absolute;bottom:-1px;left:0;right:0;height:2px;
                   background:var(--accent,var(--white));border-radius:2px 2px 0 0}
.tab[data-bot="signal"]{--accent:var(--signal)}
.tab[data-bot="grid"]{--accent:var(--grid)}
.tab[data-bot="funding"]{--accent:var(--funding)}
.tab[data-bot="dca"]{--accent:var(--dca)}
.status-dot{width:6px;height:6px;border-radius:50%;display:inline-block;
            margin-left:6px;vertical-align:middle}
.dot-run{background:var(--signal);box-shadow:0 0 6px var(--signal);animation:pulse 2s infinite}
.dot-stop{background:var(--dim)}
.dot-start{background:var(--dca);animation:pulse .8s infinite}
.dot-pause{background:var(--grid);animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.2}}

/* PANELS */
.panel{display:none;padding:20px}
.panel.active{display:block}

/* CARDS */
.grid{display:grid;gap:10px;margin-bottom:14px}
.g4{grid-template-columns:repeat(4,1fr)}
.g3{grid-template-columns:repeat(3,1fr)}
.g2{grid-template-columns:repeat(2,1fr)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px}
.card-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:6px}
.card-value{font-size:20px;font-weight:700;color:var(--white)}
.card-sub{font-size:10px;color:var(--muted);margin-top:3px}
.green{color:var(--signal)}.red{color:var(--red)}.blue{color:var(--grid)}
.purple{color:var(--funding)}.amber{color:var(--dca)}.white{color:var(--white)}

/* BOT HEADER */
.bot-header{display:flex;align-items:center;justify-content:space-between;
             margin-bottom:14px;padding:12px 16px;background:var(--bg2);
             border:1px solid var(--border);border-radius:8px}
.bot-title{font-size:13px;font-weight:700;letter-spacing:.08em}
.bot-meta{font-size:10px;color:var(--muted);margin-top:3px}
.btn{font-family:inherit;font-size:11px;font-weight:600;padding:7px 16px;
     border:none;border-radius:5px;cursor:pointer;letter-spacing:.06em;
     transition:all .15s}
.btn-start{background:var(--accent,var(--signal));color:#000}
.btn-stop{background:var(--dim);color:var(--red);border:1px solid var(--border)}
.btn-start:hover{filter:brightness(1.15)}
.btn-stop:hover{background:#1a0a0a}
.btn-save{background:var(--white);color:#000;padding:8px 24px;font-size:12px}
.btn-save:hover{filter:brightness(.9)}
.btn-panic{background:rgba(248,113,113,.15);border:1px solid rgba(248,113,113,.4);
           color:var(--red);font-family:inherit;font-size:12px;font-weight:700;
           padding:10px 24px;border-radius:6px;cursor:pointer;letter-spacing:.08em;
           transition:all .2s}
.btn-panic:hover{background:rgba(248,113,113,.3);border-color:var(--red);
                  box-shadow:0 0 16px rgba(248,113,113,.2)}
.mode-badge{font-size:10px;font-weight:700;letter-spacing:.12em;padding:4px 10px;
            border-radius:4px;border:1px solid}
.mode-demo{color:var(--grid);background:rgba(77,166,255,.1);border-color:rgba(77,166,255,.3)}
.mode-live{color:var(--red);background:rgba(248,113,113,.1);border-color:rgba(248,113,113,.3);
           animation:pulse .8s infinite}
.toggle-wrap{display:flex;align-items:center;gap:10px;padding:10px 0}
.toggle{position:relative;width:44px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.toggle-slider{position:absolute;cursor:pointer;inset:0;background:var(--dim);
               border-radius:24px;transition:.2s}
.toggle-slider:before{content:'';position:absolute;height:18px;width:18px;
  left:3px;bottom:3px;background:#888;border-radius:50%;transition:.2s}
.toggle input:checked + .toggle-slider{background:rgba(248,113,113,.3);border:1px solid var(--red)}
.toggle input:checked + .toggle-slider:before{transform:translateX(20px);background:var(--red)}
.preset-wrap{display:flex;gap:8px;margin-bottom:14px}
.preset-btn{background:var(--bg3);border:1px solid var(--border);color:var(--muted);
            font-family:inherit;font-size:10px;font-weight:600;letter-spacing:.06em;
            padding:6px 14px;border-radius:5px;cursor:pointer;transition:all .15s}
.preset-btn:hover{border-color:#555;color:var(--text)}
.preset-btn.low:hover{border-color:var(--signal);color:var(--signal)}
.preset-btn.med:hover{border-color:var(--grid);color:var(--grid)}
.preset-btn.degen:hover{border-color:var(--red);color:var(--red)}
.validate-row{display:flex;align-items:center;gap:10px;margin-top:10px}
.btn-validate{background:var(--bg3);border:1px solid var(--border);color:var(--muted);
              font-family:inherit;font-size:10px;padding:6px 14px;border-radius:5px;cursor:pointer}
.btn-validate:hover{border-color:#555;color:var(--text)}
.val-result{font-size:10px;display:none}
#tv-chart{margin-bottom:14px;border-radius:8px;overflow:hidden;border:1px solid var(--border)}
.btn-help{background:none;border:1px solid var(--border);color:var(--muted);
          font-family:inherit;font-size:11px;font-weight:700;
          width:24px;height:24px;border-radius:50%;cursor:pointer;
          transition:all .15s;flex-shrink:0}
.btn-help:hover{border-color:#555;color:var(--text);background:var(--dim)}

/* HELP MODAL */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.75);backdrop-filter:blur(3px);
               z-index:300;display:none;align-items:center;justify-content:center;padding:20px}
.modal-overlay.open{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:10px;
       padding:24px;max-width:540px;width:100%;max-height:80vh;overflow-y:auto}
.modal-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:16px}
.modal-title{font-size:14px;font-weight:700;letter-spacing:.06em}
.modal-x{background:none;border:none;color:var(--muted);cursor:pointer;
          font-size:16px;line-height:1;padding:0 4px}
.modal-x:hover{color:var(--text)}
.modal-sub{font-size:10px;color:var(--muted);margin-top:2px}
.modal-section{margin-bottom:16px;padding-bottom:16px;border-bottom:1px solid var(--border)}
.modal-section:last-of-type{border-bottom:none;margin-bottom:0;padding-bottom:0}
.modal-section-title{font-size:10px;font-weight:700;text-transform:uppercase;
                      letter-spacing:.1em;color:var(--muted);margin-bottom:8px}
.modal-text{font-size:11px;color:#bbb;line-height:1.8}
.modal-text b{color:var(--white);font-weight:600}
.mtable{width:100%;border-collapse:collapse;font-size:10px}
.mtable td{padding:5px 8px;border-bottom:1px solid var(--border);vertical-align:top}
.mtable tr:last-child td{border-bottom:none}
.mtable td:first-child{color:var(--muted);width:38%;white-space:nowrap}
.mtable td:last-child{color:#ccc;line-height:1.5}
.modal-close{background:var(--dim);border:1px solid var(--border);color:var(--muted);
              font-family:inherit;font-size:11px;letter-spacing:.06em;padding:8px;
              border-radius:5px;cursor:pointer;width:100%;margin-top:16px}
.modal-close:hover{background:var(--border);color:var(--text)}

/* TOKEN CARDS */
.token-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
.tc{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px}
.tc-name{font-weight:700;font-size:12px;letter-spacing:.05em;
         display:flex;justify-content:space-between;margin-bottom:6px}
.badge{font-size:10px;font-weight:700;padding:3px 8px;border-radius:4px;
       letter-spacing:.06em;display:inline-block;margin-bottom:6px}
.badge-long{color:var(--signal);background:rgba(0,214,143,.08);border:1px solid rgba(0,214,143,.2)}
.badge-short{color:var(--red);background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.2)}
.badge-neutral{color:var(--muted);background:rgba(255,255,255,.03);border:1px solid var(--border)}
.ind{display:flex;justify-content:space-between;font-size:10px;
     color:var(--muted);margin:2px 0}
.ind span:last-child{color:#888}
.sdots{display:flex;gap:3px;margin-bottom:5px}
.sd{width:6px;height:6px;border-radius:50%;background:var(--dim)}
.sd.g{background:var(--signal)}.sd.r{background:var(--red)}

/* GRID VISUALIZATION */
.grid-vis{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
          padding:14px;margin-bottom:14px}
.grid-label{font-size:10px;color:var(--muted);text-transform:uppercase;
             letter-spacing:.1em;margin-bottom:10px}
.grid-levels{display:flex;flex-direction:column;gap:3px;max-height:200px;overflow-y:auto}
.grid-level{display:flex;align-items:center;gap:10px;padding:3px 6px;
            border-radius:3px;font-size:10px}
.gl-price{width:90px;color:var(--text)}
.gl-bar{flex:1;height:4px;background:var(--dim);border-radius:2px;overflow:hidden}
.gl-fill{height:100%;border-radius:2px;transition:width .3s}
.gl-side{width:40px;text-align:right}

/* RATE TABLE */
.rate-table{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
            margin-bottom:14px;overflow:hidden}
.rt-head{display:grid;grid-template-columns:1fr 1fr 1fr 2fr;
         padding:8px 14px;background:var(--bg3);
         font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.rt-row{display:grid;grid-template-columns:1fr 1fr 1fr 2fr;
        padding:8px 14px;border-top:1px solid var(--border);
        font-size:11px;align-items:center}
.rt-row:hover{background:var(--bg3)}

/* MACRO BAR */
.macro-bar{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
           padding:12px 14px;margin-bottom:14px}
.macro-title{font-size:10px;color:var(--muted);text-transform:uppercase;
              letter-spacing:.1em;margin-bottom:8px}
.macro-events{display:flex;gap:6px;flex-wrap:wrap}
.me{font-size:10px;padding:3px 8px;border-radius:4px;border:1px solid}
.me.high{color:var(--red);background:rgba(248,113,113,.08);border-color:rgba(248,113,113,.2)}
.me.medium{color:var(--dca);background:rgba(251,191,36,.08);border-color:rgba(251,191,36,.2)}

/* OVERVIEW TABLE */
.ov-table{background:var(--bg2);border:1px solid var(--border);border-radius:8px;
          margin-bottom:14px;overflow:hidden}
.ov-head{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 120px;
         padding:8px 14px;background:var(--bg3);
         font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.ov-row{display:grid;grid-template-columns:2fr 1fr 1fr 1fr 1fr 120px;
        padding:10px 14px;border-top:1px solid var(--border);align-items:center}
.ov-bot-name{font-weight:600;font-size:12px}
.ov-status{font-size:10px;padding:2px 8px;border-radius:3px;
            display:inline-block;font-weight:600;letter-spacing:.05em}
.ov-uptime{display:block;font-size:9px;color:var(--muted);margin-top:3px;letter-spacing:.03em}
.cfg-summary{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:14px}
.cfg-sum-head{display:flex;justify-content:space-between;align-items:center;font-size:11px;font-weight:700;
              letter-spacing:.08em;color:var(--muted);margin-bottom:10px;text-transform:uppercase}
.cfg-sum-edit{background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;
              font-size:10px;padding:3px 10px;border-radius:4px;cursor:pointer;text-transform:none;letter-spacing:0}
.cfg-sum-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px 14px}
.cfg-sum-item{display:flex;flex-direction:column;gap:2px}
.cfg-sum-k{font-size:9px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.cfg-sum-v{font-size:12px;color:var(--text);font-weight:600}
.cfg-sum-filters{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.cfg-chip{font-size:10px;padding:3px 9px;border-radius:12px;border:1px solid var(--border)}
.cfg-chip.on{color:var(--signal);background:rgba(0,214,143,.10);border-color:rgba(0,214,143,.3)}
.cfg-chip.off{color:var(--dim);background:transparent}
.s-running{color:var(--signal);background:rgba(0,214,143,.1)}
.s-stopped{color:var(--muted);background:var(--dim)}
.s-starting{color:var(--dca);background:rgba(251,191,36,.1)}
.s-paused{color:var(--grid);background:rgba(77,166,255,.1)}
.s-stopping{color:var(--red);background:rgba(248,113,113,.1)}

/* LOG */
.log-wrap{background:var(--bg2);border:1px solid var(--border);border-radius:8px}
.log-head{display:flex;justify-content:space-between;padding:8px 14px;
           border-bottom:1px solid var(--border);font-size:10px;
           color:var(--muted);text-transform:uppercase;letter-spacing:.08em}
.log-body{height:150px;overflow-y:auto;padding:4px 14px}
.log-entry{display:flex;gap:10px;padding:3px 0;
            border-bottom:1px solid rgba(255,255,255,.02);font-size:11px}
.lt{color:var(--dim);min-width:55px}
.ll{min-width:45px}
.ll.INFO{color:var(--grid)}.ll.WARN{color:var(--dca)}.ll.ERROR{color:var(--red)}
.ll.TRADE{color:var(--signal)}.ll.MACRO{color:var(--funding)}

/* SETTINGS */
.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px;align-items:start;max-width:1500px}
.settings-col{min-width:0}
@media(max-width:900px){.settings-grid{grid-template-columns:1fr}}
.settings-section{background:var(--bg2);border:1px solid var(--border);
                   border-radius:8px;margin-bottom:10px;overflow:hidden}
.settings-head{padding:12px 16px;cursor:pointer;
                display:flex;justify-content:space-between;align-items:center;
                font-size:12px;font-weight:600;letter-spacing:.05em}
.settings-head:hover{background:var(--bg3)}
.settings-body{padding:16px;border-top:1px solid var(--border);display:none}
.settings-body.open{display:block}
.field-row{display:grid;grid-template-columns:180px 1fr;gap:10px;
           align-items:center;margin-bottom:10px}
.field-row label{font-size:11px;color:var(--muted)}
.field-row input{background:var(--bg);border:1px solid var(--border);
                  border-radius:5px;padding:8px 10px;color:var(--text);
                  font-family:inherit;font-size:11px;width:100%}
.field-row input:focus{outline:none;border-color:#444}
.field-row input::placeholder{color:var(--dim)}
.settings-note{font-size:10px;color:var(--muted);margin-top:6px;
                padding:8px;background:var(--bg3);border-radius:4px;
                line-height:1.6}
.save-row{display:flex;align-items:center;gap:14px;margin-top:16px}
.save-msg{font-size:11px;color:var(--signal);display:none}

/* SCROLLBAR */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:var(--bg)}
::-webkit-scrollbar-thumb{background:var(--dim);border-radius:2px}

/* SPARKLINE */
.spark{width:100%;height:40px;display:block;margin-top:6px}
.spark-line-pos{stroke:var(--signal);fill:none;stroke-width:1.5}
.spark-line-neg{stroke:var(--red);fill:none;stroke-width:1.5}
.spark-line-flat{stroke:var(--muted);fill:none;stroke-width:1.5}
.spark-fill-pos{fill:rgba(0,214,143,.08);stroke:none}
.spark-fill-neg{fill:rgba(248,113,113,.08);stroke:none}
.spark-zero{stroke:var(--dim);stroke-width:0.5}
.pnl-card{background:var(--bg2);border:1px solid var(--border);
          border-radius:8px;padding:12px 14px;margin-bottom:14px}
.pnl-card-label{font-size:10px;color:var(--muted);text-transform:uppercase;
                 letter-spacing:.1em;margin-bottom:2px;
                 display:flex;justify-content:space-between;align-items:center}

/* TREND ARROWS */
.trend-up{color:var(--signal)}
.trend-down{color:var(--red)}
.trend-flat{color:var(--muted)}

/* MOBILE RESPONSIVE */
@media(max-width:640px){
  .nav{overflow-x:auto;scrollbar-width:none;padding:0 10px;gap:2px}
  .nav::-webkit-scrollbar{display:none}
  .nav-brand{display:none}
  .tab{padding:6px 10px;font-size:10px;white-space:nowrap}
  .panel{padding:12px}
  .g4{grid-template-columns:1fr 1fr}
  .g3{grid-template-columns:1fr 1fr}
  .g2{grid-template-columns:1fr}
  .token-grid{grid-template-columns:1fr 1fr}
  .ov-head{grid-template-columns:2fr 1fr 1fr 80px}
  .ov-row{grid-template-columns:2fr 1fr 1fr 80px}
  .ov-col-trades,.ov-col-pnlpct{display:none}
  .bot-header{flex-wrap:wrap;gap:8px}
  .bot-header>div:first-child{flex:1 1 100%}
  .rt-head{grid-template-columns:1fr 1fr 1fr}
  .rt-row{grid-template-columns:1fr 1fr 1fr}
  .rt-col-dir{display:none}
  .field-row{grid-template-columns:1fr;gap:4px}
  .field-row label{margin-bottom:2px}
  .two-col{grid-template-columns:1fr}
  .card-value{font-size:16px}
  .settings-section{margin-bottom:8px}
  .mode-badge{display:none}
}</style>
</head>
<body>

<nav class="nav">
  <div class="nav-brand">TRADING PLATFORM</div>
  <button class="tab" data-tab="overview" onclick="switchTab('overview')">OVERVIEW</button>
  <button class="tab" data-tab="signal" data-bot="signal" onclick="switchTab('signal')">
    SIGNAL<span class="status-dot dot-stop" id="dot-signal"></span>
  </button>
  <button class="tab" data-tab="grid" data-bot="grid" onclick="switchTab('grid')">
    GRID<span class="status-dot dot-stop" id="dot-grid"></span>
  </button>
  <button class="tab" data-tab="dca" data-bot="dca" onclick="switchTab('dca')">
    DCA<span class="status-dot dot-stop" id="dot-dca"></span>
  </button>
  <button class="tab" data-tab="markt" onclick="switchTab('markt')">MARKT</button>
  <button class="tab" data-tab="trades" onclick="switchTab('trades')">TRADES</button>
  <button class="tab" data-tab="backtest" onclick="switchTab('backtest')">BACKTEST</button>
  <button class="tab" data-tab="alerts" onclick="switchTab('alerts')">ALERTS</button>
  <button class="tab" data-tab="settings" onclick="switchTab('settings')">SETTINGS</button>
  <button class="tab" data-tab="syslog" onclick="switchTab('syslog')">SYSTEM-LOG</button>
  <button id="lang-btn" onclick="toggleLang()" style="margin-left:auto;background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer;white-space:nowrap">DE / EN</button>
  <div style="flex:1"></div>
  <span id="platform-uptime" style="font-size:10px;color:var(--muted);margin-left:12px" title="Plattform-Laufzeit"></span>
  <span class="mode-badge mode-demo" id="mode-badge" style="margin-left:12px">DEMO</span>
  <span style="font-size:10px;color:var(--muted);margin-left:12px" id="last-update">--:--:--</span>
</nav>

<!-- OVERVIEW -->
<div id="panel-overview" class="panel active">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)">OVERVIEW</span>
    <div style="display:flex;gap:8px;align-items:center">
      <div id="circuit-badge" style="display:none;background:rgba(248,113,113,.12);border:1px solid rgba(248,113,113,.3);color:var(--red);font-size:10px;font-weight:700;padding:4px 10px;border-radius:4px">CIRCUIT BREAKER AKTIV</div>
      <button class="btn-panic" onclick="triggerPanic()" id="panic-btn">ALL STOP &amp; CLOSE</button>
      <button class="btn-help" onclick="showHelp('overview')" title="Erklaerung">?</button>
    </div>
  </div>
  <div class="grid g4">
    <div class="card"><div class="card-label" data-i18n="total_balance">Gesamt Balance</div>
      <div class="card-value blue" id="ov-balance">0.00</div><div class="card-sub">USDT (Demo)</div></div>
    <div class="card"><div class="card-label" data-i18n="total_pnl_nofund">Gesamt PnL</div>
      <div class="card-value" id="ov-pnl">+0.00</div><div class="card-sub" id="ov-pnlpct">0.00%</div></div>
    <div class="card"><div class="card-label" data-i18n="active_bots">Aktive Bots</div>
      <div class="card-value white" id="ov-active">0 / 4</div><div class="card-sub" data-i18n="running_total">Laufen / Gesamt</div></div>
    <div class="card"><div class="card-label" data-i18n="total_trades">Trades gesamt</div>
      <div class="card-value white" id="ov-trades">0</div><div class="card-sub" data-i18n="all_bots">Alle Bots</div></div>
  </div>
  <div class="ov-table">
    <div class="ov-head"><span>Bot</span><span>Status</span><span>Balance</span><span>PnL</span><span>Trades</span><span data-i18n="th_action">Aktion</span></div>
    <div id="ov-rows"></div>
  </div>
  <div class="macro-bar" id="fg-history-wrap" style="margin-bottom:14px">
    <div class="macro-title" style="display:flex;justify-content:space-between">
      <span data-i18n="fg_chart">Fear &amp; Greed Index – 30 Tage</span>
      <span id="fg-current" style="font-weight:700"></span>
    </div>
    <svg id="fg-chart" viewBox="0 0 760 52" preserveAspectRatio="none"
         style="width:100%;height:52px;display:block;margin-top:8px"></svg>
    <div id="fg-labels" style="display:flex;justify-content:space-between;font-size:9px;color:var(--muted);margin-top:2px"></div>
  </div>
  <div class="macro-bar">
    <div class="macro-title" data-i18n="macro_events">Makro-Ereignisse (48h)</div>
    <div class="macro-events" id="ov-macro"><span style="color:var(--dim)" data-i18n="no_finnhub">Kein Finnhub Key gesetzt</span></div>
  </div>
  <div id="ov-positions-wrap" style="display:none;margin-bottom:14px">
    <div class="macro-title" style="margin-bottom:8px" data-i18n="positions">Offene Positionen (alle Bots)</div>
    <div class="ov-table" style="margin-bottom:0">
      <div class="ov-head" style="grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr 1fr">
        <span>Bot</span><span>Symbol</span><span data-i18n="pos_side">Seite</span><span data-i18n="pos_size">Groesse</span><span data-i18n="pos_entry">Einstieg</span><span>uPnL</span><span data-i18n="pos_lev">Hebel</span>
      </div>
      <div id="ov-positions"></div>
    </div>
  </div>
  <div class="log-wrap">
    <div class="log-head"><span data-i18n="last_activity">Letzte Aktivitaet</span><span id="ov-logcount">0</span></div>
    <div class="log-body" id="ov-log"></div>
  </div>
</div>

<!-- MARKT -->
<div id="panel-markt" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)">MARKT-UEBERSICHT</span>
    <div style="display:flex;gap:10px;align-items:center">
      <span id="markt-update" style="font-size:10px;color:var(--muted)">--</span>
      <button onclick="loadMarket()" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer">Aktualisieren</button>
    </div>
  </div>
  <div class="ov-table">
    <div id="markt-head" class="ov-head" style="grid-template-columns:80px 1fr 80px 80px 80px 80px 120px">
      <span>Symbol</span><span data-i18n="th_price">Preis</span><span>24h %</span><span data-i18n="th_high24">24h Hoch</span><span data-i18n="th_low24">24h Tief</span><span>Funding</span><span data-i18n="th_vol_m">Volumen (Mio $)</span>
    </div>
    <div id="markt-rows"><div style="padding:20px;color:var(--muted);font-size:11px" data-i18n="loading_market">Lade Marktdaten...</div></div>
  </div>
</div>

<!-- TRADES -->
<div id="panel-trades" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)">TRADE-HISTORIE</span>
    <div style="display:flex;gap:8px;align-items:center">
      <select id="trades-filter" onchange="renderTrades()" style="background:var(--bg2);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:10px;padding:5px 8px;border-radius:4px">
        <option value="all">Alle Bots</option>
        <option value="signal">Signal Bot</option>
        <option value="grid">Grid Bot</option>
        <option value="dca">DCA Bot</option>
      </select>
      <button onclick="loadTrades()" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer" data-i18n="load">Laden</button>
    </div>
  </div>
  <div id="trades-summary" style="display:flex;gap:10px;margin-bottom:14px;flex-wrap:wrap"></div>
  <div class="ov-table" style="margin-bottom:14px">
    <div class="ov-head" style="grid-template-columns:90px 70px 60px 60px 80px 60px 80px 70px">
      <span data-i18n="th_time">Zeit</span><span>Bot</span><span>Symbol</span><span data-i18n="pos_side">Seite</span><span data-i18n="th_price">Preis</span><span data-i18n="th_qty">Menge</span><span>PnL</span><span data-i18n="th_fee">Gebuehr</span>
    </div>
    <div id="trades-rows"><div style="padding:20px;color:var(--muted);font-size:11px" data-i18n="click_load">Auf "Laden" klicken.</div></div>
  </div>
  <div style="margin-top:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <span style="font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.08em">TRADE-TIMING-ANALYSE</span>
      <button onclick="loadTradeTiming()" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer">Analyse laden</button>
    </div>
    <div style="font-size:10px;color:var(--muted);margin-bottom:8px">Wann sind Trades am profitabelsten? (Aus SQLite DB)</div>
    <div id="timing-chart" style="height:80px;display:flex;gap:2px;align-items:flex-end"></div>
    <div id="timing-labels" style="display:flex;gap:2px;margin-top:3px"></div>
  </div>
</div>

<!-- SIGNAL BOT -->
<div id="panel-signal" class="panel">
  <div class="bot-header">
    <div>
      <div class="bot-title" style="color:var(--signal)">SIGNAL BOT</div>
      <div class="bot-meta" data-i18n="meta_signal">RSI · EMA · MACD · Funding · Makro | 3x Hebel</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button class="btn-help" onclick="showHelp('signal')" title="Erklaerung">?</button>
      <span id="signal-status-badge" class="ov-status s-stopped">STOPPED</span>
      <button class="btn btn-start" style="--accent:var(--signal)" id="signal-btn" onclick="toggleBot('signal')">START</button>
    </div>
  </div>
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card"><div class="card-label">PnL</div>
      <div class="card-value" id="s-pnl">+0.00</div><div class="card-sub" id="s-pnlpct">0.00%</div></div>
    <div class="card"><div class="card-label" data-i18n="trades">Trades</div>
      <div class="card-value white" id="s-trades">0</div><div class="card-sub" data-i18n="executed">Ausgefuehrt</div></div>
    <div class="card"><div class="card-label" data-i18n="macro">Makro</div>
      <div class="card-value" id="s-blackout">OK</div><div class="card-sub" data-i18n="no_blackout">Kein Blackout</div></div>
    <div class="card"><div class="card-label">Win/Loss Streak</div>
      <div style="display:flex;gap:6px;align-items:baseline;margin-top:4px">
        <span id="s-win-streak" style="font-size:18px;font-weight:700;color:var(--signal)">0W</span>
        <span id="s-loss-streak" style="font-size:18px;font-weight:700;color:var(--red)">0L</span>
      </div>
      <div class="card-sub" id="s-streak-info" data-i18n="cur_streak">aktuell</div>
    </div>
  </div>
  <div class="cfg-summary" id="s-settings">
    <div class="cfg-sum-head">
      <span data-i18n="cfg_configured">Aktuelle Konfiguration</span>
      <button onclick="switchTab('settings')" class="cfg-sum-edit" data-i18n="cfg_edit">bearbeiten</button>
    </div>
    <div class="cfg-sum-grid" id="s-settings-grid"></div>
    <div class="cfg-sum-filters" id="s-settings-filters"></div>
  </div>
  <div class="pnl-card">
    <div class="pnl-card-label">
      <span data-i18n="pnl_history">PnL-Verlauf</span>
      <span id="s-trend" class="trend-flat">- -</span>
    </div>
    <svg class="spark" id="s-spark" viewBox="0 0 400 40" preserveAspectRatio="none"></svg>
  </div>
  <div class="token-grid" id="s-tokens"></div>
  <div class="macro-bar">
    <div class="macro-title" data-i18n="macro_events">Makro-Ereignisse</div>
    <div class="macro-events" id="s-macro"></div>
  </div>
  <div class="log-wrap">
    <div class="log-head"><span>Signal Bot Log</span><span id="s-logcount"></span></div>
    <div class="log-body" id="s-log"></div>
  </div>
</div>

<!-- GRID BOT -->
<div id="panel-grid" class="panel">
  <div class="bot-header">
    <div>
      <div class="bot-title" style="color:var(--grid)">GRID BOT</div>
      <div class="bot-meta" data-i18n="meta_grid">Automatische Kauf/Verkauf-Level im Preis-Raster</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button class="btn-help" onclick="showHelp('grid')" title="Erklaerung">?</button>
      <span id="grid-status-badge" class="ov-status s-stopped">STOPPED</span>
      <button class="btn btn-start" style="--accent:var(--grid)" id="grid-btn" onclick="toggleBot('grid')">START</button>
    </div>
  </div>
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card"><div class="card-label" data-i18n="balance">Balance</div>
      <div class="card-value blue" id="g-balance">0.00</div><div class="card-sub">USDT</div></div>
    <div class="card"><div class="card-label">PnL</div>
      <div class="card-value" id="g-pnl">+0.00</div><div class="card-sub" data-i18n="grid_profits">Grid-Gewinne</div></div>
    <div class="card"><div class="card-label" data-i18n="filled_levels">Gefuellte Level</div>
      <div class="card-value white" id="g-filled">0</div><div class="card-sub" id="g-range">–</div></div>
    <div class="card"><div class="card-label">Symbol</div>
      <div class="card-value white" id="g-symbol">–</div><div class="card-sub">Futures</div></div>
  </div>
  <div class="cfg-summary" id="g-settings">
    <div class="cfg-sum-head">
      <span data-i18n="cfg_configured">Aktuelle Konfiguration</span>
      <button onclick="switchTab('settings')" class="cfg-sum-edit" data-i18n="cfg_edit">bearbeiten</button>
    </div>
    <div class="cfg-sum-grid" id="g-settings-grid"></div>
  </div>
  <div class="grid-vis">
    <div class="grid-label">Grid-Level</div>
    <div class="grid-levels" id="g-levels"><span style="color:var(--dim)">Bot nicht aktiv</span></div>
  </div>
  <div id="tv-chart" style="height:260px"></div>
  <div class="pnl-card">
    <div class="pnl-card-label"><span data-i18n="pnl_history">PnL-Verlauf</span><span id="g-trend" class="trend-flat">- -</span></div>
    <svg class="spark" id="g-spark" viewBox="0 0 400 40" preserveAspectRatio="none"></svg>
  </div>
  <div class="log-wrap">
    <div class="log-head"><span>Grid Bot Log</span><span id="g-logcount"></span></div>
    <div class="log-body" id="g-log"></div>
  </div>

  <!-- MULTI-GRID INSTANZEN -->
  <div style="margin-top:20px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)">WEITERE GRID-INSTANZEN</span>
      <button onclick="toggleAddGrid()" class="btn btn-start" style="--accent:var(--grid);padding:6px 14px;font-size:11px">+ GRID HINZUFUEGEN</button>
    </div>

    <!-- ADD FORM -->
    <div id="add-grid-form" style="display:none;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px">
      <div class="card-label" style="margin-bottom:12px">Neue Grid-Instanz konfigurieren</div>
      <div class="grid g2" style="gap:10px;margin-bottom:10px">
        <div><div class="card-label" style="margin-bottom:4px">Name</div>
          <input type="text" id="ng-name" placeholder="z.B. ETH Grid" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
        <div><div class="card-label" style="margin-bottom:4px">Symbol</div>
          <select id="ng-sym" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
            <option>BTCUSDT</option><option>ETHUSDT</option><option>SOLUSDT</option><option>XRPUSDT</option><option>DOGEUSDT</option>
          </select></div>
        <div><div class="card-label" style="margin-bottom:4px">Grid Levels</div>
          <input type="number" id="ng-n" value="10" min="2" max="50" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
        <div><div class="card-label" style="margin-bottom:4px">Budget (USDT)</div>
          <input type="number" id="ng-inv" value="100" min="10" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
        <div><div class="card-label" style="margin-bottom:4px">Stufengroesse (USDT, 0 = auto)</div>
          <input type="number" id="ng-step" value="0" min="0" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
        <div><div class="card-label" style="margin-bottom:4px">API Key</div>
          <input type="text" id="ng-key" placeholder="Bitget API Key" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
        <div><div class="card-label" style="margin-bottom:4px">API Secret</div>
          <input type="password" id="ng-sec" placeholder="Bitget API Secret" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
        <div><div class="card-label" style="margin-bottom:4px">Passphrase</div>
          <input type="password" id="ng-pass" placeholder="Bitget Passphrase" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
      </div>
      <div style="display:flex;gap:10px">
        <button onclick="addGridInstance()" class="btn btn-start" style="--accent:var(--grid);padding:8px 20px">HINZUFUEGEN</button>
        <button onclick="toggleAddGrid()" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:11px;padding:8px 16px;border-radius:5px;cursor:pointer">ABBRECHEN</button>
      </div>
    </div>

    <div id="grid-instances-list"><div style="font-size:11px;color:var(--muted)">Noch keine weiteren Instanzen. Klick auf "+ Grid hinzufuegen".</div></div>
  </div>
</div>

<!-- MARKT + KALENDER -->
<div id="panel-markt" class="panel">
  <!-- Abschnitt 1: Markt-Uebersicht -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="markt_title">MARKT-UEBERSICHT</span>
    <div style="display:flex;gap:8px;align-items:center">
      <span id="markt-update" style="font-size:10px;color:var(--muted)">--</span>
      <button onclick="loadMarket()" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer" data-i18n="refresh">Aktualisieren</button>
    </div>
  </div>
  <div class="ov-table" style="margin-bottom:28px">
    <div id="markt-head" class="ov-head" style="grid-template-columns:80px 1fr 80px 80px 80px 80px 120px">
      <span>Symbol</span><span data-i18n="th_price">Preis</span><span>24h %</span><span data-i18n="th_high24">24h Hoch</span><span data-i18n="th_low24">24h Tief</span><span>Funding</span><span data-i18n="th_vol_m">Volumen (Mio $)</span>
    </div>
    <div id="markt-rows"><div style="padding:20px;color:var(--muted);font-size:11px" data-i18n="loading_market">Lade Marktdaten...</div></div>
  </div>

  <!-- Divider -->
  <div style="border-top:1px solid var(--border);margin-bottom:20px"></div>

  <!-- Abschnitt 2: Wirtschaftskalender -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="econ_cal">WIRTSCHAFTSKALENDER</span>
    <div style="display:flex;gap:6px;align-items:center">
      <button onclick="filterKal('all')" id="kf-all" class="preset-btn med" style="padding:4px 10px" data-i18n="filter_all">ALLE</button>
      <button onclick="filterKal('US')"  id="kf-us"  class="preset-btn degen" style="padding:4px 10px">USA</button>
      <button onclick="filterKal('EU')"  id="kf-eu"  class="preset-btn med" style="padding:4px 10px">EU</button>
      <button onclick="loadKalender(true)" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer" data-i18n="reload">Neu laden</button>
    </div>
  </div>
  <div id="kal-blackout-info" style="display:none;background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.2);border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:11px;color:var(--red)" data-i18n="us_blackout">
    US BLACKOUT AKTIV - Signal Bot oeffnet keine neuen Positionen
  </div>
  <div class="ov-table">
    <div class="ov-head" style="grid-template-columns:70px 50px 1fr 80px 80px 80px">
      <span data-i18n="kal_time">Zeit (UTC)</span><span data-i18n="kal_country">Land</span><span data-i18n="kal_event">Ereignis</span><span>Impact</span><span data-i18n="kal_actual">Aktuell</span><span data-i18n="kal_forecast">Prognose</span>
    </div>
    <div id="kal-rows"><div style="padding:20px;color:var(--muted);font-size:11px" data-i18n="loading_cal">Lade Kalender... (Finnhub API Key in Settings benoetigt)</div></div>
  </div>
</div>

<!-- DCA BOT -->
<div id="panel-dca" class="panel">
  <div class="bot-header">
    <div>
      <div class="bot-title" style="color:var(--dca)">DCA BOT</div>
      <div class="bot-meta" data-i18n="meta_dca">Zeitbasiertes Kaufen mit Durchschnittskosteneffekt</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button class="btn-help" onclick="showHelp('dca')" title="Erklaerung">?</button>
      <span id="dca-status-badge" class="ov-status s-stopped">STOPPED</span>
      <button class="btn btn-start" style="--accent:var(--dca)" id="dca-btn" onclick="toggleBot('dca')">START</button>
    </div>
  </div>
  <div class="grid g4" style="margin-bottom:14px">
    <div class="card"><div class="card-label" data-i18n="balance">Balance</div>
      <div class="card-value blue" id="d-balance">0.00</div><div class="card-sub">USDT</div></div>
    <div class="card"><div class="card-label" data-i18n="invested">Investiert</div>
      <div class="card-value white" id="d-invested">0.00</div><div class="card-sub" data-i18n="usdt_total">USDT gesamt</div></div>
    <div class="card"><div class="card-label">PnL</div>
      <div class="card-value" id="d-pnl">+0.00</div><div class="card-sub" id="d-avg">Avg: –</div></div>
    <div class="card"><div class="card-label" data-i18n="next_buy">Naechster Kauf</div>
      <div class="card-value amber" id="d-next" style="font-size:14px">–</div><div class="card-sub" id="d-buys">0</div></div>
  </div>
  <div class="pnl-card">
    <div class="pnl-card-label"><span data-i18n="pnl_history">PnL-Verlauf</span><span id="d-trend" class="trend-flat">- -</span></div>
    <svg class="spark" id="d-spark" viewBox="0 0 400 40" preserveAspectRatio="none"></svg>
  </div>
  <div class="log-wrap">
    <div class="log-head"><span>DCA Bot Log</span><span id="d-logcount"></span></div>
    <div class="log-body" id="d-log"></div>
  </div>
</div>

<!-- BACKTEST -->
<div id="panel-backtest" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)">BACKTESTING</span>
    <button class="btn-help" onclick="showHelp('backtest')" title="Erklaerung">?</button>
  </div>
  <div class="card" style="margin-bottom:14px;padding:16px">
    <div class="grid g3" style="margin-bottom:14px;gap:10px">
      <div><div class="card-label" style="margin-bottom:4px">Symbol</div>
        <select id="bt-symbol" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
          <option>BTCUSDT</option><option>ETHUSDT</option><option>SOLUSDT</option>
          <option>XRPUSDT</option><option>DOGEUSDT</option>
        </select></div>
      <div><div class="card-label" style="margin-bottom:4px" data-i18n="bt_period">Zeitraum</div>
        <select id="bt-days" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
          <option value="7">7 Tage</option><option value="14" selected>14 Tage</option>
          <option value="30">30 Tage</option><option value="60">60 Tage</option>
          <option value="90">90 Tage</option><option value="180">180 Tage</option>
          <option value="365">365 Tage (1 Jahr)</option>
          <option value="730">730 Tage (2 Jahre)</option>
        </select></div>
      <div><div class="card-label" style="margin-bottom:4px" data-i18n="bt_lever">Hebel</div>
        <input type="number" id="bt-lever" value="3" min="1" max="10"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
      <div><div class="card-label" style="margin-bottom:4px" data-i18n="bt_thresh">Signal-Schwelle (1-3)</div>
        <input type="number" id="bt-thresh" value="2" min="1" max="3"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
      <div><div class="card-label" style="margin-bottom:4px">Stop Loss %</div>
        <input type="number" id="bt-sl" value="1.0" step="0.1" min="0.1" max="5"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
      <div><div class="card-label" style="margin-bottom:4px">Take Profit %</div>
        <input type="number" id="bt-tp" value="2.0" step="0.1" min="0.1" max="10"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
      <div><div class="card-label" style="margin-bottom:4px" data-i18n="bt_pos">Positionsgroesse %</div>
        <input type="number" id="bt-pos" value="10" step="1" min="1" max="100" title="Anteil des Kapitals als Margin pro Trade (Live: Risiko pro Trade %)"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
    </div>
    <button onclick="runBacktest()" id="bt-run-btn" class="btn btn-start" style="--accent:var(--signal);width:100%;padding:10px">
      BACKTEST STARTEN
    </button>
    <div style="display:flex;gap:16px;margin-top:10px;align-items:center;flex-wrap:wrap">
      <label style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--muted);cursor:pointer">
        <input type="checkbox" id="bt-walkforward"> Walk-Forward (70/30 Train/Test Split)
      </label>
      <button onclick="runMultiBacktest()" id="bt-multi-btn"
        style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:6px 14px;border-radius:4px;cursor:pointer">
        ALLE SYMBOLE VERGLEICHEN
      </button>
    </div>
    <div style="font-size:10px;color:var(--muted);margin-top:8px;padding:6px 8px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;line-height:1.7">
      Backtest: 5 Indikatoren (EMA, Wilder RSI, MACD, BB, Volume) + ATR-SL. Gebuehren: 0.04% pro Trade.
      Walk-Forward: 70% Training / 30% Test – verhindert Overfitting.
    </div>
  </div>
  <div id="bt-result" style="display:none">
    <div class="grid g4" id="bt-stats" style="margin-bottom:14px"></div>
    <div class="grid" style="grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
      <div class="card" style="padding:10px">
        <div class="card-label">Sharpe Ratio</div>
        <div class="card-value white" id="bt-sharpe">-</div>
        <div class="card-sub">Gut: >1.5 | Sehr gut: >2.0</div>
      </div>
      <div class="card" style="padding:10px">
        <div class="card-label" data-i18n="bt_fees_total">Gebuehren gesamt</div>
        <div class="card-value" id="bt-fees" style="color:var(--red)">-</div>
        <div class="card-sub">0.04% Taker pro Trade</div>
      </div>
    </div>
    <div id="bt-walkforward-info" style="display:none;margin-bottom:10px;padding:8px 12px;background:rgba(0,214,143,.06);border:1px solid rgba(0,214,143,.15);border-radius:5px;font-size:10px;color:var(--signal)"></div>
    <div class="pnl-card" style="margin-bottom:14px">
      <div class="pnl-card-label"><span>Equity-Kurve (Startwert: 1000 USDT)</span><span id="bt-final"></span></div>
      <svg id="bt-spark" class="spark" viewBox="0 0 400 40" preserveAspectRatio="none"></svg>
    </div>
    <div class="ov-table">
      <div class="ov-head" style="grid-template-columns:70px 80px 80px 70px 70px 60px">
        <span data-i18n="pos_side">Seite</span><span data-i18n="bt_entry">Einstieg</span><span data-i18n="bt_exit">Ausstieg</span><span>PnL</span><span data-i18n="th_fee">Gebuehr</span><span data-i18n="bt_res">Erg.</span>
      </div>
      <div id="bt-trades"></div>
    </div>
  </div>
  <div id="bt-multi-result" style="display:none;margin-top:14px">
    <div style="font-size:11px;font-weight:700;color:var(--muted);letter-spacing:.08em;margin-bottom:10px">SYMBOL-VERGLEICH</div>
    <div class="ov-table">
      <div class="ov-head" style="grid-template-columns:90px 1fr 70px 70px 70px 70px 70px">
        <span>Symbol</span><span>Trades / Win%</span><span>PnL</span><span>Sharpe</span><span>Drawdown</span><span data-i18n="bt_fees">Gebuehren</span><span data-i18n="bt_final">Endkapital</span>
      </div>
      <div id="bt-multi-rows"></div>
    </div>
  </div>
  <div id="bt-error" style="display:none;padding:16px;color:var(--red);font-size:11px"></div>
</div>

<!-- ALERTS -->
<div id="panel-alerts" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)">ALERTS &amp; BENACHRICHTIGUNGEN</span>
    <button class="btn-help" onclick="showHelp('alerts')" title="Erklaerung">?</button>
  </div>
  <div class="settings-note" style="margin-bottom:14px">
    Alerts senden Telegram-Nachrichten wenn eine Bedingung zutrifft. Telegram muss unter Settings konfiguriert sein.
  </div>

  <!-- NEUER ALERT -->
  <div class="card" style="margin-bottom:14px;padding:16px">
    <div class="card-label" style="margin-bottom:10px">Neuen Alert erstellen</div>
    <div class="grid g2" style="gap:10px;margin-bottom:10px">
      <div>
        <div class="card-label" style="margin-bottom:4px">Typ</div>
        <select id="al-type" onchange="updateAlertForm()"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
          <option value="price_above" data-i18n="price_above">Preis UEBER Schwelle</option>
          <option value="price_below" data-i18n="price_below">Preis UNTER Schwelle</option>
          <option value="pnl_below"   data-i18n="pnl_below">Gesamt-PnL unter Wert</option>
          <option value="funding_above" data-i18n="funding_above">Funding Rate ueber Schwelle</option>
        </select>
      </div>
      <div id="al-sym-wrap">
        <div class="card-label" style="margin-bottom:4px">Coin</div>
        <select id="al-symbol" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
          <option>BTC</option><option>ETH</option><option>SOL</option>
          <option>XRP</option><option>DOGE</option>
        </select>
      </div>
    </div>
    <div class="grid g2" style="gap:10px;margin-bottom:10px">
      <div>
        <div class="card-label" style="margin-bottom:4px" data-i18n="al_value">Wert / Schwelle</div>
        <input type="number" id="al-value" placeholder="z.B. 100000"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
      </div>
      <div>
        <div class="card-label" style="margin-bottom:4px">Name (optional)</div>
        <input type="text" id="al-name" placeholder="z.B. BTC Moon Alert"
          style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%">
      </div>
    </div>
    <button onclick="addAlert()" class="btn btn-start" style="--accent:var(--signal);padding:8px 20px">
      ALERT HINZUFUEGEN
    </button>
  </div>

  <!-- AKTIVE ALERTS -->
  <div class="log-wrap">
    <div class="log-head"><span data-i18n="active_alerts">Aktive Alerts</span><span id="al-count">0</span></div>
    <div id="al-list" style="padding:8px 0"></div>
  </div>

  <!-- ALERT LOG -->
  <div class="log-wrap" style="margin-top:10px">
    <div class="log-head"><span data-i18n="last_triggers">Letzte Ausloeser</span><button onclick="loadAlertLog()" style="background:none;border:none;color:var(--muted);font-family:inherit;font-size:10px;cursor:pointer" data-i18n="refresh">Aktualisieren</button></div>
    <div class="log-body" id="al-log"></div>
  </div>
</div>
<div id="panel-settings" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;max-width:1500px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="set_head">EINSTELLUNGEN</span>
    <button class="btn-help" onclick="showHelp('settings')" title="Erklaerung">?</button>
  </div>
  <div class="settings-grid">
   <!-- ===== LINKE SPALTE: Modus / Presets / Zugang / globale Keys ===== -->
   <div class="settings-col">

    <!-- LIVE / DEMO TOGGLE -->
    <div class="settings-section" style="margin-bottom:10px">
      <div class="settings-head" onclick="toggle('s-mode')"><span data-i18n="set_mode_head">Handelsmodus</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-mode" class="settings-body open">
        <div class="toggle-wrap">
          <label class="toggle">
            <input type="checkbox" id="cfg-live" onchange="onLiveModeChange(this.checked)">
            <span class="toggle-slider"></span>
          </label>
          <div>
            <div id="mode-label" style="font-size:12px;font-weight:600;color:var(--grid)">DEMO-MODUS aktiv</div>
            <div style="font-size:10px;color:var(--muted);margin-top:2px" data-i18n="set_mode_hint">Demo = paptrading:1 (kein echtes Geld). Live = echte Orders auf Bitget.</div>
          </div>
        </div>
        <div style="font-size:10px;color:var(--dca);background:rgba(251,191,36,.06);border:1px solid rgba(251,191,36,.2);border-radius:5px;padding:8px;margin-top:6px" data-i18n="set_mode_warn">
          ⚠️ Nach dem Wechsel alle laufenden Bots neu starten, damit der neue Modus greift.
        </div>
      </div>
    </div>

    <!-- STRATEGIE-VORLAGEN -->
    <div class="settings-section" style="margin-bottom:10px">
      <div class="settings-head" onclick="toggle('s-presets')"><span data-i18n="set_presets_head">Strategie-Vorlagen (Presets)</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-presets" class="settings-body open">
        <div style="font-size:11px;color:var(--muted);margin-bottom:10px;line-height:1.6" data-i18n="set_presets_hint">
          Presets fuellen die Signal- und Grid-Bot-Felder automatisch aus. Danach noch API Keys eintragen.
        </div>
        <div class="preset-wrap">
          <button class="preset-btn low"   onclick="applyPreset('passiv')" data-i18n="bp_passiv">PASSIV</button>
          <button class="preset-btn low"   onclick="applyPreset('defensiv')" data-i18n="bp_defensiv">DEFENSIV</button>
          <button class="preset-btn med"   onclick="applyPreset('standard')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen" onclick="applyPreset('offensiv')" data-i18n="bp_offensiv">OFFENSIV</button>
          <button class="preset-btn degen" onclick="applyPreset('aggressiv')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div id="preset-desc" style="font-size:10px;color:var(--muted);min-height:16px"></div>
      </div>
    </div>

    <!-- DASHBOARD ZUGANG -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-auth')"><span data-i18n="set_auth_head">Dashboard-Zugang</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-auth" class="settings-body open">
        <div style="background:rgba(248,113,113,.07);border:1px solid rgba(248,113,113,.2);border-radius:5px;padding:9px 12px;margin-bottom:12px;font-size:11px;color:var(--red)" data-i18n="set_auth_hint">
          Beim ersten Start wurde ein zufaelliges Passwort generiert (siehe platform.log). Hier aendern und SPEICHERN nicht vergessen - danach fragt der Browser beim naechsten Laden neu nach Login.
        </div>
        <div class="field-row"><label data-i18n="lbl_user">Benutzername</label>
          <input type="text" id="cfg-dash-user" placeholder="admin"></div>
        <div class="field-row"><label data-i18n="lbl_pass">Passwort</label>
          <input type="text" id="cfg-dash-pass" data-i18n-ph="ph_pass_unchanged" placeholder="Leer lassen = unveraendert"></div>
      </div>
    </div>

    <!-- GLOBALE KEYS -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-global')"><span data-i18n="set_global_head">Globale API-Keys</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-global" class="settings-body open">
        <div style="background:rgba(0,214,143,.07);border:1px solid rgba(0,214,143,.2);border-radius:5px;padding:9px 12px;margin-bottom:12px;font-size:11px;color:var(--signal)" data-i18n="set_global_hint">
          Wichtig: Nach dem Eintragen immer unten auf SPEICHERN klicken, dann START druecken.
        </div>
        <div class="field-row"><label>Finnhub API Key</label>
          <input type="text" id="cfg-finnhub" data-i18n-ph="ph_finnhub" placeholder="Fuer Makro-Kalender (kostenlos)"></div>
        <div class="field-row"><label>Coinalyze API Key</label>
          <input type="text" id="cfg-coinalyze" data-i18n-ph="ph_coinalyze" placeholder="Fuer Derivate-Tab (kostenlos, coinalyze.net)"></div>
        <div class="field-row"><label>Telegram Bot Token</label>
          <input type="text" id="cfg-tg-token" placeholder="123456:ABC-DEF... von @BotFather"></div>
        <div class="field-row"><label>Telegram Chat ID</label>
          <input type="text" id="cfg-tg-chat" data-i18n-ph="ph_tg_chat" placeholder="Deine Chat-ID (z.B. 123456789)"></div>
        <div class="field-row"><label>Discord Webhook URL</label>
          <input type="text" id="cfg-discord-wh" placeholder="https://discord.com/api/webhooks/..."></div>
        <div class="settings-note" data-i18n="set_notify_note">
          Telegram: @BotFather → /newbot → Token. Chat-ID von @userinfobot.<br>
          Discord: Server-Einstellungen → Integrationen → Webhooks → URL kopieren.<br>
          Beide koennen gleichzeitig aktiv sein. News-Sentiment: CoinGecko (kostenlos, kein Key).
        </div>
      </div>
    </div>

   </div>
   <!-- ===== RECHTE SPALTE: die 4 Bots ===== -->
   <div class="settings-col">

    <!-- SIGNAL BOT -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-signal')" style="color:var(--signal)"><span>Signal Bot – Sub-Account API</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-signal" class="settings-body">
        <div class="preset-wrap" style="margin-bottom:12px">
          <span style="font-size:10px;color:var(--muted);margin-right:6px" data-i18n="set_preset">Preset:</span>
          <button class="preset-btn low"    onclick="applyBotPreset('signal','passiv')" data-i18n="bp_passiv">PASSIV</button>
          <button class="preset-btn low"    onclick="applyBotPreset('signal','defensiv')" data-i18n="bp_defensiv">DEFENSIV</button>
          <button class="preset-btn med"    onclick="applyBotPreset('signal','standard')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen"  onclick="applyBotPreset('signal','offensiv')" data-i18n="bp_offensiv">OFFENSIV</button>
          <button class="preset-btn degen"  onclick="applyBotPreset('signal','aggressiv')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="sig-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="sig-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="sig-pass" placeholder="Bitget Passphrase"></div>
        <div class="field-row"><label data-i18n="lbl_autostart">Auto-Start nach Neustart</label><input type="checkbox" id="sig-autostart" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_coins">Coins (kommagetrennt)</label><input type="text" id="sig-tokens" placeholder="SOLUSDT, ETHUSDT, DOGEUSDT"></div>
        <div class="settings-note" data-i18n="hint_coins">Welche Coins der Signal-Bot handelt. Kaputte Demo-Coins (z.B. XRP) einfach weglassen. Änderung greift beim nächsten Bot-Start.</div>
        <div class="field-row"><label>Leverage (1-10)</label><input type="number" id="sig-lever" placeholder="3" min="1" max="10"></div>
        <div class="field-row"><label data-i18n="lbl_risk_trade">Risiko pro Trade (%)</label><input type="number" id="sig-risk-pct" placeholder="3.0" step="0.5" min="0.5" max="10"></div>
        <div class="field-row"><label data-i18n="lbl_sl_mult">Stop-Loss Weite (ATR-Faktor)</label><input type="number" id="sig-sl-mult" placeholder="1.5" step="0.1" min="0.5" max="6"></div>
        <div class="settings-note" data-i18n="hint_sl_mult">Abstand des Einstiegs-Stops = ATR × Faktor. Größer = mehr Luft, weniger Whipsaw-Ausstopper im Rauschen (dafür größerer Verlust je Fehltrade). 1,5 = eng, 2,5–3 = geduldig.</div>
        <div class="field-row"><label data-i18n="lbl_usdt_trade">USDT pro Trade (fallback)</label><input type="number" id="sig-usdt" placeholder="30" min="5"></div>
        <div class="field-row"><label data-i18n="lbl_budget">Budget (USDT)</label><input type="number" id="sig-budget" placeholder="0 = kein Limit" min="0" title="Max. Margin, die dieser Bot binden darf. 0 = kein Limit (volle Balance)."></div>
        <div class="field-row"><label data-i18n="lbl_max_conc">Max. gleichzeitige Pos.</label><input type="number" id="sig-max-conc" placeholder="2" min="1" max="4"></div>
        <div class="field-row"><label data-i18n="lbl_corr_filter">Korrelations-Filter</label><input type="checkbox" id="sig-corr-filter" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_max_corr">Max. Korrelation (0.5-1.0)</label><input type="number" id="sig-max-corr" placeholder="0.85" step="0.05" min="0.5" max="1.0"></div>
        <div class="settings-note" data-i18n="note_corr">Korrelations-Filter: verhindert, dass der Bot eine neue Position eroeffnet, die zu stark mit einer bereits offenen, gleichgerichteten Position korreliert (Diversifikation). Bei fehlenden Daten wird normal weitergehandelt.</div>
        <div class="field-row"><label data-i18n="lbl_adx_filter">ADX-Trendfilter</label><input type="checkbox" id="sig-adx-filter" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_min_adx">Min. ADX (10-40)</label><input type="number" id="sig-min-adx" placeholder="20" step="1" min="10" max="40"></div>
        <div class="settings-note" data-i18n="note_adx">ADX-Trendfilter: daempft das Signal, wenn kein klarer Trend da ist (ADX unter Schwelle) – handelt weniger im Seitwaerts-Gezappel. Fail-open bei zu wenig Daten.</div>
        <div class="field-row"><label data-i18n="lbl_adx_gate">ADX-Hart-Filter (nur bei Trend)</label><input type="checkbox" id="sig-adx-gate" style="width:auto"></div>
        <div class="settings-note" data-i18n="note_adx_gate">Härter als der Dämpfer: unter Min. ADX wird gar nicht gehandelt (Signal → NEUTRAL). Vermeidet die teuren Fehlsignale im Seitwaerts-Markt. Empfohlen AN (Min. ADX z.B. 25).</div>
        <div class="field-row"><label data-i18n="lbl_ob">Order-Book-Kaufdruck</label><input type="checkbox" id="sig-ob-signal" style="width:auto"></div>
        <div class="settings-note" data-i18n="note_ob">Order-Book-Kaufdruck: bezieht den Kauf-/Verkaufsdruck aus dem Live-Orderbuch als zusaetzlichen Signal-Faktor mit ein. Fail-open, wenn keine Daten verfuegbar sind.</div>
        <div class="field-row" style="grid-template-columns:1fr;align-items:start">
          <label data-i18n="lbl_factors" style="margin-bottom:6px">Score-Faktoren (an/aus)</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:5px 14px;font-size:11px;color:var(--text)">
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-ema" style="width:auto"> EMA-Cross</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-rsi" style="width:auto"> RSI</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-macd" style="width:auto"> MACD</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-bb" style="width:auto"> Bollinger</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-volume" style="width:auto"> Volumen</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-funding" style="width:auto"> Funding</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-fg" style="width:auto"> Fear &amp; Greed</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-news" style="width:auto"> News</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-macro" style="width:auto"> Makro</label>
            <label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="sig-f-delta" style="width:auto"> Delta (Order-Flow)</label>
          </div>
        </div>
        <div class="settings-note" data-i18n="note_factors">Score-Faktoren: schalte einzelne Indikatoren an/aus. Alle bestehenden sind standardmaessig an. Die Beitraege jedes Faktors siehst du live im SIGNAL-Tab pro Coin.</div>
        <div class="field-row"><label data-i18n="lbl_trend">Trendfilter (lange EMA)</label><input type="checkbox" id="sig-f-trend" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_trend_len">Trend-EMA Laenge (20-200)</label><input type="number" id="sig-trend-len" placeholder="50" min="20" max="200"></div>
        <div class="settings-note" data-i18n="note_trend">Trendfilter (MT5-Stil): zusaetzlicher Faktor +1/-1, je nachdem ob der Preis ueber/unter einer langen EMA liegt. Standardmaessig AUS. Macht Signale selektiver (handelt eher mit dem uebergeordneten Trend).</div>
        <div class="field-row"><label data-i18n="lbl_sig_thresh">Signal-Schwelle (1-5)</label><input type="number" id="sig-thresh" placeholder="3" min="1" max="5"></div>
        <div class="field-row"><label data-i18n="lbl_daily_limit">Tages-Verlustlimit % (0 = aus)</label><input type="number" id="sig-daily-limit" placeholder="0" min="0" max="90" step="0.5"></div>
        <div class="settings-note" data-i18n="hint_daily_limit">Pausiert den Bot bis zum naechsten Tag (UTC), wenn der Tagesverlust diese % erreicht. 0 = aus (Bot laeuft durch). Fuer LIVE z.B. 5-10 empfohlen.</div>
        <div class="field-row"><label data-i18n="lbl_trend_gate">Harter Trend-Filter (kein Gegen-Trend)</label><input type="checkbox" id="sig-trend-gate" style="width:auto"></div>
        <div class="settings-note" data-i18n="hint_trend_gate">Ueber der langen EMA nur LONG, darunter nur SHORT. Verhindert, dass der Bot gegen den Trend handelt (z.B. eine Rallye shortet). Empfohlen AN.</div>
        <div class="field-row"><label data-i18n="lbl_htf_trend">Trend auf 1h-Zeitrahmen</label><input type="checkbox" id="sig-htf-trend" style="width:auto"></div>
        <div class="settings-note" data-i18n="hint_htf_trend">Berechnet die Trend-EMA auf 1-Stunden-Kerzen statt auf 1-Minuten-Rauschen. So spiegelt der Filter den ECHTEN Trend: kurze Dips drehen ihn nicht mehr, keine Gegen-Trend-Shorts in einer Rallye. Bei AN gilt die "Trend-EMA Laenge" in STUNDEN (z.B. 24 = 1-Tages-Trend, 50 = ~2 Tage). Empfohlen AN.</div>
        <div class="field-row"><label data-i18n="lbl_cooldown">Cooldown pro Coin (Min., 0 = aus)</label><input type="number" id="sig-cooldown" placeholder="20" min="0" max="240"></div>
        <div class="settings-note" data-i18n="hint_cooldown">Nach dem Schliessen einer Position ist derselbe Coin so lange gesperrt. Stoppt staendiges Rein/Raus (Anti-Churn).</div>
        <div class="field-row"><label data-i18n="lbl_trailing">Trailing-Stop</label><input type="checkbox" id="sig-trailing" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_trail_mult">Trailing-Abstand (ATR-Faktor)</label><input type="number" id="sig-trail-mult" placeholder="2.0" min="0.3" max="10" step="0.1"></div>
        <div class="settings-note" data-i18n="hint_trailing">Statt festem Take-Profit zieht der Stop mit dem Gewinn nach (Abstand = ATR × Faktor) und laesst Gewinner im Trend laufen. Kleiner = enger/sichert frueher, groesser = mehr Raum. Empfohlen AN.</div>
        <div class="validate-row">
          <button class="btn-validate" onclick="validateKey('signal')">Verbindung testen</button>
          <span class="val-result" id="val-signal"></span>
        </div>
      </div>
    </div>

    <!-- GRID BOT -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-grid')" style="color:var(--grid)"><span>Grid Bot – Sub-Account API</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-grid" class="settings-body">
        <div style="background:rgba(248,113,113,.08);border:1px solid rgba(248,113,113,.2);border-radius:5px;padding:9px 12px;margin-bottom:12px;font-size:10px;color:var(--red);line-height:1.7" data-i18n="grid_oneway_warn">
          <b>WICHTIG:</b> Bitget Sub-Account muss auf <b>One-Way Mode</b> stehen!<br>
          Bitget App: Futures-Handel -> Einstellungen -> Positionsmodus -> One-Way Mode.<br>
          Im Hedge-Modus oeffnet der Grid Bot ungewollt gegenlaeutige Positionen.
        </div>
        <div class="preset-wrap" style="margin-bottom:12px">
          <span style="font-size:10px;color:var(--muted);margin-right:6px" data-i18n="set_preset">Preset:</span>
          <button class="preset-btn low"   onclick="applyBotPreset('grid','passiv')" data-i18n="bp_passiv">PASSIV</button>
          <button class="preset-btn low"   onclick="applyBotPreset('grid','defensiv')" data-i18n="bp_defensiv">DEFENSIV</button>
          <button class="preset-btn med"   onclick="applyBotPreset('grid','standard')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen" onclick="applyBotPreset('grid','offensiv')" data-i18n="bp_offensiv">OFFENSIV</button>
          <button class="preset-btn degen" onclick="applyBotPreset('grid','aggressiv')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="grd-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="grd-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="grd-pass" placeholder="Bitget Passphrase"></div>
        <div class="field-row"><label data-i18n="lbl_autostart">Auto-Start nach Neustart</label><input type="checkbox" id="grd-autostart" style="width:auto"></div>
        <div class="field-row">
          <label>Symbol</label>
          <select id="grd-sym" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:8px 10px;border-radius:5px;width:100%">
            <option value="BTCUSDT">BTCUSDT – Bitcoin</option>
            <option value="ETHUSDT">ETHUSDT – Ethereum</option>
            <option value="SOLUSDT">SOLUSDT – Solana</option>
            <option value="XRPUSDT">XRPUSDT – XRP</option>
            <option value="DOGEUSDT">DOGEUSDT – Dogecoin</option>
            <option value="BNBUSDT">BNBUSDT – BNB</option>
            <option value="ADAUSDT">ADAUSDT – Cardano</option>
            <option value="AVAXUSDT">AVAXUSDT – Avalanche</option>
            <option value="LINKUSDT">LINKUSDT – Chainlink</option>
            <option value="DOTUSDT">DOTUSDT – Polkadot</option>
          </select>
        </div>
        <div class="field-row"><label data-i18n="lbl_price_up">Preis oben (0 = auto)</label><input type="number" id="grd-upper" placeholder="0" min="0"></div>
        <div class="field-row"><label data-i18n="lbl_price_low">Preis unten (0 = auto)</label><input type="number" id="grd-lower" placeholder="0" min="0"></div>
        <div class="field-row"><label data-i18n="lbl_step">Stufengroesse (USDT, 0 = aus)</label><input type="number" id="grd-step" placeholder="0" min="0" step="1"></div>
        <div style="font-size:10px;color:var(--muted);margin:-4px 0 8px 0" data-i18n="hint_step">Wenn > 0 und Preis oben/unten = 0: Range wird automatisch so gesetzt, dass jede Stufe so gross ist (z.B. 100 = 100-USDT-Schritte um den aktuellen Preis).</div>
        <div class="field-row"><label data-i18n="lbl_smart_hours">Smart-Range Rueckblick (h)</label><input type="number" id="grd-srhours" placeholder="24" min="6" max="168"></div>
        <div style="font-size:10px;color:var(--muted);margin:-4px 0 8px 0" data-i18n="hint_smart">Wenn Preis oben/unten = 0 UND Stufengroesse = 0: Der Grid legt die Range beim Start aus dem echten Hoch/Tief der letzten N Stunden fest (statt stumpf +-5%).</div>
        <div class="field-row"><label data-i18n="lbl_levels">Anzahl Levels</label><input type="number" id="grd-n" placeholder="10" min="2" max="50"></div>
        <div class="field-row"><label>Budget (USDT)</label><input type="number" id="grd-inv" placeholder="100" min="10"></div>
        <div class="field-row"><label data-i18n="lbl_grd_lev">Hebel (0 = Konto-Hebel lassen)</label><input type="number" id="grd-lev" placeholder="0" min="0" max="125"></div>
        <div class="field-row"><label data-i18n="lbl_grd_sl">Stop-Loss % unter Untergrenze (0 = aus)</label><input type="number" id="grd-sl" placeholder="0" min="0" max="90" step="0.5"></div>
        <div style="font-size:10px;color:var(--muted);margin:-4px 0 8px 0" data-i18n="hint_grd_sl">Sicherheitsnetz: Faellt der Preis diese % unter die Untergrenze, schliesst der Grid den Bestand und stoppt (statt ins Bodenlose zu halten). Fuer LIVE empfohlen.</div>
        <div class="validate-row">
          <button class="btn-validate" onclick="validateKey('grid')">Verbindung testen</button>
          <span class="val-result" id="val-grid"></span>
        </div>
      </div>
    </div>

    <!-- DCA BOT -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-dca')" style="color:var(--dca)"><span>DCA Bot – Sub-Account API</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-dca" class="settings-body">
        <div class="preset-wrap" style="margin-bottom:12px">
          <span style="font-size:10px;color:var(--muted);margin-right:6px" data-i18n="set_preset">Preset:</span>
          <button class="preset-btn low"   onclick="applyBotPreset('dca','passiv')" data-i18n="bp_passiv">PASSIV</button>
          <button class="preset-btn low"   onclick="applyBotPreset('dca','defensiv')" data-i18n="bp_defensiv">DEFENSIV</button>
          <button class="preset-btn med"   onclick="applyBotPreset('dca','standard')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen" onclick="applyBotPreset('dca','offensiv')" data-i18n="bp_offensiv">OFFENSIV</button>
          <button class="preset-btn degen" onclick="applyBotPreset('dca','aggressiv')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div style="background:rgba(0,214,143,.06);border:1px solid rgba(0,214,143,.15);border-radius:5px;padding:8px 12px;margin-bottom:12px;font-size:10px;color:var(--signal)" data-i18n="dca_spot_note">
          DCA kauft immer auf dem Spot-Markt (kein Hebel, keine Funding-Kosten). Das Guthaben muss auf dem Spot-Konto des Sub-Accounts liegen.
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="dca-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="dca-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="dca-pass" placeholder="Bitget Passphrase"></div>
        <div class="field-row"><label data-i18n="lbl_autostart">Auto-Start nach Neustart</label><input type="checkbox" id="dca-autostart" style="width:auto"></div>
        <div class="field-row">
          <label>Symbol (Spot)</label>
          <select id="dca-sym" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:8px 10px;border-radius:5px;width:100%">
            <option value="BTCUSDT">BTCUSDT – Bitcoin</option>
            <option value="ETHUSDT">ETHUSDT – Ethereum</option>
            <option value="SOLUSDT">SOLUSDT – Solana</option>
            <option value="XRPUSDT">XRPUSDT – XRP</option>
            <option value="DOGEUSDT">DOGEUSDT – Dogecoin</option>
            <option value="BNBUSDT">BNBUSDT – BNB</option>
            <option value="ADAUSDT">ADAUSDT – Cardano</option>
          </select>
        </div>
        <div class="field-row"><label data-i18n="lbl_interval">Interval (Stunden)</label><input type="number" id="dca-hrs" placeholder="24" min="1"></div>
        <div class="field-row"><label data-i18n="lbl_amount_buy">Betrag pro Kauf (USDT)</label><input type="number" id="dca-amt" placeholder="20" min="5"></div>
        <div class="validate-row">
          <button class="btn-validate" onclick="validateKey('dca')">Verbindung testen</button>
          <span class="val-result" id="val-dca"></span>
        </div>
        <div class="settings-note" data-i18n="dca_conn_note">
          Verbindungstest zeigt Spot-Balance (genutztes Kapital) und Futures-Balance getrennt.<br>
          Tipp: Fuer DCA nur Spot-Guthaben aufbuchen, Futures-Konto leer lassen.
        </div>
      </div>
    </div>

    <div class="save-row">
      <button class="btn btn-save save-btn" onclick="saveSettings()" data-i18n="settings_save">EINSTELLUNGEN SPEICHERN</button>
      <span class="save-msg" id="save-msg" data-i18n="saved_msg">Gespeichert.</span>
    </div>

   </div>
  </div>
</div>

<!-- SYSTEM-LOG -->
<div id="panel-syslog" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:10px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="slog_title">SYSTEM-LOG (platform.log)</span>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <label style="font-size:10px;color:var(--muted);display:flex;align-items:center;gap:5px;cursor:pointer">
        <input type="checkbox" id="slog-auto" style="width:auto" onchange="toggleSyslogAuto()"><span data-i18n="slog_auto">Auto-Refresh (5s)</span></label>
      <select id="slog-lines" onchange="loadSyslog()" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:10px;padding:4px 8px;border-radius:4px">
        <option value="200">200</option><option value="400" selected>400</option><option value="1000">1000</option><option value="3000">3000</option>
      </select>
      <button onclick="loadSyslog()" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer" data-i18n="slog_refresh">Aktualisieren</button>
      <a href="/api/syslog/download" download style="background:var(--signal);color:#04140d;font-family:inherit;font-size:10px;font-weight:700;padding:5px 12px;border-radius:4px;cursor:pointer;text-decoration:none" data-i18n="slog_download">Herunterladen</a>
    </div>
  </div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:10px" data-i18n="slog_hint">Vollstaendiges Log der Plattform auf dem Pi. Zum Teilen unten herunterladen.</div>
  <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--dim);margin-bottom:4px">
    <span id="slog-meta"></span><span id="slog-updated"></span>
  </div>
  <pre id="slog-body" style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px;margin:0;
       font-family:'SF Mono',Consolas,monospace;font-size:11px;line-height:1.55;color:var(--text);
       max-height:62vh;overflow:auto;white-space:pre;word-break:normal"><span style="color:var(--muted)" data-i18n="slog_empty">Log wird geladen...</span></pre>
</div>

<!-- HELP MODAL -->
<div class="modal-overlay" id="help-modal" onclick="if(event.target===this)closeHelp()">
  <div class="modal">
    <div class="modal-header">
      <div>
        <div class="modal-title" id="help-title"></div>
        <div class="modal-sub" id="help-sub"></div>
      </div>
      <button class="modal-x" onclick="closeHelp()">✕</button>
    </div>
    <div id="help-body"></div>
    <button class="modal-close" onclick="closeHelp()">SCHLIESSEN</button>
  </div>
</div>

<script>
const BOT_COLORS = {signal:'#00d68f',grid:'#4da6ff',dca:'#fbbf24'};
const BOT_NAMES  = {signal:'Signal Bot',grid:'Grid Bot',dca:'DCA Bot'};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// -- SPRACHE / LANGUAGE ---------------------------------------
let _lang = (typeof localStorage !== 'undefined' && localStorage.getItem('tp_lang')) || 'en';

const STRINGS = {
  de: {
    // Nav
    nav_overview:'OVERVIEW', nav_signal:'SIGNAL', nav_grid:'GRID',
    nav_dca:'DCA', nav_markt:'MARKT',
    nav_trades:'TRADES',
    nav_backtest:'BACKTEST', nav_correlation:'KORRELATION', nav_derivate:'DERIVATE', nav_alerts:'ALERTS', nav_settings:'SETTINGS',
    nav_syslog:'SYSTEM-LOG',
    // Signal-Konfig-Uebersicht
    cfg_configured:'Aktuelle Konfiguration', cfg_edit:'bearbeiten',
    cfg_tokens:'Coins', cfg_leverage:'Hebel', cfg_budget:'Budget', cfg_stake:'Einsatz/Trade',
    cfg_sltp:'SL / TP', cfg_maxconc:'Max. gleichzeitig', cfg_threshold:'Signal-Schwelle',
    cfg_interval:'Pruef-Intervall', cfg_full_bal:'volle Balance', cfg_fixed:'fix', cfg_score_factors:'Score-Faktoren',
    cfg_range:'Range', cfg_step:'Stufe', cfg_levels:'Levels', cfg_smartrange:'Smart-Range', cfg_account:'Konto-Hebel', cfg_off:'aus',
    lbl_step:'Stufengroesse (USDT, 0 = aus)',
    hint_step:'Wenn > 0 und Preis oben/unten = 0: Range wird automatisch so gesetzt, dass jede Stufe so gross ist (z.B. 100 = 100-USDT-Schritte um den aktuellen Preis).',
    lbl_smart_hours:'Smart-Range Rueckblick (h)',
    hint_smart:'Wenn Preis oben/unten = 0 UND Stufengroesse = 0: Der Grid legt die Range beim Start aus dem echten Hoch/Tief der letzten N Stunden fest (statt stumpf +-5%).',
    lbl_grd_lev:'Hebel (0 = Konto-Hebel lassen)',
    lbl_grd_sl:'Stop-Loss % unter Untergrenze (0 = aus)',
    hint_grd_sl:'Sicherheitsnetz: Faellt der Preis diese % unter die Untergrenze, schliesst der Grid den Bestand und stoppt (statt ins Bodenlose zu halten). Fuer LIVE empfohlen.',
    // System-Log
    slog_title:'SYSTEM-LOG (platform.log)', slog_refresh:'Aktualisieren', slog_download:'Herunterladen',
    slog_auto:'Auto-Refresh (5s)', slog_lines:'Zeilen', slog_hint:'Vollstaendiges Log der Plattform auf dem Pi (aktuelle Datei + rotierte Vorgaenger-Datei). Zum Teilen unten herunterladen.',
    slog_empty:'Log wird geladen...',
    // Korrelation
    corr_title:'KORRELATIONS-MATRIX', corr_period:'Zeitraum', corr_refresh:'Aktualisieren',
    corr_legend:'Legende:',
    corr_hint:'Korrelation der Tagesrenditen deiner Signal-Bot-Coins. Hohe positive Werte (rot) = die Coins bewegen sich gemeinsam → gleichzeitige Positionen erhoehen dein Risiko. Niedrige/negative Werte (gruen) = bessere Diversifikation.',
    // Derivate + Markt-Regime
    reg_title:'MARKT-REGIME (COINGECKO)', reg_refresh:'Aktualisieren',
    reg_btc_dom:'BTC-Dominanz', reg_eth_dom:'ETH-Dominanz', reg_mcap:'Market Cap 24h', reg_trending:'Trending',
    reg_hint:'Hohe/steigende BTC-Dominanz = Kapital fliesst in BTC, Alts schwaecheln oft. Nutze das als groben Regime-Filter.',
    deriv_title:'DERIVATE-DATEN (COINALYZE)',
    deriv_hint:'Aggregierte Futures-Daten (Binance-Perps): Open Interest, Funding Rate, Long/Short-Verhaeltnis und Liquidationen der letzten 24h. Braucht einen kostenlosen Coinalyze-API-Key (Settings → Globale API-Keys).',
    deriv_coin:'Coin', deriv_oi:'Open Interest', deriv_funding:'Funding', deriv_ls:'Long/Short', deriv_liq:'Liq. 24h (L/S)',
    ob_title:'ORDER-BOOK-DRUCK (BITGET)',
    ob_hint:'Kauf-/Verkaufsdruck aus dem Live-Orderbuch: Verhaeltnis von Kauf- zu Verkaufsvolumen im Bereich ±1 % um den Preis. >1 (gruen) = Kaufdruck ueberwiegt, <1 (rot) = Verkaufsdruck. Kurzfristig und kann durch Fake-Walls verzerrt sein.',
    ob_pressure:'Druck (Bid/Ask)', ob_bidvol:'Kaufvol.', ob_askvol:'Verkaufsvol.', ob_spread:'Spread',
    // Status
    running:'RUNNING', stopped:'STOPPED', starting:'STARTING',
    paused:'PAUSIERT', stopping:'STOPPING',
    // Buttons
    start:'START', stop:'STOP', save:'EINSTELLUNGEN SPEICHERN',
    test_conn:'Verbindung testen', load:'Laden', refresh:'Aktualisieren',
    panic:'ALL STOP & CLOSE',
    // Card labels
    balance:'Balance', total_balance:'Gesamt Balance',
    total_pnl:'Gesamt PnL', active_bots:'Aktive Bots',
    total_pnl_nofund:'Gesamt PnL', running_total:'Laufen / Gesamt',
    all_bots:'Alle Bots', th_action:'Aktion', no_finnhub:'Kein Finnhub Key gesetzt',
    pos_side:'Seite', pos_size:'Groesse', pos_entry:'Einstieg', pos_lev:'Hebel',
    pnl_history:'PnL-Verlauf', no_blackout:'Kein Blackout', grid_profits:'Grid-Gewinne',
    th_price:'Preis', th_high24:'24h Hoch', th_low24:'24h Tief', th_vol_m:'Volumen (Mio $)',
    loading:'Lade...', loading_market:'Lade Marktdaten...', click_load:'Auf "Laden" klicken.',
    th_time:'Zeit', th_qty:'Menge', th_fee:'Gebuehr',
    kal_time:'Zeit (UTC)', kal_country:'Land', kal_event:'Ereignis', kal_actual:'Aktuell', kal_forecast:'Prognose',
    loading_cal:'Lade Kalender... (Finnhub API Key in Settings benoetigt)',
    over_thresh:'Ueber Schwelle', strategy:'Strategie',
    bt_fees_total:'Gebuehren gesamt', bt_entry:'Einstieg', bt_exit:'Ausstieg', bt_res:'Erg.', bt_final:'Endkapital', bt_fees:'Gebuehren',
    alert_value:'Wert / Schwelle', active_alerts:'Aktive Alerts', last_triggers:'Letzte Ausloeser',
    executed:'Ausgefuehrt', cur_streak:'aktuell', usdt_funding:'USDT Funding',
    filled_levels:'Gefuellte Level', usdt_total:'USDT gesamt', funding_cum:'Kumulierter Funding-Ertrag',
    econ_cal:'WIRTSCHAFTSKALENDER', reload:'Neu laden', filter_all:'ALLE',
    us_blackout:'US BLACKOUT AKTIV - Signal Bot oeffnet keine neuen Positionen',
    meta_signal:'RSI · EMA · MACD · Funding · Makro | 3x Hebel',
    meta_grid:'Automatische Kauf/Verkauf-Level im Preis-Raster',
    meta_funding:'Beobachtungs-Modus: zeigt Funding-Rate-Opportunities, platziert aber KEINE echten Orders. "Verdient" ist eine Schaetzung.',
    meta_dca:'Zeitbasiertes Kaufen mit Durchschnittskosteneffekt',
    total_trades:'Trades Gesamt', pnl:'PnL', trades:'Trades',
    macro:'Makro', invested:'Investiert', next_buy:'Naechster Kauf',
    earned:'Verdient (est.)', opportunities:'Opportunities',
    // Sections
    overview:'OVERVIEW', signal_log:'Signal Bot Log',
    grid_log:'Grid Bot Log', funding_log:'Funding Bot Log',
    dca_log:'DCA Bot Log', last_activity:'Letzte Aktivitaet',
    macro_events:'Makro-Ereignisse (48h)',
    positions:'Offene Positionen (alle Bots)',
    fg_chart:'Fear & Greed Index - 30 Tage',
    // Backtest
    bt_start:'BACKTEST STARTEN', bt_symbol:'Symbol',
    bt_period:'Zeitraum', bt_lever:'Hebel', bt_thresh:'Signal-Schwelle (1-3)', bt_pos:'Positionsgroesse %',
    bt_sl:'Stop Loss %', bt_tp:'Take Profit %',
    bt_wf:'Walk-Forward (70/30 Train/Test Split)',
    bt_compare:'ALLE SYMBOLE VERGLEICHEN',
    bt_trades:'Trades gesamt', bt_winrate:'Win Rate',
    bt_totalpnl:'PnL gesamt', bt_drawdown:'Max Drawdown',
    bt_sharpe:'Sharpe Ratio', bt_fees:'Gebuehren gesamt',
    bt_equity:'Equity-Kurve (Startwert: 1000 USDT)',
    bt_endcap:'Endkapital',
    // Alerts
    al_title:'ALERTS & BENACHRICHTIGUNGEN',
    al_new:'Neuen Alert erstellen', al_type:'Typ', al_coin:'Coin',
    al_value:'Wert / Schwelle', al_name:'Name (optional)',
    al_add:'ALERT HINZUFUEGEN', al_active:'Aktive Alerts',
    al_log:'Letzte Ausloeser', al_triggered:'AUSGELOEST',
    al_active_s:'AKTIV', al_disabled:'DEAKTIVIERT',
    price_above:'Preis UEBER Schwelle', price_below:'Preis UNTER Schwelle',
    pnl_below:'Gesamt-PnL unter Wert', funding_above:'Funding Rate ueber Schwelle',
    // Settings
    settings_save:'EINSTELLUNGEN SPEICHERN',
    settings_note:'Wichtig: Nach dem Eintragen immer unten auf SPEICHERN klicken.',
    mode_demo:'DEMO-MODUS', mode_live:'LIVE-MODUS',
    saved_msg:'Gespeichert.',
    confirm_live:'LIVE-MODUS AKTIVIEREN? Echte Orders mit echtem Geld. Alle laufenden Bots danach neu starten!',
    mode_demo_active:'DEMO-MODUS aktiv', mode_live_active:'LIVE-MODUS aktiv',
    set_head:'EINSTELLUNGEN',
    set_mode_head:'Handelsmodus',
    set_mode_hint:'Demo = paptrading:1 (kein echtes Geld). Live = echte Orders auf Bitget.',
    set_mode_warn:'⚠️ Nach dem Wechsel alle laufenden Bots neu starten, damit der neue Modus greift.',
    set_presets_head:'Strategie-Vorlagen (Presets)',
    set_presets_hint:'Presets fuellen die Signal- und Grid-Bot-Felder automatisch aus. Danach noch API Keys eintragen.',
    set_auth_head:'Dashboard-Zugang',
    set_auth_hint:'Benutzername und Passwort legst du beim ersten Start SELBST fest (Abfrage im Terminal bzw. Setup-Assistent im Browser). Hier kannst du sie jederzeit aendern - dann SPEICHERN, danach fragt der Browser beim naechsten Laden neu nach Login.',
    lbl_user:'Benutzername', lbl_pass:'Passwort', ph_pass_unchanged:'Leer lassen = unveraendert',
    set_global_head:'Globale API-Keys',
    set_global_hint:'Wichtig: Nach dem Eintragen immer unten auf SPEICHERN klicken, dann START druecken.',
    ph_finnhub:'Fuer Makro-Kalender (kostenlos)', ph_coinalyze:'Fuer Derivate-Tab (kostenlos, coinalyze.net)',
    ph_tg_chat:'Deine Chat-ID (z.B. 123456789)',
    set_notify_note:'Telegram: @BotFather → /newbot → Token. Chat-ID von @userinfobot.<br>Discord: Server-Einstellungen → Integrationen → Webhooks → URL kopieren.<br>Beide koennen gleichzeitig aktiv sein. News-Sentiment: CoinGecko (kostenlos, kein Key).',
    set_preset:'Preset:', bp_cons:'KONSERVATIV', bp_std:'STANDARD', bp_agg:'AGGRESSIV',
    bp_passiv:'PASSIV', bp_defensiv:'DEFENSIV', bp_offensiv:'OFFENSIV',
    preset_low:'🟢 GERINGES RISIKO', preset_med:'🔵 MITTLERES RISIKO', preset_high:'🔴 HOHES RISIKO',
    preset_desc_passiv:'PASSIV: Hebel 1x, nur sehr starke Signale (Schwelle 5), kleines Grid. Wenige, vorsichtige Trades.',
    preset_desc_defensiv:'DEFENSIV: Hebel 2x, Schwelle 4, Grid 8 Levels. Vorsichtig.',
    preset_desc_standard:'STANDARD: Hebel 3x, Schwelle 3, Grid 12 Levels. Ausgewogen.',
    preset_desc_offensiv:'OFFENSIV: Hebel 5x, Schwelle 2, Trend-Faktor an, Grid 20 Levels. Mehr Trades.',
    preset_desc_aggressiv:'AGGRESSIV: Hebel 8x, Schwelle 1, Trend-Faktor an, Grid 30 Levels. Viele Trades, mehr Risiko.',
    lbl_risk_trade:'Risiko pro Trade (%)', lbl_usdt_trade:'USDT pro Trade (fallback)', lbl_budget:'Budget (USDT)',
    lbl_autostart:'Auto-Start nach Neustart',
    lbl_max_conc:'Max. gleichzeitige Pos.', lbl_corr_filter:'Korrelations-Filter', lbl_max_corr:'Max. Korrelation (0.5-1.0)',
    note_corr:'Korrelations-Filter: verhindert, dass der Bot eine neue Position eroeffnet, die zu stark mit einer bereits offenen, gleichgerichteten Position korreliert (Diversifikation). Bei fehlenden Daten wird normal weitergehandelt.',
    lbl_adx_filter:'ADX-Trendfilter', lbl_min_adx:'Min. ADX (10-40)',
    note_adx:'ADX-Trendfilter: daempft das Signal, wenn kein klarer Trend da ist (ADX unter Schwelle) – handelt weniger im Seitwaerts-Gezappel. Fail-open bei zu wenig Daten.',
    lbl_adx_gate:'ADX-Hart-Filter (nur bei Trend)',
    note_adx_gate:'Härter als der Dämpfer: unter Min. ADX wird gar nicht gehandelt (Signal → NEUTRAL). Vermeidet teure Fehlsignale im Seitwaerts-Markt. Empfohlen AN (Min. ADX z.B. 25).',
    lbl_sl_mult:'Stop-Loss Weite (ATR-Faktor)',
    hint_sl_mult:'Abstand des Einstiegs-Stops = ATR × Faktor. Größer = mehr Luft, weniger Whipsaw (dafür größerer Verlust je Fehltrade). 1,5 = eng, 2,5–3 = geduldig.',
    lbl_coins:'Coins (kommagetrennt)',
    hint_coins:'Welche Coins der Signal-Bot handelt. Kaputte Demo-Coins (z.B. XRP) einfach weglassen. Änderung greift beim nächsten Bot-Start.',
    lbl_ob:'Order-Book-Kaufdruck',
    note_ob:'Order-Book-Kaufdruck: bezieht den Kauf-/Verkaufsdruck aus dem Live-Orderbuch als zusaetzlichen Signal-Faktor mit ein. Fail-open, wenn keine Daten verfuegbar sind.',
    lbl_factors:'Score-Faktoren (an/aus)',
    note_factors:'Score-Faktoren: schalte einzelne Indikatoren an/aus. Alle bestehenden sind standardmaessig an. Die Beitraege jedes Faktors siehst du live im SIGNAL-Tab pro Coin.',
    lbl_trend:'Trendfilter (lange EMA)', lbl_trend_len:'Trend-EMA Laenge (20-200)',
    note_trend:'Trendfilter (MT5-Stil): zusaetzlicher Faktor +1/-1, je nachdem ob der Preis ueber/unter einer langen EMA liegt. Standardmaessig AUS. Macht Signale selektiver (handelt eher mit dem uebergeordneten Trend).',
    lbl_sig_thresh:'Signal-Schwelle (1-5)',
    lbl_daily_limit:'Tages-Verlustlimit % (0 = aus)',
    hint_daily_limit:'Pausiert den Bot bis zum naechsten Tag (UTC), wenn der Tagesverlust diese % erreicht. 0 = aus (Bot laeuft durch). Fuer LIVE z.B. 5-10 empfohlen.',
    lbl_trend_gate:'Harter Trend-Filter (kein Gegen-Trend)',
    hint_trend_gate:'Ueber der langen EMA nur LONG, darunter nur SHORT. Verhindert, dass der Bot gegen den Trend handelt (z.B. eine Rallye shortet). Empfohlen AN.',
    lbl_htf_trend:'Trend auf 1h-Zeitrahmen', hint_htf_trend:'Berechnet die Trend-EMA auf 1-Stunden-Kerzen statt auf 1-Minuten-Rauschen. So spiegelt der Filter den ECHTEN Trend: kurze Dips drehen ihn nicht mehr, keine Gegen-Trend-Shorts in einer Rallye. Bei AN gilt die "Trend-EMA Laenge" in STUNDEN (z.B. 24 = 1-Tages-Trend, 50 = ~2 Tage). Empfohlen AN.',
    lbl_cooldown:'Cooldown pro Coin (Min., 0 = aus)',
    hint_cooldown:'Nach dem Schliessen einer Position ist derselbe Coin so lange gesperrt. Stoppt staendiges Rein/Raus (Anti-Churn).',
    lbl_trailing:'Trailing-Stop', lbl_trail_mult:'Trailing-Abstand (ATR-Faktor)',
    hint_trailing:'Statt festem Take-Profit zieht der Stop mit dem Gewinn nach (Abstand = ATR × Faktor) und laesst Gewinner im Trend laufen. Kleiner = enger/sichert frueher, groesser = mehr Raum. Empfohlen AN.',
    grid_oneway_warn:'<b>WICHTIG:</b> Bitget Sub-Account muss auf <b>One-Way Mode</b> stehen!<br>Bitget App: Futures-Handel -> Einstellungen -> Positionsmodus -> One-Way Mode.<br>Im Hedge-Modus oeffnet der Grid Bot ungewollt gegenlaeutige Positionen.',
    lbl_price_up:'Preis oben (0 = auto)', lbl_price_low:'Preis unten (0 = auto)', lbl_levels:'Anzahl Levels',
    lbl_min_funding:'Min. Funding Rate (%)', lbl_max_pos:'Max. Position (USDT)',
    dca_spot_note:'DCA kauft immer auf dem Spot-Markt (kein Hebel, keine Funding-Kosten). Das Guthaben muss auf dem Spot-Konto des Sub-Accounts liegen.',
    lbl_interval:'Interval (Stunden)', lbl_amount_buy:'Betrag pro Kauf (USDT)',
    dca_conn_note:'Verbindungstest zeigt Spot-Balance (genutztes Kapital) und Futures-Balance getrennt.<br>Tipp: Fuer DCA nur Spot-Guthaben aufbuchen, Futures-Konto leer lassen.',
    // Market
    markt_title:'MARKT-UEBERSICHT', symbol:'Symbol', price:'Preis',
    change24:'24h %', high24:'24h Hoch', low24:'24h Tief',
    funding:'Funding', volume:'Volumen (Mio $)',
    // Trades
    trades_title:'TRADE-HISTORIE', time:'Zeit', bot:'Bot',
    side:'Seite', amount:'Menge', fee:'Gebuehr',
    entry:'Einstieg', exit:'Ausstieg', result:'Erg.',
    timing:'TRADE-TIMING-ANALYSE',
    // Grid
    grid_add:'+ GRID HINZUFUEGEN', grid_name:'Name',
    grid_levels:'Grid Levels', grid_invest:'Investment (USDT)',
    // Help
    help_what:'Was ist das?', help_close:'Schliessen',
    // General
    no_data:'Keine Daten', loading:'Lade...', error:'Fehler',
    circuit_active:'CIRCUIT BREAKER AKTIV',
    win_streak:'Win/Loss Streak', current:'aktuell',
    wins_in_row:'Gewinne in Folge', losses_in_row:'Verluste in Folge',
  },
  en: {
    nav_overview:'OVERVIEW', nav_signal:'SIGNAL', nav_grid:'GRID',
    nav_dca:'DCA', nav_markt:'MARKET',
    nav_trades:'TRADES',
    nav_backtest:'BACKTEST', nav_correlation:'CORRELATION', nav_derivate:'DERIVATIVES', nav_alerts:'ALERTS', nav_settings:'SETTINGS',
    nav_syslog:'SYSTEM LOG',
    cfg_configured:'Current configuration', cfg_edit:'edit',
    cfg_tokens:'Coins', cfg_leverage:'Leverage', cfg_budget:'Budget', cfg_stake:'Stake/trade',
    cfg_sltp:'SL / TP', cfg_maxconc:'Max concurrent', cfg_threshold:'Signal threshold',
    cfg_interval:'Check interval', cfg_full_bal:'full balance', cfg_fixed:'fixed', cfg_score_factors:'Score factors',
    cfg_range:'Range', cfg_step:'Step', cfg_levels:'Levels', cfg_smartrange:'Smart-Range', cfg_account:'account lev', cfg_off:'off',
    lbl_step:'Step size (USDT, 0 = off)',
    hint_step:'If > 0 and upper/lower price = 0: the range is set automatically so each step is this size (e.g. 100 = 100-USDT steps around the current price).',
    lbl_smart_hours:'Smart-Range lookback (h)',
    hint_smart:'If upper/lower price = 0 AND step size = 0: the grid sets its range at start from the real high/low of the last N hours (instead of a blunt +-5%).',
    lbl_grd_lev:'Leverage (0 = keep account leverage)',
    lbl_grd_sl:'Stop-loss % below lower bound (0 = off)',
    hint_grd_sl:'Safety net: if price falls this % below the lower bound, the grid closes its inventory and stops (instead of holding into a crash). Recommended for LIVE.',
    slog_title:'SYSTEM LOG (platform.log)', slog_refresh:'Refresh', slog_download:'Download',
    slog_auto:'Auto-refresh (5s)', slog_lines:'lines', slog_hint:'Full platform log on the Pi (current file + rotated previous file). Download below to share.',
    slog_empty:'Loading log...',
    corr_title:'CORRELATION MATRIX', corr_period:'Period', corr_refresh:'Refresh',
    corr_legend:'Legend:',
    corr_hint:'Correlation of daily returns across your Signal Bot coins. High positive values (red) = coins move together → simultaneous positions increase your risk. Low/negative values (green) = better diversification.',
    reg_title:'MARKET REGIME (COINGECKO)', reg_refresh:'Refresh',
    reg_btc_dom:'BTC dominance', reg_eth_dom:'ETH dominance', reg_mcap:'Market cap 24h', reg_trending:'Trending',
    reg_hint:'High/rising BTC dominance = capital flowing into BTC, alts often weaken. Use it as a rough regime filter.',
    deriv_title:'DERIVATIVES DATA (COINALYZE)',
    deriv_hint:'Aggregated futures data (Binance perps): open interest, funding rate, long/short ratio and liquidations over the last 24h. Requires a free Coinalyze API key (Settings → Global API keys).',
    deriv_coin:'Coin', deriv_oi:'Open Interest', deriv_funding:'Funding', deriv_ls:'Long/Short', deriv_liq:'Liq. 24h (L/S)',
    ob_title:'ORDER-BOOK PRESSURE (BITGET)',
    ob_hint:'Buy/sell pressure from the live order book: ratio of buy to sell volume within ±1% of price. >1 (green) = buy pressure dominates, <1 (red) = sell pressure. Short-term and can be distorted by fake walls.',
    ob_pressure:'Pressure (Bid/Ask)', ob_bidvol:'Buy vol.', ob_askvol:'Sell vol.', ob_spread:'Spread',
    running:'RUNNING', stopped:'STOPPED', starting:'STARTING',
    paused:'PAUSED', stopping:'STOPPING',
    start:'START', stop:'STOP', save:'SAVE SETTINGS',
    test_conn:'Test Connection', load:'Load', refresh:'Refresh',
    panic:'ALL STOP & CLOSE',
    balance:'Balance', total_balance:'Total Balance',
    total_pnl:'Total PnL', active_bots:'Active Bots',
    total_pnl_nofund:'Total PnL', running_total:'Running / Total',
    all_bots:'All bots', th_action:'Action', no_finnhub:'No Finnhub key set',
    pos_side:'Side', pos_size:'Size', pos_entry:'Entry', pos_lev:'Leverage',
    pnl_history:'PnL history', no_blackout:'No blackout', grid_profits:'Grid profits',
    th_price:'Price', th_high24:'24h High', th_low24:'24h Low', th_vol_m:'Volume (M$)',
    loading:'Loading...', loading_market:'Loading market data...', click_load:'Click "Load".',
    th_time:'Time', th_qty:'Size', th_fee:'Fee',
    kal_time:'Time (UTC)', kal_country:'Country', kal_event:'Event', kal_actual:'Actual', kal_forecast:'Forecast',
    loading_cal:'Loading calendar... (Finnhub API key required in Settings)',
    over_thresh:'Above threshold', strategy:'Strategy',
    bt_fees_total:'Total fees', bt_entry:'Entry', bt_exit:'Exit', bt_res:'Res.', bt_final:'Final equity', bt_fees:'Fees',
    alert_value:'Value / threshold', active_alerts:'Active alerts', last_triggers:'Last triggers',
    executed:'Executed', cur_streak:'current', usdt_funding:'USDT Funding',
    filled_levels:'Filled levels', usdt_total:'USDT total', funding_cum:'Cumulative funding earned',
    econ_cal:'ECONOMIC CALENDAR', reload:'Reload', filter_all:'ALL',
    us_blackout:'US BLACKOUT ACTIVE - Signal Bot opens no new positions',
    meta_signal:'RSI · EMA · MACD · Funding · Macro | 3x leverage',
    meta_grid:'Automated buy/sell levels in a price grid',
    meta_funding:'Monitoring mode: shows funding-rate opportunities but places NO real orders. "Earned" is an estimate.',
    meta_dca:'Time-based buying with dollar-cost averaging',
    total_trades:'Total Trades', pnl:'PnL', trades:'Trades',
    macro:'Macro', invested:'Invested', next_buy:'Next Buy',
    earned:'Earned (est.)', opportunities:'Opportunities',
    overview:'OVERVIEW', signal_log:'Signal Bot Log',
    grid_log:'Grid Bot Log', funding_log:'Funding Bot Log',
    dca_log:'DCA Bot Log', last_activity:'Latest Activity',
    macro_events:'Macro Events (48h)',
    positions:'Open Positions (all Bots)',
    fg_chart:'Fear & Greed Index - 30 Days',
    bt_start:'START BACKTEST', bt_symbol:'Symbol',
    bt_period:'Period', bt_lever:'Leverage', bt_thresh:'Signal Threshold (1-3)', bt_pos:'Position size %',
    bt_sl:'Stop Loss %', bt_tp:'Take Profit %',
    bt_wf:'Walk-Forward (70/30 Train/Test Split)',
    bt_compare:'COMPARE ALL SYMBOLS',
    bt_trades:'Total Trades', bt_winrate:'Win Rate',
    bt_totalpnl:'Total PnL', bt_drawdown:'Max Drawdown',
    bt_sharpe:'Sharpe Ratio', bt_fees:'Total Fees',
    bt_equity:'Equity Curve (Start: 1000 USDT)',
    bt_endcap:'Final Capital',
    al_title:'ALERTS & NOTIFICATIONS',
    al_new:'Create New Alert', al_type:'Type', al_coin:'Coin',
    al_value:'Value / Threshold', al_name:'Name (optional)',
    al_add:'ADD ALERT', al_active:'Active Alerts',
    al_log:'Recent Triggers', al_triggered:'TRIGGERED',
    al_active_s:'ACTIVE', al_disabled:'DISABLED',
    price_above:'Price ABOVE threshold', price_below:'Price BELOW threshold',
    pnl_below:'Total PnL below value', funding_above:'Funding Rate above threshold',
    settings_save:'SAVE SETTINGS',
    settings_note:'Important: Always click SAVE after entering values, then START.',
    mode_demo:'DEMO MODE', mode_live:'LIVE MODE',
    saved_msg:'Saved.',
    confirm_live:'ACTIVATE LIVE MODE? Real orders with real money. Restart all running bots afterwards!',
    mode_demo_active:'DEMO MODE active', mode_live_active:'LIVE MODE active',
    set_head:'SETTINGS',
    set_mode_head:'Trading mode',
    set_mode_hint:'Demo = paptrading:1 (no real money). Live = real orders on Bitget.',
    set_mode_warn:'⚠️ After switching, restart all running bots so the new mode takes effect.',
    set_presets_head:'Strategy presets',
    set_presets_hint:'Presets auto-fill the Signal and Grid bot fields. Then just enter your API keys.',
    set_auth_head:'Dashboard access',
    set_auth_hint:'You choose your own username and password on first start (terminal prompt or browser setup wizard). You can change them here anytime — click SAVE, then the browser asks for the new login on next load.',
    lbl_user:'Username', lbl_pass:'Password', ph_pass_unchanged:'Leave empty = unchanged',
    set_global_head:'Global API keys',
    set_global_hint:'Important: Always click SAVE below after entering keys, then press START.',
    ph_finnhub:'For the macro calendar (free)', ph_coinalyze:'For the Derivatives tab (free, coinalyze.net)',
    ph_tg_chat:'Your chat ID (e.g. 123456789)',
    set_notify_note:'Telegram: @BotFather → /newbot → token. Chat ID from @userinfobot.<br>Discord: Server settings → Integrations → Webhooks → copy URL.<br>Both can be active at once. News sentiment: CoinGecko (free, no key).',
    set_preset:'Preset:', bp_cons:'CONSERVATIVE', bp_std:'STANDARD', bp_agg:'AGGRESSIVE',
    bp_passiv:'PASSIVE', bp_defensiv:'DEFENSIVE', bp_offensiv:'OFFENSIVE',
    preset_low:'🟢 LOW RISK', preset_med:'🔵 MEDIUM RISK', preset_high:'🔴 HIGH RISK',
    preset_desc_passiv:'PASSIVE: 1x leverage, only very strong signals (threshold 5), small grid. Few, careful trades.',
    preset_desc_defensiv:'DEFENSIVE: 2x leverage, threshold 4, 8-level grid. Careful.',
    preset_desc_standard:'STANDARD: 3x leverage, threshold 3, 12-level grid. Balanced.',
    preset_desc_offensiv:'OFFENSIVE: 5x leverage, threshold 2, trend factor on, 20-level grid. More trades.',
    preset_desc_aggressiv:'AGGRESSIVE: 8x leverage, threshold 1, trend factor on, 30-level grid. Many trades, more risk.',
    lbl_risk_trade:'Risk per trade (%)', lbl_usdt_trade:'USDT per trade (fallback)', lbl_budget:'Budget (USDT)',
    lbl_autostart:'Auto-start after reboot',
    lbl_max_conc:'Max simultaneous pos.', lbl_corr_filter:'Correlation filter', lbl_max_corr:'Max correlation (0.5-1.0)',
    note_corr:'Correlation filter: prevents the bot from opening a new position that is too strongly correlated with an already-open one in the same direction (diversification). When data is missing it keeps trading as normal.',
    lbl_adx_filter:'ADX trend filter', lbl_min_adx:'Min ADX (10-40)',
    note_adx:'ADX trend filter: dampens the signal when there is no clear trend (ADX below threshold) – trades less in sideways chop. Fail-open when there is too little data.',
    lbl_adx_gate:'ADX hard filter (trend only)',
    note_adx_gate:'Harder than the dampener: below Min ADX it does not trade at all (signal → NEUTRAL). Avoids costly fake-outs in sideways markets. Recommended ON (Min ADX e.g. 25).',
    lbl_sl_mult:'Stop-loss width (ATR factor)',
    hint_sl_mult:'Entry stop distance = ATR × factor. Larger = more room, less whipsaw (but bigger loss per bad trade). 1.5 = tight, 2.5–3 = patient.',
    lbl_coins:'Coins (comma-separated)',
    hint_coins:'Which coins the Signal bot trades. Just drop broken demo coins (e.g. XRP). Change takes effect on the next bot start.',
    lbl_ob:'Order-book buy pressure',
    note_ob:'Order-book buy pressure: includes buy/sell pressure from the live order book as an extra signal factor. Fail-open when no data is available.',
    lbl_factors:'Score factors (on/off)',
    note_factors:'Score factors: turn individual indicators on/off. All existing ones are on by default. You can see each factor\'s contribution live per coin in the SIGNAL tab.',
    lbl_trend:'Trend filter (long EMA)', lbl_trend_len:'Trend EMA length (20-200)',
    note_trend:'Trend filter (MT5 style): extra +1/-1 factor depending on whether price is above/below a long EMA. Off by default. Makes signals more selective (trades more with the higher-timeframe trend).',
    lbl_sig_thresh:'Signal threshold (1-5)',
    lbl_daily_limit:'Daily loss limit % (0 = off)',
    hint_daily_limit:'Pauses the bot until the next day (UTC) when the daily loss reaches this %. 0 = off (bot keeps trading). For LIVE e.g. 5-10 recommended.',
    lbl_trend_gate:'Hard trend filter (no counter-trend)',
    hint_trend_gate:'Above the long EMA only LONG, below only SHORT. Stops the bot from trading against the trend (e.g. shorting a rally). Recommended ON.',
    lbl_htf_trend:'Trend on 1h timeframe', hint_htf_trend:'Computes the trend EMA on 1-hour candles instead of 1-minute noise. The filter then reflects the REAL trend: short dips no longer flip it, no counter-trend shorts in a rally. When ON, "Trend EMA length" is in HOURS (e.g. 24 = 1-day trend, 50 = ~2 days). Recommended ON.',
    lbl_cooldown:'Cooldown per coin (min, 0 = off)',
    hint_cooldown:'After closing a position the same coin is locked for this long. Stops constant in/out (anti-churn).',
    lbl_trailing:'Trailing stop', lbl_trail_mult:'Trailing distance (ATR factor)',
    hint_trailing:'Instead of a fixed take-profit the stop trails the price (distance = ATR × factor), letting winners run in a trend. Smaller = tighter/locks earlier, larger = more room. Recommended ON.',
    grid_oneway_warn:'<b>IMPORTANT:</b> the Bitget sub-account must be set to <b>One-Way Mode</b>!<br>Bitget app: Futures trading -> Settings -> Position mode -> One-Way Mode.<br>In Hedge mode the Grid Bot unintentionally opens opposing positions.',
    lbl_price_up:'Upper price (0 = auto)', lbl_price_low:'Lower price (0 = auto)', lbl_levels:'Number of levels',
    lbl_min_funding:'Min funding rate (%)', lbl_max_pos:'Max position (USDT)',
    dca_spot_note:'DCA always buys on the spot market (no leverage, no funding costs). The balance must sit on the sub-account\'s spot account.',
    lbl_interval:'Interval (hours)', lbl_amount_buy:'Amount per buy (USDT)',
    dca_conn_note:'The connection test shows spot balance (capital used) and futures balance separately.<br>Tip: for DCA only fund the spot balance, leave the futures account empty.',
    markt_title:'MARKET OVERVIEW', symbol:'Symbol', price:'Price',
    change24:'24h %', high24:'24h High', low24:'24h Low',
    funding:'Funding', volume:'Volume (M $)',
    trades_title:'TRADE HISTORY', time:'Time', bot:'Bot',
    side:'Side', amount:'Size', fee:'Fee',
    entry:'Entry', exit:'Exit', result:'Result',
    timing:'TRADE TIMING ANALYSIS',
    grid_add:'+ ADD GRID', grid_name:'Name',
    grid_levels:'Grid Levels', grid_invest:'Investment (USDT)',
    help_what:'What is this?', help_close:'Close',
    no_data:'No data', loading:'Loading...', error:'Error',
    circuit_active:'CIRCUIT BREAKER ACTIVE',
    win_streak:'Win/Loss Streak', current:'current',
    wins_in_row:'wins in a row', losses_in_row:'losses in a row',
  }
};

// Help texts - also bilingual
const HELP_TEXT = {
  overview: {
    de: {title:'OVERVIEW', sub:'Alle Bots auf einen Blick', accent:'#00d68f',
      sections:[
        {title:'Was zeigt der Overview?',
         text:'Der Overview aggregiert alle laufenden Bots. Gesamt-Balance addiert die Balance aller Sub-Accounts. PnL zeigt Gewinn/Verlust seit dem letzten Start. Aktive Bots zeigt wie viele von 4 Bots laufen.'},
        {title:'Fear & Greed Index',
         text:'Misst die Marktstimmung auf einer Skala von 0 (Extremangst) bis 100 (Extreme Gier). Unter 25: Kaufgelegenheit laut historischen Daten. Ueber 75: Vorsicht, Markt ueberhitzt. Zeigt die letzten 30 Tage.'},
        {title:'Circuit Breaker',
         text:'BTC bewegt sich mehr als 5% innerhalb einer Stunde -> automatische Pause aller Bots fuer 30 Minuten. Schutzmechanismus fuer Flash Crashes und extreme Volatilitaet.'},
      ]},
    en: {title:'OVERVIEW', sub:'All bots at a glance', accent:'#00d68f',
      sections:[
        {title:'What does Overview show?',
         text:'Overview aggregates all running bots. Total Balance adds up all sub-account balances. PnL shows profit/loss since last start. Active Bots shows how many of 4 bots are running.'},
        {title:'Fear & Greed Index',
         text:'Measures market sentiment on a scale from 0 (Extreme Fear) to 100 (Extreme Greed). Below 25: historically a buying opportunity. Above 75: caution, market is overheated. Shows last 30 days.'},
        {title:'Circuit Breaker',
         text:'BTC moves more than 5% within one hour -> automatic pause of all bots for 30 minutes. Protection mechanism for flash crashes and extreme volatility.'},
      ]},
  },
  signal: {
    de: {title:'SIGNAL BOT', sub:'RSI, EMA, MACD, BB, Volume, Funding, Fear&Greed, Sentiment, Makro', accent:'#00d68f',
      sections:[
        {title:'Wie funktioniert der Score?',
         table:[
           ['EMA Kreuzung','Fast EMA (8) > Slow EMA (20) = bullish +1, darunter -1'],
           ['Wilder RSI','RSI < 38 = ueberverkauft +1, RSI > 62 = ueberkauft -1'],
           ['MACD','MACD-Linie > Signal-Linie = bullish +1, darunter -1'],
           ['Bollinger Bands','Preis unter unterem Band +1, ueber oberem -1'],
           ['Volume Ratio','Hohes Volumen bestaetigt Signal, niedriges daempft es'],
           ['Funding Rate','Negative Rate bullish, stark positive Rate bearish'],
           ['Fear & Greed','Unter 30 (Angst) = +1, ueber 70 (Gier) = -1'],
           ['News-Sentiment','CoinGecko Community-Votes: bullish/bearish/neutral'],
           ['Makro','US-Events reduzieren Score, aktiver Blackout stoppt Trading'],
         ]},
        {title:'ATR-basierter Stop Loss',
         text:'Stop Loss und Take Profit basieren auf dem Average True Range (ATR). SL = 1.5x ATR, TP = 2.5x ATR vom Einstiegspreis. Passt sich automatisch der aktuellen Volatilitaet an.'},
        {title:'Korrelations-Check',
         text:'Max. 2 gleichzeitige Positionen. SOL und ETH sind oft korreliert - doppeltes Risiko waere suboptimal. Konfigurierbar unter Settings.'},
      ]},
    en: {title:'SIGNAL BOT', sub:'RSI, EMA, MACD, BB, Volume, Funding, Fear&Greed, Sentiment, Macro', accent:'#00d68f',
      sections:[
        {title:'How does the score work?',
         table:[
           ['EMA Cross','Fast EMA (8) > Slow EMA (20) = bullish +1, below -1'],
           ['Wilder RSI','RSI < 38 = oversold +1, RSI > 62 = overbought -1'],
           ['MACD','MACD line > Signal line = bullish +1, below -1'],
           ['Bollinger Bands','Price below lower band +1, above upper band -1'],
           ['Volume Ratio','High volume confirms signal, low volume dampens it'],
           ['Funding Rate','Negative rate bullish, strongly positive rate bearish'],
           ['Fear & Greed','Below 30 (fear) = +1, above 70 (greed) = -1'],
           ['News Sentiment','CoinGecko community votes: bullish/bearish/neutral'],
           ['Macro','US events reduce score, active blackout stops trading'],
         ]},
        {title:'ATR-based Stop Loss',
         text:'Stop Loss and Take Profit are based on Average True Range (ATR). SL = 1.5x ATR, TP = 2.5x ATR from entry price. Automatically adapts to current volatility.'},
        {title:'Correlation Check',
         text:'Max. 2 simultaneous positions. SOL and ETH are often correlated - double risk would be suboptimal. Configurable under Settings.'},
      ]},
  },
  backtest: {
    de: {title:'BACKTESTING', sub:'Signal Bot Strategie auf historischen Daten testen', accent:'#00d68f',
      sections:[
        {title:'Was ist Backtesting?',
         text:'Simuliert wie der Signal Bot in der Vergangenheit gehandelt haette. Nutzt 5 Indikatoren (EMA, Wilder RSI, MACD, Bollinger Bands, Volume) + ATR-SL. Gebuehren (0.04%) werden abgezogen.'},
        {title:'Walk-Forward Test',
         text:'70% der Daten sind Training (gesehen), 30% sind Test (ungesehen). Das Ergebnis auf den Testdaten ist realistischer als ein einfacher Backtest auf dem gesamten Zeitraum.'},
        {title:'Kennzahlen',
         table:[
           ['Win Rate','Anteil profitabler Trades. Ueber 55% ist gut.'],
           ['Sharpe Ratio','Rendite / Risiko. Ueber 1.5 gut, ueber 2.0 sehr gut.'],
           ['Max Drawdown','Groesster Verlust vom Hochpunkt. Unter 15% ist sicher.'],
           ['Gebuehren','0.04% Taker-Fee pro Trade. Oft unterschaetzt!'],
         ]},
        {title:'Wichtige Einschraenkung',
         text:'Der Backtest nutzt nur technische Indikatoren (RSI, EMA, MACD). Makro-Blackouts, Funding Rates, News-Sentiment, Fear&Greed, Korrelations-/ADX-Filter und der Volatilitaets-Circuit-Breaker (>5% BTC in 1h pausiert live alle Bots) sind NICHT eingebaut. Der Backtest nimmt Flash-Crashes also voll mit, waehrend das echte System dann pausiert haette. Das echte System hat dadurch oft bessere Ergebnisse als der Backtest zeigt, aber auch mehr Pausen.'},
      ]},
    en: {title:'BACKTESTING', sub:'Test Signal Bot strategy on historical data', accent:'#00d68f',
      sections:[
        {title:'What is backtesting?',
         text:'Simulates how the Signal Bot would have traded in the past. Uses 5 indicators (EMA, Wilder RSI, MACD, Bollinger Bands, Volume) + ATR-SL. Fees (0.04%) are deducted.'},
        {title:'Walk-Forward Test',
         text:'70% of data is training (seen), 30% is test (unseen). The result on test data is more realistic than a simple backtest on the full period.'},
        {title:'Key Metrics',
         table:[
           ['Win Rate','Share of profitable trades. Above 55% is good.'],
           ['Sharpe Ratio','Return / Risk. Above 1.5 good, above 2.0 excellent.'],
           ['Max Drawdown','Largest loss from peak. Below 15% is safe.'],
           ['Fees','0.04% taker fee per trade. Often underestimated!'],
         ]},
        {title:'Important Limitation',
         text:'The backtest only uses technical indicators (RSI, EMA, MACD). Macro blackouts, funding rates, news sentiment, fear & greed, correlation/ADX filters and the volatility circuit breaker (>5% BTC in 1h pauses all live bots) are NOT built in. Therefore, the backtest takes full flash crashes, while the real system would have paused.'},
      ]},
  },
  alerts: {
    de: {title:'ALERTS', sub:'Automatische Benachrichtigungen via Telegram & Discord', accent:'#fbbf24',
      sections:[
        {title:'Wie funktionieren Alerts?',
         text:'Alerts pruefen alle 60 Sekunden eine Bedingung. Wenn sie zutrifft, wird eine Telegram- und/oder Discord-Nachricht gesendet. Reset automatisch wenn Bedingung nicht mehr gilt.'},
        {title:'Alert-Typen',
         table:[
           ['Preis UEBER','Alarm wenn Coin-Preis eine Schwelle ueberschreitet'],
           ['Preis UNTER','Alarm wenn Coin-Preis unter eine Schwelle faellt'],
           ['PnL unter Wert','Alarm wenn Gesamt-PnL aller Bots unter Schwellwert'],
           ['Funding Rate','Alarm bei hoher Funding-Rate (Opportunity-Alert)'],
         ]},
      ]},
    en: {title:'ALERTS', sub:'Automated notifications via Telegram & Discord', accent:'#fbbf24',
      sections:[
        {title:'How do alerts work?',
         text:'Alerts check a condition every 60 seconds. When triggered, a Telegram and/or Discord message is sent. Auto-resets when condition no longer applies.'},
        {title:'Alert Types',
         table:[
           ['Price ABOVE','Alert when coin price exceeds a threshold'],
           ['Price BELOW','Alert when coin price falls below a threshold'],
           ['PnL below value','Alert when total PnL of all bots falls below threshold'],
           ['Funding Rate','Alert for high funding rate (opportunity alert)'],
         ]},
      ]},
  },
};

function t(key) {
  return (STRINGS[_lang] || STRINGS.de)[key] || (STRINGS.de)[key] || key;
}

function toggleLang() {
  _lang = _lang === 'de' ? 'en' : 'de';
  try { localStorage.setItem('tp_lang', _lang); } catch(e) {}
  location.reload();
}

function applyLang() {
  // Nav tabs
  const tabMap = {
    overview:'nav_overview', signal:'nav_signal', grid:'nav_grid',
    dca:'nav_dca', markt:'nav_markt',
    trades:'nav_trades',
    backtest:'nav_backtest', alerts:'nav_alerts', settings:'nav_settings',
    syslog:'nav_syslog',
  };
  document.querySelectorAll('.tab[data-tab]').forEach(btn => {
    const k = tabMap[btn.dataset.tab];
    if (k) btn.childNodes[0].textContent = t(k);
  });
  // All elements with data-i18n (including <option> which needs .text)
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const val = t(el.dataset.i18n);
    if (el.tagName === 'OPTION')       el.text = val;
    else if (val.indexOf('<') >= 0)    el.innerHTML = val;   // Strings mit <br>/<b> (statische UI-Texte)
    else                               el.textContent = val;
  });
  // Placeholder-Uebersetzungen (data-i18n-ph)
  document.querySelectorAll('[data-i18n-ph]').forEach(el => {
    el.setAttribute('placeholder', t(el.dataset.i18nPh));
  });
  // Buttons
  document.querySelectorAll('.btn-validate').forEach(btn => btn.textContent = t('test_conn'));
  const btBtn = document.getElementById('bt-run-btn');
  if (btBtn) btBtn.textContent = t('bt_start');
  const btMulti = document.getElementById('bt-multi-btn');
  if (btMulti) btMulti.textContent = t('bt_compare');
  document.querySelectorAll('.save-btn').forEach(b => b.textContent = t('settings_save'));
  // Lang button
  const lb = document.getElementById('lang-btn');
  if (lb) lb.textContent = _lang === 'de' ? 'DE / EN' : 'EN / DE';
}


const pnlHistory = {signal:[],grid:[],dca:[]};
const MAX_PTS = 80;

function trackPnl(state) {
  ['signal','grid','dca'].forEach(id => {
    const v = parseFloat(state.bots[id]?.pnl || 0);
    pnlHistory[id].push(v);
    if (pnlHistory[id].length > MAX_PTS) pnlHistory[id].shift();
  });
}

function sparkline(id, data) {
  const el = document.getElementById(id);
  if (!el || data.length < 2) return;
  const W = 400, H = 40, pad = 2;
  const min  = Math.min(...data, 0);
  const max  = Math.max(...data, 0);
  const rng  = max - min || 0.01;
  const scX  = i => (i / (data.length - 1)) * (W - pad*2) + pad;
  const scY  = v => H - pad - ((v - min) / rng) * (H - pad*2);
  const pts  = data.map((v,i) => scX(i)+','+scY(v)).join(' ');
  const last = data[data.length - 1];
  const prev = data[data.length - 2] || 0;
  const cls  = last > 0.001 ? 'pos' : last < -0.001 ? 'neg' : 'flat';
  const fillPts = pts + ' ' + scX(data.length-1)+','+H + ' '+pad+','+H;
  const zY   = scY(0);
  el.innerHTML =
    '<line class="spark-zero" x1="'+pad+'" y1="'+zY+'" x2="'+(W-pad)+'" y2="'+zY+'"/>' +
    '<polygon points="'+fillPts+'" class="spark-fill-'+cls+'"/>' +
    '<polyline points="'+pts+'" class="spark-line-'+cls+'"/>';
  const trendEl = document.getElementById(id.replace('-spark','-trend'));
  if (trendEl) {
    const delta = last - prev;
    trendEl.textContent = (last>=0?'+':'')+last.toFixed(2)+' USDT ' + (delta>0.001?'(+'+delta.toFixed(2)+')':delta<-0.001?'('+delta.toFixed(2)+')':'');
    trendEl.className   = 'trend-'+(last>0.001?'up':last<-0.001?'down':'flat');
  }
}

function updateSparklines() {
  sparkline('s-spark', pnlHistory.signal);
  sparkline('g-spark', pnlHistory.grid);
  sparkline('d-spark', pnlHistory.dca);
}


// -- PANIC BUTTON ----------------------------------------------
async function triggerPanic() {
  const confirmed = confirm('NOTFALL-STOPP: Stoppt alle Bots und schliesst alle Positionen. Fortfahren?');
  if (!confirmed) return;
  const btn = document.getElementById('panic-btn');
  btn.textContent = '... Wird ausgefuehrt...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/panic', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d = await r.json();
    const res = d.result || {};
    btn.textContent = `OK: ${res.closed||0} geschlossen, ${res.errors||0} Fehler`;
    btn.style.color = 'var(--signal)';
    setTimeout(() => {
      btn.textContent = 'NOTFALL-STOPP ALL STOP & CLOSE';
      btn.style.color = '';
      btn.disabled = false;
    }, 8000);
    await poll();
  } catch(e) {
    btn.textContent = 'FEHLER: Fehler';
    setTimeout(() => { btn.textContent = 'NOTFALL-STOPP ALL STOP & CLOSE'; btn.disabled = false; }, 3000);
  }
}

// -- LIVE MODE TOGGLE ------------------------------------------
function onLiveModeChange(isLive) {
  const label = document.getElementById('mode-label');
  if (isLive) {
    const ok = confirm(t('confirm_live'));
    if (!ok) {
      document.getElementById('cfg-live').checked = false;
      return;
    }
    label.textContent = t('mode_live_active');
    label.style.color = 'var(--red)';
    document.body.className = 'live-mode';
  } else {
    label.textContent = t('mode_demo_active');
    label.style.color = 'var(--grid)';
    document.body.className = 'demo-mode';
  }
}

// -- STRATEGY PRESETS ------------------------------------------
// Globaler Strategie-Preset (Signal + Grid gleichzeitig). Gleiche 5 Stufen wie die
// Bot-eigenen Presets (BOT_PRESETS), damit alles konsistent ist.
const PRESETS = {
  passiv:    {signal:{lever:1, usdt:15, thresh:5, trend:false}, grid:{n:6,  inv:50}},
  defensiv:  {signal:{lever:2, usdt:20, thresh:4, trend:false}, grid:{n:8,  inv:80}},
  standard:  {signal:{lever:3, usdt:30, thresh:3, trend:false}, grid:{n:12, inv:100}},
  offensiv:  {signal:{lever:5, usdt:40, thresh:2, trend:true},  grid:{n:20, inv:200}},
  aggressiv: {signal:{lever:8, usdt:60, thresh:1, trend:true},  grid:{n:30, inv:300}},
};

function applyPreset(id) {
  const p = PRESETS[id];
  if (!p) return;
  document.getElementById('preset-desc').textContent = t('preset_desc_'+id);
  // Signal Bot
  if (p.signal) {
    document.getElementById('sig-lever').value  = p.signal.lever;
    document.getElementById('sig-usdt').value   = p.signal.usdt;
    document.getElementById('sig-thresh').value = p.signal.thresh;
    const tr = document.getElementById('sig-f-trend');
    if (tr && p.signal.trend !== undefined) tr.checked = p.signal.trend;
  }
  // Grid Bot
  if (p.grid) {
    document.getElementById('grd-n').value   = p.grid.n;
    document.getElementById('grd-inv').value = p.grid.inv;
  }
  // Open the sections so user sees changes
  document.getElementById('s-signal').classList.add('open');
  document.getElementById('s-grid').classList.add('open');
}

// -- API KEY VALIDATION ----------------------------------------
async function validateKey(botId) {
  const keys = {
    signal:  {key:'sig-key',  sec:'sig-sec',  pass:'sig-pass'},
    grid:    {key:'grd-key',  sec:'grd-sec',  pass:'grd-pass'},
    dca:     {key:'dca-key',  sec:'dca-sec',  pass:'dca-pass'},
  };
  const ids    = keys[botId];
  const result = document.getElementById('val-' + botId);
  result.textContent = '... Teste...';
  result.style.display = 'inline';
  result.style.color = 'var(--muted)';
  try {
    const r = await fetch('/api/validate', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        bot_id:     botId,
        api_key:    document.getElementById(ids.key).value,
        api_secret: document.getElementById(ids.sec).value,
        passphrase: document.getElementById(ids.pass).value,
      }),
    });
    const d = await r.json();
    result.textContent = d.status === 'ok' ? 'OK: ' + d.msg : 'FEHLER: ' + d.msg;
    result.style.color = d.status === 'ok' ? 'var(--signal)' : 'var(--red)';
  } catch(e) {
    result.textContent = 'FEHLER: Verbindungsfehler';
    result.style.color = 'var(--red)';
  }
}

// -- TRADINGVIEW CHART -----------------------------------------
let tvChart = null;
let tvCandles = null;
let tvPriceLines = [];

function initTVChart() {
  const el = document.getElementById('tv-chart');
  if (!el || typeof LightweightCharts === 'undefined') return;
  if (tvChart) { tvChart.remove(); tvChart = null; tvCandles = null; tvPriceLines = []; }
  tvChart = LightweightCharts.createChart(el, {
    width: el.clientWidth, height: 260,
    layout: {background:{color:'#0e0e10'}, textColor:'#666'},
    grid: {vertLines:{color:'#1a1a1c'}, horzLines:{color:'#1a1a1c'}},
    crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
    rightPriceScale: {borderColor:'#1e1e22'},
    timeScale: {borderColor:'#1e1e22', timeVisible:true},
  });
  tvCandles = tvChart.addCandlestickSeries({
    upColor:'#00d68f', downColor:'#f87171',
    borderUpColor:'#00d68f', borderDownColor:'#f87171',
    wickUpColor:'#00d68f', wickDownColor:'#f87171',
  });
  window.addEventListener('resize', () => {
    if (tvChart) tvChart.applyOptions({width: el.clientWidth});
  });
}

async function loadTVChart(symbol) {
  if (!tvChart || !tvCandles) return;
  try {
    const r = await fetch('/api/klines?symbol=' + (symbol||'BTCUSDT') + '&granularity=1H');
    const d = await r.json();
    const raw = d.data || [];
    const candles = raw.reverse().map(c => ({
      time:  Math.floor(parseInt(c[0]) / 1000),
      open:  parseFloat(c[1]),
      high:  parseFloat(c[2]),
      low:   parseFloat(c[3]),
      close: parseFloat(c[4]),
    })).filter(c => !isNaN(c.open));
    if (candles.length > 0) tvCandles.setData(candles);
  } catch(e) { console.log('TV chart:', e); }
}

function updateTVGridLines(orders, upper, lower) {
  if (!tvCandles) return;
  tvPriceLines.forEach(l => { try { tvCandles.removePriceLine(l); } catch(e){} });
  tvPriceLines = [];
  if (!orders || !orders.length) return;
  const mid = (upper + lower) / 2;
  orders.forEach((o, i) => {
    const isBuy  = o.price < mid;
    const color  = o.filled ? (isBuy ? '#00d68f' : '#f87171') : '#2a2a2e';
    const line   = tvCandles.createPriceLine({
      price: o.price, color,
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: o.filled ? (isBuy ? '> BUY' : '> SELL') : '',
    });
    tvPriceLines.push(line);
  });
}



// -- HELP CONTENT ----------------------------------------------
const HELP = {
  overview: {
    title: 'OVERVIEW',
    sub: 'Gesamtuebersicht aller laufenden Bots',
    accent: '#f4f4f5',
    sections: [
      {
        title: 'Was zeigt dieser Tab?',
        text: 'Der Overview gibt dir auf einen Blick den kombinierten Status aller vier Bots. Gesamt-Balance, Gesamt-PnL, wie viele Bots gerade laufen und wie viele Trades insgesamt gemacht wurden. Darunter siehst du eine Tabelle mit dem Status jedes Bots, und kannst jeden einzeln starten oder stoppen.'
      },
      {
        title: 'Makro-Ereignisse',
        text: 'Hier werden High-Impact Wirtschaftsdaten der naechsten 48 Stunden angezeigt. <b>Rot</b> = US-Ereignis (loest Blackout im Signal Bot aus). <b>Gelb</b> = EU/andere Laender (verringert Signal-Score, kein harter Blackout). Benoetigt einen Finnhub API Key unter Settings.'
      },
      {
        title: 'Aktivitaets-Log',
        text: 'Die letzten Eintraege aus allen Bots zusammengefasst. Fuer detailliertere Logs den jeweiligen Bot-Tab oeffnen.'
      }
    ]
  },
  signal: {
    title: 'SIGNAL BOT',
    sub: 'Indikatorbasiertes Long/Short-Trading mit Hebel',
    accent: '#00d68f',
    sections: [
      {
        title: 'Risiko & Rendite',
        table: [
          ['Risikostufe', '[MITTEL-HOCH] MITTEL-HOCH'],
          ['Gutes Jahr', '15 - 35% p.a. in klar trendenden Maerkten'],
          ['Durchschnittsjahr', '0 - 15% p.a. - Gebuehren und Fehlsignale reduzieren die Rendite'],
          ['Schlechtes Jahr', '-10 bis -30% p.a. in seitwaetstrendenden, choppy Maerkten'],
          ['Groesstes Risiko', 'Chop: viele Fehlsignale hintereinander. Hebel wirkt in beide Richtungen.'],
          ['Schutz', '1% SL, Tageslimit und Makro-Blackout begrenzen den Schaden'],
        ]
      },
      {
        title: 'Wie funktioniert der Bot?',
        text: 'Bewertet jeden Token alle 30 Sekunden mit einem Score. Jeder Indikator gibt +1 (bullish) oder -1 (bearish). Score +3 oder hoeher = Long-Position. Score -3 oder tiefer = Short-Position. Neutrale Scores fuehren zu keiner Aktion.'
      },
      {
        title: 'Score-System (9 Indikatoren)',
        table: [
          ['EMA 8/20', '+1 wenn EMA8 ueber EMA20 (Trend), -1 darunter'],
          ['RSI (14)', '+1 unter 38 (ueberverkauft), -1 ueber 62 (ueberkauft)'],
          ['MACD', '+1 wenn MACD-Linie ueber Signal-Linie'],
          ['Volumen', 'Bestaetigt oder daempft das Signal je nach Staerke'],
          ['Funding Rate', '-1 bei Long-Uebersaettigung, +1 wenn Markt short-lastig ist'],
          ['Fear & Greed', '+1 bei Extrem-Angst (<30), -1 bei Extrem-Gier (>70)'],
          ['News', '+1 bullish, -1 bearish - aus Krypto-Nachrichtenquellen'],
          ['Makro US', '+/-1 aus US-Wirtschaftsdaten (Actual vs. Estimate)'],
          ['Makro Non-US', 'Soft-Penalty bis -2 bei EU/DE High-Impact Events'],
        ]
      },
      {
        title: 'Risiko-Parameter',
        table: [
          ['Stop Loss', '1.0% - automatisch bei Ordereroeffnung gesetzt'],
          ['Take Profit', '2.0% - Risk/Reward Ratio von 1:2'],
          ['Tageslimit', '-2% des Startkapitals -> Bot pausiert 1 Stunde'],
          ['US Blackout', 'Keine neuen Positionen rund um FOMC / CPI / NFP'],
        ]
      },
    ]
  },
  grid: {
    title: 'GRID BOT',
    sub: 'Automatisches Kaufen und Verkaufen in einem Preis-Raster',
    accent: '#4da6ff',
    sections: [
      {
        title: 'Risiko & Rendite',
        table: [
          ['Risikostufe', '[NIEDRIG] NIEDRIG-MITTEL (kein Hebel im Standard-Setup)'],
          ['Gutes Jahr', '20 - 40% p.a. in volatilen Seitwaetstmaerkten'],
          ['Durchschnittsjahr', '8 - 20% p.a. - haengt stark von der Marktphase ab'],
          ['Schlechtes Jahr', '0 bis -25% p.a. wenn Preis dauerhaft aus der Grid-Range faellt'],
          ['Groesstes Risiko', 'Starker Downtrend: Bot kauft auf jedem Level nach, unrealisierter Verlust waechst'],
          ['Goldene Regel', 'Grid-Range nur in bekannten Seitwaetstmaerkten laufen lassen, bei Trending-Markt stoppen'],
        ]
      },
      {
        title: 'Grundprinzip',
        text: 'Der Grid Bot teilt einen Preisbereich (z.B. 90.000 - 100.000 USDT bei BTC) in gleichmaessige Level auf. Faellt der Preis auf ein Level, wird per Market-Order gekauft. Steigt er wieder, wird verkauft. Der Profit kommt aus diesen wiederholten kleinen Schwankungen - ohne Trendvorhersage.'
      },
      {
        title: 'Wann laufen lassen, wann stoppen?',
        table: [
          ['Laufen lassen', 'Preis pendelt in einer bekannten Range (z.B. BTC seit Wochen zwischen 90k-100k)'],
          ['Stoppen', 'Klarer Trend erkennbar - entweder nach oben (Gewinnmitnahme) oder unten (Verlustbegrenzung)'],
          ['Auto-Range', 'Bot setzt +/-5% um aktuellen Preis - fuer ruhige Maerkte ausreichend'],
          ['Manuelle Range', 'Besser fuer Coins die du gut kennst und deren Handelsspanne du einschaetzen kannst'],
        ]
      },
      {
        title: 'Parameter erklaert',
        table: [
          ['Preis oben/unten', 'Definiert die Range. Bei 0 = automatisch +/-5% um aktuellen Preis'],
          ['Anzahl Levels', '10-20 Levels ist ein guter Startwert. Mehr = enger = mehr Trades, mehr Gebuehren'],
          ['Investment', 'Gesamtbetrag aufgeteilt auf alle Levels'],
        ]
      },
    ]
  },
  dca: {
    title: 'DCA BOT',
    sub: 'Dollar-Cost-Averaging - regelmaessiges Kaufen auf dem Spot-Markt',
    accent: '#fbbf24',
    sections: [
      {
        title: 'Risiko & Rendite',
        table: [
          ['Risikostufe', '[NIEDRIG] NIEDRIG (kein Hebel, Spot-Markt, kein Liquidationsrisiko)'],
          ['Rendite', 'Entspricht der langfristigen Asset-Performance + Averaging-Vorteil'],
          ['BTC historisch (5J-Schnitt)', '~60 - 80% p.a. - aber mit extremer Volatilitaet'],
          ['Realistisch (3-5 Jahre halten)', '20 - 50% p.a. als konservative Erwartung fuer BTC/ETH'],
          ['Schlechtestes Szenario', 'Wenn das Asset langfristig faellt (z.B. ein totes Projekt) - verlierst du unabhaengig vom Averaging'],
          ['Hauptrisiko', 'Psychologie: Bei -50% Drawdown den Plan trotzdem durchhalten (buy the dip, nicht verkaufen)'],
        ]
      },
      {
        title: 'Was ist DCA?',
        text: 'Dollar-Cost-Averaging bedeutet: Du kaufst einen festen Betrag in regelmaessigen Abstaenden, egal ob der Preis hoch oder niedrig ist. Bei verschiedenen Kaufpreisen entsteht automatisch ein guenstiger Durchschnittspreis - du vermeidest den Fehler alles auf einmal zum Hochpunkt zu kaufen.'
      },
      {
        title: 'Warum Spot statt Futures?',
        text: 'Der DCA Bot kauft echtes BTC oder ETH - keine Futures-Kontrakte. Das ist wichtig: Futures-Long-Positionen zahlen alle 8 Stunden Funding Rate, die langfristig die Rendite auffressen wuerde. Spot bedeutet: du besitzt den Coin wirklich, kein Verfallsdatum, keine Funding-Kosten.'
      },
      {
        title: 'Empfohlene Strategie',
        table: [
          ['Asset', 'BTC oder ETH - die einzigen Kryptos mit nachgewiesener langfristiger Adoption'],
          ['Interval', 'Woechentlich (168h) oder zweimal pro Woche - nicht zu oft (Gebuehren)'],
          ['Betrag', 'Nur was du 3-5 Jahre nicht brauchst. DCA ist eine Langzeit-Strategie.'],
          ['Zeithorizont', 'Mindestens 2-3 Jahre. Kurzfristige Schwankungen ignorieren.'],
        ]
      },
    ]
  },
  backtest: {
    title: 'BACKTESTING',
    sub: 'Signal Bot Strategie auf historischen Daten testen',
    accent: '#00d68f',
    sections: [
      {title:'Was ist Backtesting?',
       text:'Backtesting simuliert wie der Signal Bot in der Vergangenheit gehandelt haette. Du konfigurierst dieselben Parameter (Hebel, Schwelle, SL, TP) und der Bot laeuft rueckwirkend durch historische 1H-Kerzen. Das Ergebnis zeigt ob die Strategie unter echten Marktbedingungen profitabel gewesen waere.'},
      {title:'Wichtige Einschraenkung',
       text:'Der Backtest verwendet nur technische Indikatoren (RSI, EMA, MACD). Makro-Blackouts, Funding Rates, News-Sentiment, Fear&Greed, Korrelations-/ADX-Filter und der Volatilitaets-Circuit-Breaker (>5% BTC in 1h pausiert live alle Bots) sind NICHT eingebaut. Der Backtest nimmt Flash-Crashes also voll mit, waehrend das echte System dann pausiert haette. Das echte System hat dadurch oft bessere Ergebnisse als der Backtest zeigt, aber auch mehr Pausen.'},
      {title:'Kennzahlen erklaert',
       table:[
         ['Win Rate','Anteil profitabler Trades. Ueber 55% ist gut.'],
         ['Max Drawdown','Groesster Verlust vom Hochpunkt. Ueber 20% bedeutet hohes Risiko.'],
         ['PnL gesamt','Summe aller Trade-Gewinne und -Verluste auf 1000 USDT Startkapital.'],
         ['Equity-Kurve','Visualisiert den Kapitalverlauf. Glattes Aufwaerts ist ideal.'],
       ]},
    ]
  },
  alerts: {
    title: 'ALERTS',
    sub: 'Automatische Benachrichtigungen bei wichtigen Ereignissen',
    accent: '#fbbf24',
    sections: [
      {title:'Wie funktionieren Alerts?',
       text:'Alerts pruefen alle 60 Sekunden eine Bedingung. Wenn sie zutrifft, wird eine Telegram-Nachricht gesendet. Der Alert bleibt aktiv aber "ausgeloest" bis die Bedingung wieder nicht mehr zutrifft - dann wird er automatisch zurueckgesetzt.'},
      {title:'Alert-Typen',
       table:[
         ['Preis UEBER','Sendet Alarm wenn der Coin-Preis einen bestimmten Wert ueberschreitet.'],
         ['Preis UNTER','Sendet Alarm wenn der Coin-Preis unter einen bestimmten Wert faellt.'],
         ['PnL unter Wert','Alarm wenn der Gesamt-PnL aller Bots einen negativen Schwellwert unterschreitet.'],
         ['Funding Rate','Alarm wenn die Funding Rate eines Coins eine bestimmte Schwelle ueberschreitet (nutzt die oeffentliche Funding-Rate, kein Bot noetig).'],
       ]},
      {title:'Voraussetzung',
       text:'Telegram Token und Chat-ID muessen unter Settings konfiguriert sein, sonst werden die Nachrichten nicht zugestellt.'},
    ]
  },
  settings: {
    sub: 'API-Keys und Bot-Parameter konfigurieren',
    accent: '#f4f4f5',
    sections: [
      {
        title: 'Strategievergleich auf einen Blick',
        table: [
          ['Signal Bot', '[MITTEL-HOCH] Mittel-Hoch | 0-35% p.a. | Trend-Maerkte'],
          ['Grid Bot', '[NIEDRIG] Niedrig-Mittel | 8-40% p.a. | Seitwaetstmaerkte'],
          ['DCA Bot', '[NIEDRIG] Niedrig | 20-50% p.a. | Langfristig, 3-5 Jahre'],
        ]
      },
      {
        title: 'Sub-Account Setup auf Bitget',
        table: [
          ['Schritt 1', 'Bitget oeffnen -> Profil -> Sub-Accounts -> Sub-Account erstellen'],
          ['Schritt 2', 'Fuer jeden Bot einen eigenen Sub-Account anlegen'],
          ['Schritt 3', 'Im Sub-Account: API Management -> Key erstellen'],
          ['Berechtigungen', 'Read + Trade aktivieren. Withdraw NIEMALS aktivieren.'],
          ['Schritt 4', 'Key, Secret und Passphrase hier in den jeweiligen Bot-Bereich eintragen'],
        ]
      },
      {
        title: 'Externe API Keys',
        table: [
          ['Finnhub', 'finnhub.io - kostenlos. Liefert den Makro-Kalender fuer US-Blackouts.'],
          ['CryptoPanic', 'cryptopanic.com - optional, kostenlos. News-Sentiment fuer Signal Bot.'],
          ['Fear & Greed', 'Kein Key noetig - kommt automatisch von alternative.me.'],
          ['Telegram', '@BotFather -> /newbot -> Token kopieren. Chat-ID von @userinfobot.'],
        ]
      },
      {
        title: 'Demo vs. Live',
        table: [
          ['Demo-Modus', 'paptrading:1 im Header - alle Orders gehen auf Bitget Demo-Konto'],
          ['Live-Modus', 'Header ohne paptrading - echte Orders mit echtem Geld'],
          ['DCA + Demo', 'Spot-Demo funktioniert bei Bitget eingeschraenkt. DCA mit 5 USDT Live testen.'],
          ['Empfehlung', 'Mindestens 4 Wochen Demo beobachten bevor Echtgeld eingesetzt wird.'],
        ]
      },
    ]
  }
};

function showHelp(id) {
  const entry = HELP_TEXT[id] || HELP[id];
  const h = entry ? (entry[_lang] || entry.de || entry) : null;
  if (!h) return;
  document.getElementById('help-title').textContent  = h.title;
  document.getElementById('help-title').style.color  = h.accent || '#f4f4f5';
  document.getElementById('help-sub').textContent    = h.sub;
  document.getElementById('help-body').innerHTML = h.sections.map(s => {
    let content = '';
    if (s.table) {
      content = '<table class="mtable">' +
        s.table.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td></tr>`).join('') +
        '</table>';
    } else {
      content = '<div class="modal-text">' +
        (s.text||'').replace(/\n/g,'<br>') + '</div>';
    }
    return `<div class="modal-section">
      <div class="modal-section-title">${s.title}</div>${content}
    </div>`;
  }).join('');
  document.getElementById('help-modal').classList.add('open');
}

function closeHelp() {
  document.getElementById('help-modal').classList.remove('open');
}

// -- END HELP --------------------------------------------------

let activePanel = 'overview';
let lastState   = null;

// -- MULTI-BACKTEST -------------------------------------------
async function runMultiBacktest() {
  const btn = document.getElementById('bt-multi-btn');
  btn.textContent = 'Laeuft...'; btn.disabled = true;
  document.getElementById('bt-multi-result').style.display = 'none';
  try {
    const r = await fetch('/api/multi_backtest', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        symbols:     ['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','DOGEUSDT'],
        period_days: parseInt(document.getElementById('bt-days').value)||14,
        leverage:    parseInt(document.getElementById('bt-lever').value)||3,
        threshold:   parseInt(document.getElementById('bt-thresh').value)||2,
        sl_pct:      parseFloat(document.getElementById('bt-sl').value)/100||0.01,
        tp_pct:      parseFloat(document.getElementById('bt-tp').value)/100||0.02,
        pos_pct:     parseFloat(document.getElementById('bt-pos').value)||10,
      })
    });
    const d = await r.json();
    renderMultiBacktest(d);
    document.getElementById('bt-multi-result').style.display = 'block';
  } catch(e) {
    alert('Fehler: '+e.message);
  }
  btn.textContent = 'ALLE SYMBOLE VERGLEICHEN'; btn.disabled = false;
}

function renderMultiBacktest(d) {
  const symbols = Object.keys(d);
  const rows = symbols.map(sym => {
    const r = d[sym];
    if (r.error) return '<div class="ov-row" style="grid-template-columns:90px 1fr 70px 70px 70px 70px 70px"><span>'+esc(sym)+'</span><span style="color:var(--red)">'+esc(r.error)+'</span></div>';
    const pc  = r.total_pnl >= 0 ? 'var(--signal)' : 'var(--red)';
    const sc  = r.sharpe >= 1.5 ? 'var(--signal)' : r.sharpe >= 1 ? 'var(--dca)' : 'var(--red)';
    return '<div class="ov-row" style="grid-template-columns:90px 1fr 70px 70px 70px 70px 70px">' +
      '<span style="font-weight:600">'+sym.replace('USDT','')+'</span>' +
      '<span style="font-size:10px;color:var(--muted)">'+r.trades+' Trades | '+r.win_rate+'% Win</span>' +
      '<span style="color:'+pc+';font-weight:600">'+(r.total_pnl>=0?'+':'')+r.total_pnl+'</span>' +
      '<span style="color:'+sc+'">'+r.sharpe+'</span>' +
      '<span style="color:var(--red)">'+r.max_drawdown+'%</span>' +
      '<span style="color:var(--muted);font-size:10px">-'+r.total_fees+'</span>' +
      '<span style="color:'+(r.final_equity>=1000?'var(--signal)':'var(--red)')+'">'+r.final_equity+'</span>' +
    '</div>';
  });
  document.getElementById('bt-multi-rows').innerHTML = rows.join('');
}

// -- TRADE TIMING ANALYSE ------------------------------------
async function loadTradeTiming() {
  try {
    const r = await fetch('/api/trade_timing', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d = await r.json();
    renderTradeTiming(d);
  } catch(e) {}
}

function renderTradeTiming(data) {
  const chart  = document.getElementById('timing-chart');
  const labels = document.getElementById('timing-labels');
  if (!chart || !data.length) return;
  const maxAbs = Math.max(...data.map(h => Math.abs(h.avg_pnl)), 0.001);
  chart.innerHTML  = data.map(h => {
    const h80 = Math.max(8, Math.abs(h.avg_pnl) / maxAbs * 76);
    const col = h.avg_pnl > 0 ? 'var(--signal)' : h.avg_pnl < 0 ? 'var(--red)' : 'var(--dim)';
    return '<div title="'+h.hour+':00 | '+h.count+' Trades | Avg: '+(h.avg_pnl>=0?'+':'')+h.avg_pnl+' | Win: '+h.win_rate+'%"' +
      ' style="flex:1;height:'+h80+'px;background:'+col+';border-radius:2px 2px 0 0;min-width:4px;cursor:pointer"></div>';
  }).join('');
  labels.innerHTML = data.filter((_,i)=>i%4===0).map(h =>
    '<span style="flex:1;font-size:9px;color:var(--muted);text-align:center">'+h.hour+'h</span>'
  ).join('');
}

async function checkCircuitBreaker() {
  try {
    const r = await fetch('/api/circuit_status', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d = await r.json();
    const badge = document.getElementById('circuit-badge');
    if (badge) badge.style.display = d.open ? 'block' : 'none';
  } catch(e) {}
}


let _gridInstances = {};
let _gridStates    = {};

function toggleAddGrid() {
  const f = document.getElementById('add-grid-form');
  f.style.display = f.style.display==='none' ? 'block' : 'none';
}

async function addGridInstance() {
  const get = id => document.getElementById(id)?.value||'';
  const body = {
    name:        get('ng-name')||('Grid '+Date.now()),
    symbol:      get('ng-sym')||'BTCUSDT',
    grid_count:  parseInt(get('ng-n'))||10,
    investment:  parseFloat(get('ng-inv'))||100,
    step_size:   parseFloat(get('ng-step'))||0,
    api_key:     get('ng-key'),
    api_secret:  get('ng-sec'),
    passphrase:  get('ng-pass'),
  };
  if (!body.api_key) { alert('Bitte API Key eintragen.'); return; }
  const r = await fetch('/api/grid/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d = await r.json();
  if (d.status==='ok') {
    toggleAddGrid();
    await loadGridInstances();
  } else { alert('Fehler: '+d.msg); }
}

async function loadGridInstances() {
  try {
    const r = await fetch('/api/grid/instances',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const d = await r.json();
    _gridInstances = {};
    (d.instances||[]).forEach(i => _gridInstances[i.id]=i);
    _gridStates    = d.states||{};
    renderGridInstances();
  } catch(e) {}
}

function renderGridInstances() {
  const list = document.getElementById('grid-instances-list');
  const ids  = Object.keys(_gridInstances);
  if (!ids.length) {
    list.innerHTML = '<div style="font-size:11px;color:var(--muted)">Noch keine weiteren Instanzen.</div>';
    return;
  }
  list.innerHTML = ids.map(id => {
    const cfg = _gridInstances[id];
    const st  = _gridStates[id] || {};
    const status = st.status||'STOPPED';
    const stCol  = status==='RUNNING'?'var(--signal)':status==='STOPPED'?'var(--muted)':'var(--dca)';
    const running = status==='RUNNING'||status==='STARTING';
    const pnl     = parseFloat(st.pnl||0);
    const orders  = st.grid_orders||[];
    const mid     = ((st.upper||0)+(st.lower||0))/2;
    return '<div class="card" style="margin-bottom:10px;padding:14px">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">' +
        '<div>' +
          '<div style="font-size:12px;font-weight:700;color:var(--grid)">'+(cfg.name||id)+'</div>' +
          '<div style="font-size:10px;color:var(--muted)">'+(st.symbol||cfg.symbol)+' | '+(st.lower||0)+' - '+(st.upper||0)+'</div>' +
        '</div>' +
        '<div style="display:flex;gap:8px;align-items:center">' +
          '<span style="font-size:10px;font-weight:700;color:'+stCol+'">'+status+'</span>' +
          '<button onclick="toggleGridInst(\''+id+'\')" class="btn '+(running?'btn-stop':'btn-start')+'" style="--accent:var(--grid);padding:5px 12px;font-size:10px">'+(running?'STOP':'START')+'</button>' +
          '<button onclick="removeGridInst(\''+id+'\')" style="background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.2);color:var(--red);font-family:inherit;font-size:10px;padding:5px 10px;border-radius:4px;cursor:pointer">X</button>' +
        '</div>' +
      '</div>' +
      '<div class="grid g4" style="margin-bottom:8px">' +
        '<div class="card" style="padding:8px"><div class="card-label">Balance</div><div class="card-value blue" style="font-size:14px">'+(st.balance||0).toFixed(2)+'</div></div>' +
        '<div class="card" style="padding:8px"><div class="card-label">PnL</div><div class="card-value '+pnlColor(pnl)+'" style="font-size:14px">'+(pnl>=0?'+':'')+pnl.toFixed(4)+'</div></div>' +
        '<div class="card" style="padding:8px"><div class="card-label">Gefuellt</div><div class="card-value white" style="font-size:14px">'+(st.filled||0)+'</div></div>' +
        '<div class="card" style="padding:8px"><div class="card-label">Levels</div><div class="card-value white" style="font-size:14px">'+(orders.length||cfg.grid_count||0)+'</div></div>' +
      '</div>' +
      (orders.length ? '<div style="display:flex;gap:3px;flex-wrap:wrap;margin-bottom:6px">'+
        orders.map(o=>'<div title="'+o.price+'" style="width:14px;height:14px;border-radius:2px;background:'+(o.filled?(o.side==='BUY'?'var(--signal)':'var(--red)'):'var(--dim)')+'"></div>').join('') +
      '</div>' : '') +
      (st.logs&&st.logs.length?'<div style="max-height:80px;overflow-y:auto;font-size:10px;color:var(--muted)">'+
        (st.logs||[]).slice(0,5).map(l=>'<div>'+l.t+' '+l.m+'</div>').join('') +
      '</div>':'') +
    '</div>';
  }).join('');
}

async function toggleGridInst(id) {
  const st = (_gridStates[id]||{}).status||'STOPPED';
  const running = st==='RUNNING'||st==='STARTING';
  const path = running ? '/api/grid/stop_instance' : '/api/grid/start_instance';
  const r = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  const d = await r.json();
  if (d.status!=='ok') alert(d.msg||'Fehler');
  setTimeout(loadGridInstances, 1000);
}

async function removeGridInst(id) {
  if (!confirm('Instanz loeschen?')) return;
  await fetch('/api/grid/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
  await loadGridInstances();
}

// -- DEFI YIELDS (DefiLlama) ----------------------------------
let _yieldsData = [];

async function loadYields() {
  const chain = document.getElementById('yields-chain')?.value || '';
  document.getElementById('yields-rows').innerHTML =
    '<div style="padding:20px;color:var(--muted);font-size:11px">Lade DeFi Yields von DefiLlama...</div>';
  try {
    // Stable coins + crypto yields from DefiLlama
    const url = chain
      ? 'https://yields.llama.fi/pools'
      : 'https://yields.llama.fi/pools';
    const r = await fetch(url);
    const d = await r.json();
    let pools = (d.data || []).filter(p => p.apy > 0 && p.tvlUsd > 100000);
    if (chain) pools = pools.filter(p => p.chain === chain);
    // Sort by APY descending, take top 100
    _yieldsData = pools.sort((a,b) => b.apy - a.apy).slice(0, 100);
    filterYields();
  } catch(e) {
    document.getElementById('yields-rows').innerHTML =
      '<div style="padding:20px;color:var(--red);font-size:11px">Fehler beim Laden. CORS oder Netzwerkproblem.</div>';
  }
}

function filterYields() {
  const minApy = parseFloat(document.getElementById('yields-min-apy')?.value || 0);
  const data = _yieldsData.filter(p => p.apy >= minApy);
  if (!data.length) {
    document.getElementById('yields-rows').innerHTML =
      '<div style="padding:20px;color:var(--muted);font-size:11px">Keine Ergebnisse fuer diesen Filter.</div>';
    return;
  }
  document.getElementById('yields-rows').innerHTML = data.slice(0,50).map(p => {
    const apy     = p.apy.toFixed(2);
    const apyCol  = p.apy >= 20 ? 'var(--signal)' : p.apy >= 10 ? 'var(--dca)' : 'var(--text)';
    const tvl     = p.tvlUsd >= 1e9 ? (p.tvlUsd/1e9).toFixed(1)+'B' :
                    p.tvlUsd >= 1e6 ? (p.tvlUsd/1e6).toFixed(1)+'M' :
                    (p.tvlUsd/1e3).toFixed(0)+'K';
    const risk    = p.ilRisk === 'yes' ? 'IL-Risiko' : p.stablecoin ? 'Stablecoin' : 'Normal';
    const riskCol = p.ilRisk === 'yes' ? 'var(--red)' : p.stablecoin ? 'var(--signal)' : 'var(--muted)';
    const symbols = (p.symbol||'').replace('_','/').slice(0,20);
    return '<div class="ov-row" style="grid-template-columns:1fr 80px 80px 1fr 80px 80px">' +
      '<span style="font-size:10px"><span style="font-weight:600;color:var(--white)">'+(p.project||'').slice(0,20)+'</span></span>' +
      '<span style="font-size:10px;color:var(--muted)">'+(p.chain||'').slice(0,10)+'</span>' +
      '<span style="font-weight:700;color:'+apyCol+'">'+apy+'%</span>' +
      '<span style="font-size:10px;color:var(--muted)">'+symbols+'</span>' +
      '<span style="font-size:10px;color:var(--muted)">'+tvl+'</span>' +
      '<span style="font-size:10px;color:'+riskCol+'">'+risk+'</span>' +
    '</div>';
  }).join('');
}


let _kalData   = [];
let _kalFilter = 'all';

async function loadKalender(refresh) {
  document.getElementById('kal-rows').innerHTML = '<div style="padding:20px;color:var(--muted);font-size:11px">Lade...</div>';
  try {
    const r = await fetch('/api/kalender',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({refresh:!!refresh})});
    const d = await r.json();
    _kalData = d.events||[];
    const bo = document.getElementById('kal-blackout-info');
    bo.style.display = d.blackout ? 'block' : 'none';
    renderKalender();
  } catch(e) {
    document.getElementById('kal-rows').innerHTML = '<div style="padding:20px;color:var(--red);font-size:11px">Fehler. Finnhub API Key konfiguriert?</div>';
  }
}

function filterKal(f) {
  _kalFilter = f;
  ['all','us','eu'].forEach(k => {
    const btn = document.getElementById('kf-'+k);
    if (btn) btn.style.opacity = (f.toLowerCase()===k||f.toUpperCase()===k)?'1':'0.4';
  });
  renderKalender();
}

const COUNTRY_NAMES = {
  US:'USA',DE:'DEU',EU:'EUR',FR:'FRA',GB:'GBR',JP:'JPN',
  CN:'CHN',CA:'CAN',AU:'AUS',IT:'ITA',ES:'ESP',
};
const EU_COUNTRIES = ['DE','EU','FR','IT','ES','NL','BE','AT','PT','PL','SE','DK','FI','NO','CH'];

function renderKalender() {
  let data = _kalData;
  if (_kalFilter==='US')  data = data.filter(e=>e.country==='US');
  if (_kalFilter==='EU')  data = data.filter(e=>EU_COUNTRIES.includes(e.country));
  if (!data.length) {
    document.getElementById('kal-rows').innerHTML =
      '<div style="padding:20px;color:var(--muted);font-size:11px">Keine Events fuer diesen Filter.</div>';
    return;
  }
  document.getElementById('kal-rows').innerHTML = data.map(e => {
    const isUS  = e.country==='US';
    const impactCol = e.impact==='high'&&isUS  ? 'var(--red)' :
                      e.impact==='high'        ? 'var(--dca)' : 'var(--muted)';
    const impactLbl = e.impact==='high'&&isUS  ? 'HOCH [US]' :
                      e.impact==='high'        ? 'HOCH' : 'MITTEL';
    const cname  = esc(COUNTRY_NAMES[e.country]||e.country||'?');
    const actual = e.actual  != null ? esc(String(e.actual))  : '-';
    const est    = e.estimate!= null ? esc(String(e.estimate)): '-';
    return '<div class="ov-row" style="grid-template-columns:70px 50px 1fr 80px 80px 80px">' +
      '<span style="color:var(--muted);font-size:10px">'+esc(e.time)+'</span>' +
      '<span style="font-size:10px;font-weight:600;color:'+(isUS?'var(--red)':'var(--blue)')+'">'+cname+'</span>' +
      '<span style="font-size:11px">'+esc(e.event)+'</span>' +
      '<span style="font-size:10px;font-weight:700;color:'+impactCol+'">'+impactLbl+'</span>' +
      '<span style="font-size:10px;color:var(--muted)">'+actual+'</span>' +
      '<span style="font-size:10px;color:var(--dim)">'+est+'</span>' +
    '</div>';
  }).join('');
}


async function loadFGHistory() {
  try {
    const r = await fetch('/api/fg_history');
    const d = await r.json();
    if (!d.length) return;
    renderFGChart(d);
  } catch(e) {}
}

function renderFGChart(data) {
  const svg = document.getElementById('fg-chart');
  const lbl = document.getElementById('fg-labels');
  const cur = document.getElementById('fg-current');
  if (!svg || !data.length) return;
  const W=760, H=52, pad=2;
  const latest = data[data.length-1];
  const v = latest.value;
  const col = v<25?'var(--red)':v<50?'var(--dca)':v<75?'var(--signal)':'#a78bfa';
  cur.textContent = v + ' - ' + latest.label;
  cur.style.color = col;
  const scX = i => (i / (data.length-1)) * (W-pad*2) + pad;
  const scY = v => H - pad - (v/100)*(H-pad*2);
  const pts = data.map((d,i) => scX(i)+','+scY(d.value)).join(' ');
  const fillPts = pts + ' ' + scX(data.length-1)+','+(H-pad) + ' '+pad+','+(H-pad);
  svg.innerHTML =
    '<defs><linearGradient id="fggrad" x1="0" y1="0" x2="1" y2="0">' +
    data.map((d,i)=>{
      const pct=(i/(data.length-1)*100).toFixed(0)+'%';
      const c=d.value<25?'#f87171':d.value<50?'#fbbf24':d.value<75?'#00d68f':'#a78bfa';
      return '<stop offset="'+pct+'" stop-color="'+c+'"/>';
    }).join('') + '</linearGradient></defs>' +
    '<polygon points="'+fillPts+'" fill="url(#fggrad)" opacity="0.15"/>' +
    '<polyline points="'+pts+'" stroke="url(#fggrad)" fill="none" stroke-width="2"/>' +
    data.filter((_,i)=>i%5===0).map((d,idx,arr)=>{
      const i = data.indexOf(arr[idx]);
      return '<line x1="'+scX(i)+'" y1="'+(H-pad-2)+'" x2="'+scX(i)+'" y2="'+(H-pad)+'" stroke="var(--border)" stroke-width="1"/>';
    }).join('');
  lbl.innerHTML = data.filter((_,i)=>i===0||i===Math.floor(data.length/2)||i===data.length-1)
    .map(d=>'<span>'+d.date+'</span>').join('');
}

// -- BACKTESTING ----------------------------------------------
async function runBacktest() {
  const btn = document.getElementById('bt-run-btn');
  btn.textContent = 'Berechne...'; btn.disabled = true;
  document.getElementById('bt-result').style.display = 'none';
  document.getElementById('bt-error').style.display  = 'none';
  try {
    const r = await fetch('/api/backtest', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({
        symbol:      document.getElementById('bt-symbol').value,
        period_days: parseInt(document.getElementById('bt-days').value),
        leverage:    parseInt(document.getElementById('bt-lever').value)||3,
        threshold:    parseInt(document.getElementById('bt-thresh').value)||2,
        sl_pct:       parseFloat(document.getElementById('bt-sl').value)/100||0.01,
        tp_pct:       parseFloat(document.getElementById('bt-tp').value)/100||0.02,
        pos_pct:      parseFloat(document.getElementById('bt-pos').value)||10,
        walk_forward: document.getElementById('bt-walkforward')?.checked||false,
      })
    });
    const d = await r.json();
    if (d.error) {
      document.getElementById('bt-error').textContent = 'Fehler: '+d.error;
      document.getElementById('bt-error').style.display = 'block';
    } else {
      renderBacktest(d);
      document.getElementById('bt-result').style.display = 'block';
    }
  } catch(e) {
    document.getElementById('bt-error').textContent = 'Verbindungsfehler: '+e.message;
    document.getElementById('bt-error').style.display = 'block';
  }
  btn.textContent = 'BACKTEST STARTEN'; btn.disabled = false;
}

function renderBacktest(d) {
  if (d.trades === 0) {
    document.getElementById('bt-result').style.display = 'block';
    document.getElementById('bt-stats').innerHTML =
      '<div class="card" style="grid-column:1/-1;padding:14px">' +
        '<div style="font-size:12px;color:var(--dca);font-weight:700;margin-bottom:6px">0 Trades gefunden</div>' +
        '<div style="font-size:11px;color:var(--muted);line-height:1.8">' +
          'Die Signal-Schwelle ist zu hoch fuer die verfuegbaren Indikatoren.<br>' +
          'Versuche: Schwelle auf 1 oder 2 setzen, oder einen laengeren Zeitraum waehlen.' +
        '</div>' +
      '</div>';
    document.getElementById('bt-trades').innerHTML = '';
    document.getElementById('bt-final').textContent = '';
    ['bt-sharpe','bt-fees'].forEach(id => { const e=document.getElementById(id); if(e) e.textContent='-'; });
    const wfEl = document.getElementById('bt-walkforward-info');
    if (wfEl) wfEl.style.display = 'none';
    return;
  }
  const pnlCol = d.total_pnl >= 0 ? 'var(--signal)' : 'var(--red)';
  const wrCol  = d.win_rate >= 55 ? 'var(--signal)' : d.win_rate >= 45 ? 'var(--dca)' : 'var(--red)';
  const ddCol  = d.max_drawdown <= 10 ? 'var(--signal)' : d.max_drawdown <= 20 ? 'var(--dca)' : 'var(--red)';
  document.getElementById('bt-stats').innerHTML = [
    ['Trades gesamt', d.trades, 'var(--white)'],
    ['Win Rate', d.win_rate+'%', wrCol],
    ['PnL gesamt', (d.total_pnl>=0?'+':'')+d.total_pnl+' USDT', pnlCol],
    ['Max Drawdown', d.max_drawdown+'%', ddCol],
  ].map(([l,v,c])=>
    '<div class="card"><div class="card-label">'+l+'</div>' +
    '<div class="card-value" style="color:'+c+'">'+v+'</div></div>'
  ).join('');
  // Sharpe + Fees
  const shEl = document.getElementById('bt-sharpe');
  if (shEl) { shEl.textContent = d.sharpe||'0.00'; shEl.style.color = d.sharpe>=1.5?'var(--signal)':d.sharpe>=1?'var(--dca)':'var(--red)'; }
  const feEl = document.getElementById('bt-fees');
  if (feEl) feEl.textContent = '-'+(d.total_fees||0).toFixed(4)+' USDT';
  // Walk-Forward info
  const wfEl = document.getElementById('bt-walkforward-info');
  if (wfEl) {
    if (d.walk_forward) {
      wfEl.style.display = 'block';
      wfEl.textContent = 'Walk-Forward: 70% Training / 30% Test ('+d.test_candles+' Test-Kerzen). Ergebnis auf ungesehenen Daten.';
    } else { wfEl.style.display = 'none'; }
  }
  document.getElementById('bt-final').textContent = 'Endkapital: '+d.final_equity+' USDT';
  document.getElementById('bt-final').style.color = d.final_equity >= 1000 ? 'var(--signal)':'var(--red)';
  sparkline('bt-spark', d.equity_curve);
  document.getElementById('bt-trades').innerHTML = d.trade_list.slice().reverse().map(t => {
    const pc = t.result==='WIN'?'var(--signal)':'var(--red)';
    const sc = t.side==='LONG'?'var(--signal)':'var(--red)';
    return '<div class="ov-row" style="grid-template-columns:70px 80px 80px 70px 70px 60px">' +
      '<span style="color:'+sc+';font-weight:600">'+t.side+'</span>' +
      '<span>'+t.entry+'</span>' +
      '<span>'+t.exit+'</span>' +
      '<span style="color:'+pc+';font-weight:600">'+(t.pnl>=0?'+':'')+t.pnl+'</span>' +
      '<span style="color:var(--muted);font-size:10px">-'+(t.fee||0).toFixed(4)+'</span>' +
      '<span style="font-size:10px;color:'+pc+'">'+t.result+'</span></div>';
  }).join('');
}

// -- ALERTS ---------------------------------------------------
let _alertRules = [];

async function loadAlerts() {
  try {
    const r = await fetch('/api/alerts/get', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    _alertRules = await r.json();
    renderAlerts();
  } catch(e) {}
}

function renderAlerts() {
  const list = document.getElementById('al-list');
  document.getElementById('al-count').textContent = _alertRules.length + ' Alert(s)';
  if (!_alertRules.length) {
    list.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:11px">Keine Alerts konfiguriert.</div>';
    return;
  }
  const TYPE_LABELS = {
    price_above:'Preis UEBER', price_below:'Preis UNTER',
    pnl_below:'PnL unter', funding_above:'Funding UEBER'
  };
  list.innerHTML = _alertRules.map((a,i) => {
    const status = a.triggered ? 'AUSGELOEST' : (a.enabled ? 'AKTIV' : 'DEAKTIVIERT');
    const sCol   = a.triggered ? 'var(--dca)' : (a.enabled ? 'var(--signal)' : 'var(--muted)');
    const sym    = a.symbol ? esc(a.symbol)+' ' : '';
    return '<div style="display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--border)">' +
      '<div style="flex:1">' +
        '<div style="font-size:11px;font-weight:600;color:var(--white)">'+esc(a.name||'Alert '+i)+'</div>' +
        '<div style="font-size:10px;color:var(--muted);margin-top:2px">'+
          esc(TYPE_LABELS[a.type]||a.type)+' '+sym+esc(a.value)+'</div>' +
      '</div>' +
      '<span style="font-size:10px;font-weight:700;color:'+sCol+'">'+status+'</span>' +
      '<button onclick="toggleAlert('+i+')" style="background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:4px 10px;border-radius:4px;cursor:pointer">' +
        (a.enabled?'AUS':'EIN')+'</button>' +
      '<button onclick="deleteAlert('+i+')" style="background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.2);color:var(--red);font-family:inherit;font-size:10px;padding:4px 10px;border-radius:4px;cursor:pointer">X</button>' +
    '</div>';
  }).join('');
}

async function addAlert() {
  const type  = document.getElementById('al-type').value;
  const sym   = document.getElementById('al-symbol')?.value || '';
  const val   = parseFloat(document.getElementById('al-value').value);
  const name  = document.getElementById('al-name').value || (type+'_'+sym+'_'+val);
  if (!val && val !== 0) { alert('Bitte einen Wert eingeben.'); return; }
  _alertRules.push({
    id:'a'+Date.now(), name, type,
    symbol: sym, value: val,
    enabled: true, triggered: false
  });
  await saveAlerts();
  document.getElementById('al-value').value = '';
  document.getElementById('al-name').value  = '';
}

function toggleAlert(i) {
  _alertRules[i].enabled = !_alertRules[i].enabled;
  _alertRules[i].triggered = false;
  saveAlerts();
}

function deleteAlert(i) {
  if (!confirm('Alert loeschen?')) return;
  _alertRules.splice(i, 1);
  saveAlerts();
}

async function saveAlerts() {
  await fetch('/api/alerts/save', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({alerts: _alertRules})
  });
  renderAlerts();
}

function updateAlertForm() {
  const type = document.getElementById('al-type').value;
  const wrap = document.getElementById('al-sym-wrap');
  wrap.style.display = type === 'pnl_below' ? 'none' : 'block';
}

async function loadAlertLog() {
  try {
    const r = await fetch('/api/alert_log');
    const d = await r.json();
    document.getElementById('al-log').innerHTML = d.length
      ? d.map(e=>'<div class="log-entry"><span class="lt">'+esc(e.t)+'</span><span style="color:var(--dca)">ALERT</span><span style="color:#aaa">'+esc(e.m)+'</span></div>').join('')
      : '<div style="padding:12px;color:var(--muted);font-size:11px">Noch keine Alerts ausgeloest.</div>';
  } catch(e) {}
}


let _tradesData = [];

async function loadMarket() {
  document.getElementById('markt-rows').innerHTML =
    '<div style="padding:20px;color:var(--muted);font-size:11px">Lade...</div>';
  try {
    const r = await fetch('/api/market');
    const d = await r.json();
    renderMarket(d);
    document.getElementById('markt-update').textContent =
      'Stand: ' + new Date().toLocaleTimeString('de-DE');
  } catch(e) {
    document.getElementById('markt-rows').innerHTML =
      '<div style="padding:20px;color:var(--red);font-size:11px">Fehler.</div>';
  }
}

function renderMarket(data) {
  if (!data || !data.length) return;
  const maxVol = Math.max(...data.map(d => d.vol24||0), 1);
  document.getElementById('markt-rows').innerHTML = data.map(d => {
    const chg = parseFloat(d.change24||0);
    const col = chg>0?'var(--signal)':chg<0?'var(--red)':'var(--muted)';
    const frc = d.funding>0.03?'var(--red)':d.funding<-0.03?'var(--signal)':'var(--muted)';
    const vp  = Math.min((d.vol24/maxVol)*100,100);
    const pr  = d.price>1000?d.price.toLocaleString('de-DE',{maximumFractionDigits:2}):d.price.toFixed(4);
    return '<div class="ov-row" style="grid-template-columns:80px 1fr 80px 80px 80px 80px 120px">' +
      '<span style="font-weight:700;color:var(--white)">'+d.symbol+'</span>' +
      '<span style="color:var(--blue)">'+pr+'</span>' +
      '<span style="color:'+col+';font-weight:600">'+(chg>=0?'+':'')+chg.toFixed(2)+'%</span>' +
      '<span style="color:var(--muted);font-size:10px">'+d.high24.toFixed(d.high24>100?1:4)+'</span>' +
      '<span style="color:var(--muted);font-size:10px">'+d.low24.toFixed(d.low24>100?1:4)+'</span>' +
      '<span style="color:'+frc+'">'+d.funding.toFixed(4)+'%</span>' +
      '<div style="display:flex;align-items:center;gap:6px">' +
        '<div style="flex:1;background:var(--dim);border-radius:2px;height:4px">' +
          '<div style="width:'+vp+'%;height:100%;background:var(--grid);border-radius:2px"></div></div>' +
        '<span style="font-size:10px;color:var(--muted);min-width:35px;text-align:right">'+d.vol24.toFixed(0)+'M</span>' +
      '</div></div>';
  }).join('');
}

// -- TRADES ---------------------------------------------------
async function loadTrades() {
  document.getElementById('trades-rows').innerHTML =
    '<div style="padding:20px;color:var(--muted);font-size:11px">Lade...</div>';
  try {
    const r = await fetch('/api/trades');
    _tradesData = await r.json();
    renderTrades();
  } catch(e) {
    document.getElementById('trades-rows').innerHTML =
      '<div style="padding:20px;color:var(--red);font-size:11px">Fehler: '+e.message+'</div>';
  }
}

function renderTrades() {
  const filter = document.getElementById('trades-filter')?.value||'all';
  const data   = filter==='all'?_tradesData:_tradesData.filter(t=>t.bot===filter);
  if (!data.length) {
    document.getElementById('trades-rows').innerHTML =
      '<div style="padding:20px;color:var(--muted);font-size:11px">Keine Trades gefunden.</div>';
    document.getElementById('trades-summary').innerHTML=''; return;
  }
  const wins=data.filter(t=>t.pnl>0).length;
  const losses=data.filter(t=>t.pnl<0).length;
  const totPnl=data.reduce((s,t)=>s+t.pnl,0);
  const totFee=data.reduce((s,t)=>s+t.fee,0);
  document.getElementById('trades-summary').innerHTML=[
    ['Trades',data.length],
    ['Gewinne',wins+' ('+(data.length?Math.round(wins/data.length*100):0)+'%)'],
    ['Verluste',losses],
    ['PnL',(totPnl>=0?'+':'')+totPnl.toFixed(4)+' USDT'],
    ['Gebuehren','-'+totFee.toFixed(4)+' USDT'],
  ].map(([l,v])=>'<div class="card" style="padding:8px 12px"><div class="card-label">'+l+'</div><div style="font-size:13px;font-weight:700;color:var(--white);margin-top:2px">'+v+'</div></div>').join('');
  const BC={signal:'var(--signal)',grid:'var(--grid)',funding:'var(--funding)',dca:'var(--dca)'};
  document.getElementById('trades-rows').innerHTML=data.slice(0,100).map(t=>{
    const pc=t.pnl>0?'var(--signal)':t.pnl<0?'var(--red)':'var(--muted)';
    const side=t.side==='buy'?'LONG':'SHORT';
    const sc=side==='LONG'?'var(--signal)':'var(--red)';
    return '<div class="ov-row" style="grid-template-columns:90px 70px 60px 60px 80px 60px 80px 70px">' +
      '<span style="font-size:10px;color:var(--muted)">'+t.time_str+'</span>' +
      '<span style="color:'+(BC[t.bot]||'#fff')+';font-size:10px">'+t.bot+'</span>' +
      '<span style="font-weight:600">'+t.symbol+'</span>' +
      '<span style="color:'+sc+';font-weight:600;font-size:10px">'+side+'</span>' +
      '<span>'+t.price.toFixed(t.price>100?2:4)+'</span>' +
      '<span style="color:var(--muted)">'+t.size+'</span>' +
      '<span style="color:'+pc+';font-weight:600">'+(t.pnl>=0?'+':'')+t.pnl.toFixed(4)+'</span>' +
      '<span style="color:var(--muted);font-size:10px">-'+t.fee.toFixed(4)+'</span></div>';
  }).join('');
}

// -- POSITIONEN (Overview) ------------------------------------
async function loadPositions() {
  try {
    const r=await fetch('/api/positions'); const d=await r.json();
    const wrap=document.getElementById('ov-positions-wrap');
    const box=document.getElementById('ov-positions');
    if (!d.length){wrap.style.display='none';return;}
    wrap.style.display='block';
    const BC={signal:'var(--signal)',grid:'var(--grid)',funding:'var(--funding)',dca:'var(--dca)'};
    box.innerHTML=d.map(p=>{
      const sc=p.side==='long'?'var(--signal)':'var(--red)';
      const uc=p.upnl>=0?'var(--signal)':'var(--red)';
      return '<div class="ov-row" style="grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr 1fr">' +
        '<span style="color:'+(BC[p.bot]||'#fff')+';font-size:10px;font-weight:700">'+p.bot+'</span>' +
        '<span style="font-weight:700">'+p.symbol+'</span>' +
        '<span style="color:'+sc+';font-weight:700;font-size:10px">'+p.side.toUpperCase()+'</span>' +
        '<span>'+p.size+'</span>' +
        '<span style="color:var(--muted)">'+p.entry.toFixed(2)+'</span>' +
        '<span style="color:'+uc+';font-weight:600">'+(p.upnl>=0?'+':'')+p.upnl.toFixed(4)+'</span>' +
        '<span style="color:var(--muted);font-size:10px">'+p.lever+'x</span></div>';
    }).join('');
  } catch(e){}
}

// -- PER-BOT PRESETS ------------------------------------------
// 5 Stufen: passiv -> defensiv -> standard -> offensiv -> aggressiv.
// Aggressiver = mehr Trades: Signal niedrigere Schwelle, Grid mehr Levels (=kleinere Schritte),
// DCA kuerzeres Intervall. Nur Formular-Vorbelegung - du kannst danach alles feinjustieren.
const BOT_PRESETS={
  signal:{
    passiv:   {lever:1, usdt:15, thresh:5, trend:false},
    defensiv: {lever:2, usdt:20, thresh:4, trend:false},
    standard: {lever:3, usdt:30, thresh:3, trend:false},
    offensiv: {lever:5, usdt:40, thresh:2, trend:true},
    aggressiv:{lever:8, usdt:60, thresh:1, trend:true},
  },
  grid:{
    passiv:   {n:6,  inv:50},
    defensiv: {n:8,  inv:80},
    standard: {n:12, inv:100},
    offensiv: {n:20, inv:200},
    aggressiv:{n:30, inv:300},
  },
  dca:{
    passiv:   {hrs:336, amt:15},
    defensiv: {hrs:168, amt:20},
    standard: {hrs:24,  amt:30},
    offensiv: {hrs:12,  amt:40},
    aggressiv:{hrs:6,   amt:60},
  },
};

function applyBotPreset(botId,level) {
  const p=BOT_PRESETS[botId]?.[level]; if(!p) return;
  const set=(id,v)=>{const el=document.getElementById(id);if(el&&v!==undefined)el.value=v;};
  if(botId==='signal'){set('sig-lever',p.lever);set('sig-usdt',p.usdt);set('sig-thresh',p.thresh);
    const tr=document.getElementById('sig-f-trend'); if(tr&&p.trend!==undefined) tr.checked=p.trend;}
  else if(botId==='grid'){set('grd-n',p.n);set('grd-inv',p.inv);}
  else if(botId==='dca'){set('dca-hrs',p.hrs);set('dca-amt',p.amt);}
  const labels={passiv:'Passiv',defensiv:'Defensiv',standard:'Standard',offensiv:'Offensiv',aggressiv:'Aggressiv'};
  const desc=document.getElementById('preset-desc');
  if(desc) desc.textContent=botId.toUpperCase()+' - '+(labels[level]||level)+' geladen.';
}

// -- TRADINGVIEW LAZY LOADER -----------------------------------
function loadTVScript(cb) {
  if (window.LightweightCharts) { cb(); return; }
  const s = document.createElement('script');
  s.src = 'https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js';
  s.onload  = cb;
  s.onerror = () => { document.getElementById('tv-chart').innerHTML =
    '<div style="color:var(--muted);font-size:11px;padding:20px;text-align:center">Chart konnte nicht geladen werden (kein Internet?)</div>'; };
  document.head.appendChild(s);
}

function switchTab(id) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const panel = document.getElementById('panel-' + id);
  if (panel) panel.classList.add('active');
  document.querySelectorAll('.tab').forEach(tb => {
    if (tb.dataset.tab === id || tb.getAttribute('data-bot') === id)
      tb.classList.add('active');
  });
  activePanel = id;
  if (id === 'settings')  fillSettingsForm();  // holt /api/config selbst - unabhaengig von lastState (Fix: nach F5 war das Formular sonst leer)
  if (id === 'signal')    loadSignalSettings();
  if (id === 'grid')      loadGridSettings();
  if (id === 'syslog')    loadSyslog();
  if (id === 'overview')  { loadPositions(); loadFGHistory(); }
  if (id === 'markt')     { loadMarket(); loadKalender(false); }
  if (id === 'alerts')    { loadAlerts(); loadAlertLog(); }
  if (id === 'grid')      loadGridInstances();
  if (id === 'grid') {
    setTimeout(() => {
      loadTVScript(() => {
        initTVChart();
        const sym = lastState?.bots?.grid?.symbol || 'BTCUSDT';
        loadTVChart(sym);
      });
    }, 50);
  }
}

function toggle(id) {
  const el = document.getElementById(id);
  el.classList.toggle('open');
}

function dotClass(status) {
  if (!status) return 'dot-stop';
  const s = status.toUpperCase();
  if (s === 'RUNNING') return 'dot-run';
  if (s === 'STARTING') return 'dot-start';
  if (s === 'PAUSED') return 'dot-pause';
  return 'dot-stop';
}

function statusClass(status) {
  if (!status) return 's-stopped';
  const s = status.toUpperCase();
  if (s === 'RUNNING') return 's-running';
  if (s === 'STARTING' || s === 'STOPPING') return 's-starting';
  if (s === 'PAUSED') return 's-paused';
  return 's-stopped';
}

function pnlColor(v) { return parseFloat(v) >= 0 ? 'green' : 'red'; }

// Laufzeit-Formatierung: started_at (Server-Epoch, Sek.) -> "3d 4h" / "5h 12m" / "8m 03s" / "42s".
// Nutzt die Server-Uhr (state.server_now), damit eine falsch gehende Client-Uhr nicht stoert.
let _serverNow = 0, _serverNowAt = 0;   // letzter Server-Zeitstempel + lokale Ankunftszeit
function fmtUptime(startedAt) {
  if (!startedAt) return '';
  const nowSec = (_serverNow ? _serverNow + (Date.now()/1000 - _serverNowAt) : Date.now()/1000);
  let s = Math.max(0, Math.floor(nowSec - startedAt));
  const d = Math.floor(s/86400); s -= d*86400;
  const h = Math.floor(s/3600);  s -= h*3600;
  const m = Math.floor(s/60);    s -= m*60;
  const hms = String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0');
  return d > 0 ? d+'d '+hms : hms;   // z.B. "2d 05:13:42" oder "05:13:42"
}

// Laufzeit-Anzeigen jede Sekunde aktualisieren (fluessiger Timer, unabhaengig vom Poll-Intervall).
function tickUptimes() {
  const pu = document.getElementById('platform-uptime');
  if (pu) pu.textContent = (lastState && lastState.platform_started_at) ? '⏱ ' + fmtUptime(lastState.platform_started_at) : '';
  document.querySelectorAll('.ov-uptime[data-start]').forEach(el => {
    const st = parseFloat(el.dataset.start) || 0;
    el.textContent = st ? '⏱ ' + fmtUptime(st) : '';
  });
}
setInterval(tickUptimes, 1000);

// Signal-Konfig-Uebersicht: holt /api/config und zeigt kompakt, was eingestellt ist.
function loadSignalSettings() {
  fetch('/api/config').then(r=>r.json()).then(cfg => renderSignalSettings(cfg))
    .catch(e=>console.error('loadSignalSettings:', e));
}
function loadGridSettings() {
  fetch('/api/config').then(r=>r.json()).then(cfg => renderGridSettings(cfg))
    .catch(e=>console.error('loadGridSettings:', e));
}
function renderGridSettings(cfg) {
  const grid = document.getElementById('g-settings-grid');
  if (!grid) return;
  const g = (cfg.bots && cfg.bots.grid) || {};
  const rng = (g.upper_price && g.lower_price) ? g.lower_price+'–'+g.upper_price
            : (g.step_size>0 ? 'auto ('+t('cfg_step')+' '+g.step_size+')' : t('cfg_smartrange')+' '+(g.smart_range_hours ?? 24)+'h');
  const item = (k,v)=>`<div class="cfg-sum-item"><span class="cfg-sum-k">${k}</span><span class="cfg-sum-v">${v}</span></div>`;
  grid.innerHTML =
    item('Symbol',        g.symbol || 'BTCUSDT') +
    item(t('cfg_range'),  rng) +
    item(t('cfg_step'),   (g.step_size>0 ? g.step_size+' USDT' : '–')) +
    item(t('cfg_levels'), g.grid_count ?? 10) +
    item(t('cfg_budget'), (g.investment ?? 100)+' USDT') +
    item(t('cfg_leverage'), (g.leverage>0 ? g.leverage+'x' : t('cfg_account'))) +
    item('Stop-Loss',     (g.stop_loss_pct>0 ? (g.stop_loss_pct*100).toFixed(1)+'%' : t('cfg_off')));
}
function renderSignalSettings(cfg) {
  const grid = document.getElementById('s-settings-grid');
  const filt = document.getElementById('s-settings-filters');
  if (!grid || !filt) return;
  const s = (cfg.bots && cfg.bots.signal) || {};
  const toks = (s.tokens || []).map(x=>String(x).replace('USDT','')).join(', ') || '–';
  const budget = (s.budget_usdt && s.budget_usdt > 0) ? s.budget_usdt+' USDT' : t('cfg_full_bal');
  const stake  = (s.use_risk_pct !== false)
      ? (s.risk_pct ?? 3)+'%' : (s.usdt_per_trade ?? 30)+' USDT '+t('cfg_fixed');
  const sltp   = (s.use_atr_sl !== false)
      ? 'ATR ×'+(s.atr_sl_mult ?? 1.5)+' / ×'+(s.atr_tp_mult ?? 2.5)
      : ((s.stop_loss_pct ?? 0.01)*100).toFixed(1)+'% / '+((s.take_profit_pct ?? 0.02)*100).toFixed(1)+'%';
  const item = (k,v)=>`<div class="cfg-sum-item"><span class="cfg-sum-k">${k}</span><span class="cfg-sum-v">${v}</span></div>`;
  grid.innerHTML =
    item(t('cfg_tokens'),   toks) +
    item(t('cfg_leverage'), (s.leverage ?? 3)+'x') +
    item(t('cfg_budget'),   budget) +
    item(t('cfg_stake'),    stake) +
    item(t('cfg_sltp'),     sltp) +
    item(t('cfg_maxconc'),  s.max_concurrent ?? 2) +
    item(t('cfg_threshold'),s.signal_threshold ?? 3) +
    item(t('cfg_interval'), (s.check_interval ?? 30)+'s');
  // Score-Faktoren + Filter als Chips (an = gruen, aus = grau)
  const chips = [
    ['EMA','use_ema'],['RSI','use_rsi'],['MACD','use_macd'],['BB','use_bb'],
    ['Volume','use_volume'],['Funding','use_funding'],['Fear&Greed','use_fg'],
    ['News','use_news'],['Makro','use_macro'],['Trend','use_trend'],['Delta','use_delta'],
    ['Korrelation','use_correlation_filter'],['ADX','use_adx_filter'],['Orderbook','use_orderbook_signal'],
  ];
  filt.innerHTML = `<span class="cfg-sum-k" style="width:100%;margin-bottom:2px">${t('cfg_score_factors')}</span>` +
    chips.map(([lbl,key]) => {
      const on = key === 'use_trend' ? (s[key] === true) : (s[key] !== false);
      return `<span class="cfg-chip ${on?'on':'off'}">${on?'✓':'○'} ${lbl}</span>`;
    }).join('');
}

// System-Log-Viewer: laedt die letzten N Zeilen von /api/syslog, faerbt WARN/ERROR ein.
let _slogTimer = null;
function loadSyslog() {
  const n = document.getElementById('slog-lines')?.value || 400;
  fetch('/api/syslog?lines='+n).then(r=>r.json()).then(d => {
    const body = document.getElementById('slog-body');
    if (!body) return;
    const lines = d.lines || [];
    if (!lines.length) { body.innerHTML = '<span style="color:var(--muted)">(leer)</span>'; }
    else {
      body.innerHTML = lines.map(l => {
        const e = esc(l);
        if (/\bERROR\b/.test(l))              return '<span style="color:var(--red)">'+e+'</span>';
        if (/\bWARNING\b|\bWARN\b/.test(l))   return '<span style="color:var(--dca)">'+e+'</span>';
        return e;
      }).join('\n');
      body.scrollTop = body.scrollHeight;   // ans Ende (neueste Zeilen) scrollen
    }
    const meta = document.getElementById('slog-meta');
    if (meta) meta.textContent = (d.shown||0)+' / '+(d.count||0)+' '+t('slog_lines')+' · '+Math.round((d.size||0)/1024)+' KB';
    const upd = document.getElementById('slog-updated');
    if (upd) upd.textContent = d.now || '';
  }).catch(e=>{ const b=document.getElementById('slog-body'); if(b) b.textContent='Fehler: '+e; });
}
function toggleSyslogAuto() {
  const on = document.getElementById('slog-auto')?.checked;
  if (_slogTimer) { clearInterval(_slogTimer); _slogTimer = null; }
  if (on) { loadSyslog(); _slogTimer = setInterval(()=>{ if (activePanel==='syslog') loadSyslog(); }, 5000); }
}

function renderLog(entries, maxN=40) {
  if (!entries || !entries.length) return '<span style="color:var(--muted);font-size:11px">Kein Log</span>';
  return entries.slice(0,maxN).map(e =>
    `<div class="log-entry"><span class="lt">${esc(e.t)}</span><span class="ll ${esc(e.l)}">${esc(e.l)}</span><span style="color:#999;opacity:.8">${esc(e.m)}</span></div>`
  ).join('');
}

function renderMacro(events) {
  if (!events || !events.length)
    return '<span style="color:var(--dim);font-size:11px">Keine High-Impact Events in 48h</span>';
  const todayStr    = new Date().toISOString().slice(0,10);
  const tomorrowStr = new Date(Date.now()+86400000).toISOString().slice(0,10);
  return events.map(e => {
    let day = '';
    if (e.date === todayStr)    day = 'Heute ';
    else if (e.date === tomorrowStr) day = 'Morgen ';
    else if (e.date)            day = esc(e.date.slice(5).replace('-','.')) + ' ';
    return '<div class="me '+esc(e.impact)+'">'+day+esc(e.time)+' '+esc(e.event)+(e.country?' ['+esc(e.country)+']':'')+'</div>';
  }).join('');
}

function renderTokenCard(sym, d) {
  if (!d) return '';
  const name = sym.replace('USDT','');
  const sig  = d.signal || 'NEUTRAL';
  const sc   = parseInt(d.score) || 0;
  const dots = [0,1,2,3,4,5,6].map(i =>
    `<div class="sd ${i<Math.abs(sc)?(sc>0?'g':'r'):''}"></div>`).join('');
  const sentColor = d.sentiment==='bullish'?'var(--signal)':d.sentiment==='bearish'?'var(--red)':'var(--muted)';
  const frColor   = d.funding_rate>0.03?'var(--red)':d.funding_rate<-0.03?'var(--signal)':'#888';
  const volColor  = d.volume_ratio>1.3?'var(--signal)':d.volume_ratio<0.7?'var(--red)':'#888';
  let posHtml = `<div style="font-size:10px;color:var(--dim);margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">Keine Position</div>`;
  if (d.position) {
    const side  = d.position.holdSide==='long'?'LONG':'SHORT';
    const sColor = side==='LONG'?'var(--signal)':'var(--red)';
    const upnl  = parseFloat(d.position.unrealizedPL||0);
    posHtml = `<div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
      <div class="ind"><span style="color:${sColor};font-weight:700">${side}</span><span style="color:#888">${parseFloat(d.position.openPriceAvg||0).toFixed(2)}</span></div>
      <div class="ind"><span>uPnL</span><span style="color:${upnl>=0?'var(--signal)':'var(--red)'}">${(upnl>=0?'+':'')+upnl.toFixed(3)}</span></div>
    </div>`;
  }
  const PART_LABELS = {ema:'EMA',rsi:'RSI',macd:'MACD',bb:'BB',volume:'Vol',funding:'Fund',fear_greed:'F&G',news:'News',orderbook:'OB',macro:'Makro',trend:'Trend'};
  const parts = d.score_parts||{}; const pk = Object.keys(parts);
  const breakdown = pk.length
    ? pk.map(k=>`<span style="color:${parts[k]>0?'var(--signal)':'var(--red)'}">${esc(PART_LABELS[k]||k)} ${parts[k]>0?'+':''}${parts[k]}</span>`).join(' · ')
    : '<span style="color:var(--dim)">–</span>';
  return `<div class="tc">
    <div class="tc-name"><span>${name}</span><span style="font-size:10px;color:var(--muted)">${d.fear_greed||50}</span></div>
    <div class="sdots">${dots}</div>
    <div class="badge badge-${sig.toLowerCase()}">${sig}</div>
    <div class="ind"><span>RSI</span><span>${parseFloat(d.rsi||0).toFixed(1)}</span></div>
    <div class="ind"><span>MACD</span><span style="color:${(d.macd||0)>(d.macd_signal||0)?'var(--signal)':'var(--red)'}">${(d.macd||0)>(d.macd_signal||0)?'Bull':'Bear'}</span></div>
    <div class="ind"><span>Volumen</span><span style="color:${volColor}">${parseFloat(d.volume_ratio||1).toFixed(1)}x</span></div>
    <div class="ind"><span>ADX</span><span style="color:${(d.adx||0)>=25?'var(--signal)':(d.adx||0)>0&&(d.adx||0)<20?'var(--red)':'#888'}">${d.adx==null?'–':parseFloat(d.adx||0).toFixed(0)}</span></div>
    <div class="ind"><span>Kaufdruck</span><span style="color:${d.ob_ratio==null?'#888':(d.ob_ratio>=1.1?'var(--signal)':d.ob_ratio<=0.9?'var(--red)':'#888')}">${d.ob_ratio==null?'–':parseFloat(d.ob_ratio).toFixed(2)}</span></div>
    <div class="ind"><span>Funding</span><span style="color:${frColor}">${(parseFloat(d.funding_rate||0)*100).toFixed(4)}%</span></div>
    <div class="ind"><span>News</span><span style="color:${sentColor}">${d.sentiment||'neutral'}</span></div>
    <div style="font-size:9px;color:var(--muted);margin-top:6px;padding-top:6px;border-top:1px solid var(--border);line-height:1.8">${breakdown}</div>
    ${posHtml}
  </div>`;
}

function update(state) {
  lastState = state;
  // Server-Uhr merken (fuer fmtUptime) - so bleibt die Laufzeit auch bei schiefer Client-Uhr korrekt.
  if (state.server_now) { _serverNow = state.server_now; _serverNowAt = Date.now()/1000; }
  document.getElementById('last-update').textContent = new Date().toLocaleTimeString('de-DE');
  // Plattform-Laufzeit im Header
  const pu = document.getElementById('platform-uptime');
  if (pu) pu.textContent = state.platform_started_at ? '⏱ ' + fmtUptime(state.platform_started_at) : '';
  trackPnl(state);
  updateSparklines();

  // Live/Demo mode badge
  const live = state.live_mode || false;
  const badge = document.getElementById('mode-badge');
  if (badge) {
    badge.textContent = live ? 'LIVE' : 'DEMO';
    badge.className = 'mode-badge ' + (live ? 'mode-live' : 'mode-demo');
  }
  document.body.className = live ? 'live-mode' : 'demo-mode';

  // Dots
  ['signal','grid','dca'].forEach(id => {
    const st = state.bots[id]?.status || 'STOPPED';
    const dot = document.getElementById('dot-' + id);
    if (dot) dot.className = 'status-dot ' + dotClass(st);
  });

  // OVERVIEW
  let totalBal = 0, totalPnl = 0, activeCount = 0, totalTrades = 0;
  const rows = ['signal','grid','dca'].map(id => {
    const b = state.bots[id] || {};
    const bal = parseFloat(b.balance||0);
    const pnl = parseFloat(b.pnl||0);
    const st  = b.status || 'STOPPED';
    if (st === 'RUNNING') activeCount++;
    totalBal    += bal;
    totalPnl    += pnl;
    totalTrades += parseInt(b.trade_count||0);
    const up = (st === 'RUNNING') ? fmtUptime(b.started_at) : '';
    return `<div class="ov-row">
      <span class="ov-bot-name" style="color:${BOT_COLORS[id]}">${BOT_NAMES[id]}</span>
      <span><span class="ov-status ${statusClass(st)}">${st}</span>${up?`<span class="ov-uptime" data-start="${b.started_at}">⏱ ${up}</span>`:''}</span>
      <span class="blue">${bal.toFixed(2)}</span>
      <span class="${pnlColor(pnl)}">${(pnl>=0?'+':'')+pnl.toFixed(2)}</span>
      <span style="color:var(--muted)">${(b.trade_count||0)+' Trades'}</span>
      <span>
        <button class="btn ${st==='RUNNING'?'btn-stop':'btn-start'}"
          style="--accent:${BOT_COLORS[id]};padding:5px 12px"
          onclick="toggleBot('${id}')">${st==='RUNNING'?'STOP':'START'}</button>
      </span>
    </div>`;
  });
  document.getElementById('ov-rows').innerHTML = rows.join('');
  const pnlEl = document.getElementById('ov-pnl');
  pnlEl.textContent = (totalPnl>=0?'+':'')+totalPnl.toFixed(2);
  pnlEl.className = 'card-value ' + pnlColor(totalPnl);
  // Balance: mehrere Bots koennen denselben Account (gleicher API-Key) nutzen - dann darf
  // der Bestand NICHT doppelt gezaehlt werden. Das Backend liefert den deduplizierten
  // Gesamtbestand (total_balance); Fallback ist die naive Summe.
  document.getElementById('ov-balance').textContent =
    (state.total_balance != null ? state.total_balance : totalBal).toFixed(2);
  document.getElementById('ov-pnlpct').textContent = state.bots.signal?.pnl_pct?.toFixed(2)+'%' || '-';
  document.getElementById('ov-active').textContent = activeCount + ' / 3';
  document.getElementById('ov-trades').textContent = totalTrades;

  // Overview macro (from signal bot)
  const macroEvs = state.bots.signal?.macro_events || [];
  document.getElementById('ov-macro').innerHTML = renderMacro(macroEvs);

  // Combined recent log
  let allLogs = [];
  ['signal','grid','dca'].forEach(id => {
    (state.bots[id]?.logs||[]).slice(0,10).forEach(entry => {
      allLogs.push({...entry, bot: id});
    });
  });
  allLogs = allLogs.slice(0,20);
  document.getElementById('ov-log').innerHTML = renderLog(allLogs, 20);
  document.getElementById('ov-logcount').textContent = allLogs.length + ' Eintraege';

  // SIGNAL
  const sg = state.bots.signal || {};
  updateBotHeader('signal', sg);
  const spnl = parseFloat(sg.pnl||0);
  // (kein 's-balance'-Element im Signal-Tab - Balance steht im Overview. Der alte Zugriff
  //  hier warf null.textContent -> update() crashte -> 'Verbindung unterbrochen'.)
  const spnlEl = document.getElementById('s-pnl');
  spnlEl.textContent = (spnl>=0?'+':'')+spnl.toFixed(2);
  spnlEl.className = 'card-value ' + pnlColor(spnl);
  document.getElementById('s-pnlpct').textContent = (sg.pnl_pct||0).toFixed(2)+'%';
  document.getElementById('s-trades').textContent = sg.trade_count||0;
  // Win/Loss Streaks
  const ws = sg.win_streak||0, ls = sg.loss_streak||0;
  const wsEl = document.getElementById('s-win-streak');
  const lsEl = document.getElementById('s-loss-streak');
  if (wsEl) { wsEl.textContent = ws+'W'; wsEl.style.opacity = ws>0?'1':'0.3'; }
  if (lsEl) { lsEl.textContent = ls+'L'; lsEl.style.opacity = ls>0?'1':'0.3'; }
  const siEl = document.getElementById('s-streak-info');
  if (siEl) siEl.textContent = ws>0 ? ws+' Gewinne in Folge' : ls>0 ? ls+' Verluste in Folge' : 'keine Streak';
  const bo = sg.blackout;
  const boEl = document.getElementById('s-blackout');
  boEl.textContent = bo ? 'BLACKOUT' : 'OK';
  boEl.className = 'card-value ' + (bo ? 'red' : 'green');
  // Circuit Breaker Badge
  checkCircuitBreaker();
  const toks = sg.tokens || {};
  document.getElementById('s-tokens').innerHTML =
    Object.entries(toks).map(([s,d]) => renderTokenCard(s,d)).join('');
  document.getElementById('s-macro').innerHTML = renderMacro(sg.macro_events||[]);
  document.getElementById('s-log').innerHTML = renderLog(sg.logs||[]);
  document.getElementById('s-logcount').textContent = (sg.logs||[]).length + ' Eintraege';

  // GRID
  const gg = state.bots.grid || {};
  updateBotHeader('grid', gg);
  const gpnl = parseFloat(gg.pnl||0);
  document.getElementById('g-balance').textContent = parseFloat(gg.balance||0).toFixed(2);
  const gpnlEl = document.getElementById('g-pnl');
  gpnlEl.textContent = (gpnl>=0?'+':'')+gpnl.toFixed(4);
  gpnlEl.className = 'card-value ' + pnlColor(gpnl);
  document.getElementById('g-filled').textContent = gg.filled||0;
  document.getElementById('g-symbol').textContent = gg.symbol||'-';
  document.getElementById('g-range').textContent = gg.lower&&gg.upper ? gg.lower+' - '+gg.upper+(gg.step?' · Δ'+gg.step:'') : '-';
  const orders = gg.grid_orders || [];
  if (orders.length > 0) {
    const midPrice = (gg.upper + gg.lower) / 2;
    document.getElementById('g-levels').innerHTML = orders.map((o,i) => {
      const pct  = ((o.price - gg.lower) / (gg.upper - gg.lower) * 100).toFixed(0);
      const side = o.price < midPrice ? 'BUY' : 'SELL';
      const col  = side==='BUY' ? 'var(--signal)' : 'var(--red)';
      return `<div class="grid-level">
        <span class="gl-price">${o.price.toFixed(2)}</span>
        <div class="gl-bar"><div class="gl-fill" style="width:${pct}%;background:${col};opacity:${o.filled?.8:.25}"></div></div>
        <span class="gl-side" style="color:${o.filled?col:'var(--muted)'}">${o.filled?'*'+side:'o'}</span>
      </div>`;
    }).join('');
  }
  document.getElementById('g-log').innerHTML = renderLog(gg.logs||[]);
  document.getElementById('g-logcount').textContent = (gg.logs||[]).length + ' Eintraege';
  // Update TV chart grid lines if grid tab active
  if (activePanel === 'grid') {
    updateTVGridLines(gg.grid_orders, gg.upper, gg.lower);
  }

  // DCA
  const dg = state.bots.dca || {};
  updateBotHeader('dca', dg);
  const dpnl = parseFloat(dg.pnl||0);
  document.getElementById('d-balance').textContent = parseFloat(dg.balance||0).toFixed(2);
  document.getElementById('d-invested').textContent = parseFloat(dg.invested||0).toFixed(2);
  const dpnlEl = document.getElementById('d-pnl');
  dpnlEl.textContent = (dpnl>=0?'+':'')+dpnl.toFixed(2);
  dpnlEl.className = 'card-value ' + pnlColor(dpnl);
  document.getElementById('d-avg').textContent = dg.avg_price > 0 ? 'Avg: '+parseFloat(dg.avg_price).toFixed(2) : 'Noch kein Kauf';
  document.getElementById('d-next').textContent = dg.next_buy || '-';
  document.getElementById('d-buys').textContent = (dg.buys||0) + ' Kaeufe';
  document.getElementById('d-log').innerHTML = renderLog(dg.logs||[]);
  document.getElementById('d-logcount').textContent = (dg.logs||[]).length + ' Eintraege';
}

function updateBotHeader(id, b) {
  const st = b.status || 'STOPPED';
  const badge = document.getElementById(id+'-status-badge');
  const btn   = document.getElementById(id+'-btn');
  if (badge) { badge.textContent = st; badge.className = 'ov-status ' + statusClass(st); }
  if (btn) {
    const running = st === 'RUNNING' || st === 'STARTING';
    btn.textContent = running ? 'STOP' : 'START';
    btn.className   = 'btn ' + (running ? 'btn-stop' : 'btn-start');
  }
}

async function toggleBot(id) {
  const st      = lastState?.bots[id]?.status || 'STOPPED';
  const running = st === 'RUNNING' || st === 'STARTING';
  // Sofortige lokale Anzeige-Aktualisierung – kein Warten auf naechsten Poll
  if (lastState?.bots?.[id]) {
    lastState.bots[id].status = running ? 'STOPPING' : 'STARTING';
    update(lastState);
  }
  try {
    const r = await fetch('/api/bot/' + (running ? 'stop' : 'start'), {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({bot_id: id}),
    });
    const d = await r.json();
    if (d.status !== 'ok') {
      // Fehler: Zustand zuruecksetzen
      if (lastState?.bots?.[id]) lastState.bots[id].status = st;
      update(lastState);
      alert(d.msg || 'Fehler');
    }
  } catch(e) {
    if (lastState?.bots?.[id]) lastState.bots[id].status = st;
    update(lastState);
    alert('Verbindungsfehler: ' + e.message);
  }
}

function fillSettingsForm(state) {
  fetch('/api/config').then(r=>r.json()).then(cfg => {
    const s = v => v != null ? String(v) : '';
    document.getElementById('cfg-dash-user').value   = s(cfg.dashboard_user||'admin');
    document.getElementById('cfg-dash-pass').value   = '';
    document.getElementById('cfg-finnhub').value     = s(cfg.finnhub_key);
    document.getElementById('cfg-coinalyze').value   = s(cfg.coinalyze_key);
    document.getElementById('cfg-tg-token').value    = s(cfg.telegram_token);
    document.getElementById('cfg-tg-chat').value     = s(cfg.telegram_chat_id);
    document.getElementById('cfg-discord-wh').value  = s(cfg.discord_webhook||'');
    const live = cfg.live_mode || false;
    document.getElementById('cfg-live').checked = live;
    const label = document.getElementById('mode-label');
    if (label) {
      label.textContent = live ? t('mode_live_active') : t('mode_demo_active');
      label.style.color = live ? 'var(--red)' : 'var(--grid)';
    }
    const b = cfg.bots || {};
    document.getElementById('sig-key').value    = s(b.signal?.api_key);
    document.getElementById('sig-lever').value      = s(b.signal?.leverage||3);
    document.getElementById('sig-risk-pct').value   = s(b.signal?.risk_pct||3.0);
    document.getElementById('sig-usdt').value        = s(b.signal?.usdt_per_trade||30);
    document.getElementById('sig-budget').value      = s(b.signal?.budget_usdt ?? 0);
    document.getElementById('sig-max-conc').value    = s(b.signal?.max_concurrent||2);
    document.getElementById('sig-corr-filter').checked = (b.signal?.use_correlation_filter !== false);
    document.getElementById('sig-max-corr').value    = s(b.signal?.max_correlation ?? 0.85);
    document.getElementById('sig-adx-filter').checked = (b.signal?.use_adx_filter !== false);
    document.getElementById('sig-min-adx').value     = s(b.signal?.min_adx ?? 20);
    document.getElementById('sig-adx-gate').checked  = (b.signal?.use_adx_gate !== false);
    document.getElementById('sig-sl-mult').value     = s(b.signal?.atr_sl_mult ?? 1.5);
    document.getElementById('sig-tokens').value      = (b.signal?.tokens || []).join(', ');
    document.getElementById('sig-ob-signal').checked = (b.signal?.use_orderbook_signal !== false);
    const sf = k => document.getElementById('sig-f-'+k); const sg = b.signal||{};
    sf('ema').checked=(sg.use_ema!==false); sf('rsi').checked=(sg.use_rsi!==false);
    sf('macd').checked=(sg.use_macd!==false); sf('bb').checked=(sg.use_bb!==false);
    sf('volume').checked=(sg.use_volume!==false); sf('funding').checked=(sg.use_funding!==false);
    sf('fg').checked=(sg.use_fg!==false); sf('news').checked=(sg.use_news!==false);
    sf('macro').checked=(sg.use_macro!==false); sf('trend').checked=(sg.use_trend===true);
    sf('delta').checked=(sg.use_delta!==false);
    document.getElementById('sig-trend-len').value = s(sg.trend_len ?? 50);
    document.getElementById('sig-thresh').value = s(b.signal?.signal_threshold||3);
    document.getElementById('sig-daily-limit').value = s(b.signal?.daily_loss_limit_pct||0);
    document.getElementById('sig-trend-gate').checked = (b.signal?.use_trend_gate !== false);
    document.getElementById('sig-htf-trend').checked = (b.signal?.use_htf_trend !== false);
    document.getElementById('sig-cooldown').value = s(b.signal?.trade_cooldown_min ?? 20);
    document.getElementById('sig-trailing').checked = (b.signal?.use_trailing !== false);
    document.getElementById('sig-trail-mult').value = s(b.signal?.trail_atr_mult ?? 2.0);
    document.getElementById('grd-key').value   = s(b.grid?.api_key);
    document.getElementById('grd-sym').value   = s(b.grid?.symbol||'BTCUSDT');
    document.getElementById('grd-upper').value = s(b.grid?.upper_price||0);
    document.getElementById('grd-lower').value = s(b.grid?.lower_price||0);
    document.getElementById('grd-step').value  = s(b.grid?.step_size||0);
    document.getElementById('grd-srhours').value = s(b.grid?.smart_range_hours||24);
    document.getElementById('grd-lev').value   = s(b.grid?.leverage||0);
    document.getElementById('grd-sl').value    = s(((b.grid?.stop_loss_pct||0)*100));
    document.getElementById('grd-n').value     = s(b.grid?.grid_count||10);
    document.getElementById('grd-inv').value   = s(b.grid?.investment||100);
    document.getElementById('dca-key').value = s(b.dca?.api_key);
    document.getElementById('dca-sym').value = s(b.dca?.symbol||'BTCUSDT');
    document.getElementById('dca-hrs').value = s(b.dca?.interval_hours||24);
    document.getElementById('dca-amt').value = s(b.dca?.amount_per_buy||20);
    // Secret/Passphrase werden aus Sicherheitsgruenden nicht zurueckgefuellt. Platzhalter
    // signalisiert, dass sie gespeichert sind - leer lassen = unveraendert (kein Neu-Eintippen).
    document.getElementById('sig-autostart').checked = (b.signal?.autostart===true);
    document.getElementById('grd-autostart').checked = (b.grid?.autostart===true);
    document.getElementById('dca-autostart').checked = (b.dca?.autostart===true);
    const setPh = (id,has)=>{const el=document.getElementById(id); if(el&&has) el.placeholder='•••••• gespeichert (leer lassen = unveraendert)';};
    setPh('sig-sec',!!b.signal?.api_secret);  setPh('sig-pass',!!b.signal?.passphrase);
    setPh('grd-sec',!!b.grid?.api_secret);    setPh('grd-pass',!!b.grid?.passphrase);
    setPh('dca-sec',!!b.dca?.api_secret);     setPh('dca-pass',!!b.dca?.passphrase);
  }).catch(e=>console.error('fillSettingsForm:', e));  // nicht mehr still verschlucken
}

async function saveSettings() {
  const val = id => document.getElementById(id)?.value || '';
  const num = id => parseFloat(val(id)) || 0;
  const int = id => parseInt(val(id))   || 0;
  const cfg = {
    dashboard_user:     val('cfg-dash-user'),
    dashboard_password: val('cfg-dash-pass'),
    finnhub_key:     val('cfg-finnhub'),
    coinalyze_key:   val('cfg-coinalyze'),
    telegram_token:  val('cfg-tg-token'),
    telegram_chat_id:val('cfg-tg-chat'),
    discord_webhook: val('cfg-discord-wh') || '',
    live_mode:       document.getElementById('cfg-live')?.checked || false,
    bots: {
      signal: {
        api_key:          val('sig-key'),
        api_secret:       val('sig-sec'),
        passphrase:       val('sig-pass'),
        autostart:        document.getElementById('sig-autostart')?.checked || false,
        tokens:           val('sig-tokens').split(',').map(t=>t.trim().toUpperCase()).filter(Boolean).map(t=>t.endsWith('USDT')?t:t+'USDT'),
        leverage:         int('sig-lever')    || 3,
        risk_pct:         num('sig-risk-pct') || 3.0,
        atr_sl_mult:      num('sig-sl-mult') || 1.5,
        use_risk_pct:     true,
        usdt_per_trade:   num('sig-usdt')     || 30,
        budget_usdt:      num('sig-budget'),
        max_concurrent:   int('sig-max-conc') || 2,
        use_correlation_filter: document.getElementById('sig-corr-filter')?.checked ?? true,
        max_correlation:  num('sig-max-corr') || 0.85,
        use_adx_filter:   document.getElementById('sig-adx-filter')?.checked ?? true,
        min_adx:          int('sig-min-adx') || 20,
        use_adx_gate:     document.getElementById('sig-adx-gate')?.checked ?? true,
        use_orderbook_signal: document.getElementById('sig-ob-signal')?.checked ?? true,
        use_ema:     document.getElementById('sig-f-ema')?.checked ?? true,
        use_rsi:     document.getElementById('sig-f-rsi')?.checked ?? true,
        use_macd:    document.getElementById('sig-f-macd')?.checked ?? true,
        use_bb:      document.getElementById('sig-f-bb')?.checked ?? true,
        use_volume:  document.getElementById('sig-f-volume')?.checked ?? true,
        use_funding: document.getElementById('sig-f-funding')?.checked ?? true,
        use_fg:      document.getElementById('sig-f-fg')?.checked ?? true,
        use_news:    document.getElementById('sig-f-news')?.checked ?? true,
        use_macro:   document.getElementById('sig-f-macro')?.checked ?? true,
        use_delta:   document.getElementById('sig-f-delta')?.checked ?? true,
        use_trend:   document.getElementById('sig-f-trend')?.checked ?? false,
        trend_len:   int('sig-trend-len') || 50,
        signal_threshold: int('sig-thresh')   || 3,
        daily_loss_limit_pct: num('sig-daily-limit') || 0,
        use_trend_gate: document.getElementById('sig-trend-gate')?.checked ?? true,
        use_htf_trend: document.getElementById('sig-htf-trend')?.checked ?? true,
        trade_cooldown_min: int('sig-cooldown'),
        use_trailing: document.getElementById('sig-trailing')?.checked ?? true,
        trail_atr_mult: num('sig-trail-mult') || 2.0,
      },
      grid: {
        api_key:     val('grd-key'),
        api_secret:  val('grd-sec'),
        passphrase:  val('grd-pass'),
        autostart:   document.getElementById('grd-autostart')?.checked || false,
        symbol:      val('grd-sym')   || 'BTCUSDT',
        upper_price: num('grd-upper'),
        lower_price: num('grd-lower'),
        step_size:   num('grd-step'),
        smart_range_hours: int('grd-srhours') || 24,
        leverage:    int('grd-lev'),
        stop_loss_pct: (num('grd-sl')||0) / 100,
        grid_count:  int('grd-n')     || 10,
        investment:  num('grd-inv')   || 100,
      },
      dca: {
        api_key:       val('dca-key'),
        api_secret:    val('dca-sec'),
        passphrase:    val('dca-pass'),
        autostart:     document.getElementById('dca-autostart')?.checked || false,
        symbol:        val('dca-sym') || 'BTCUSDT',
        interval_hours:num('dca-hrs') || 24,
        amount_per_buy:num('dca-amt') || 20,
      },
    }
  };
  try {
    const r = await fetch('/api/config', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(cfg),
    });
    const d = await r.json();
    const msg = document.getElementById('save-msg');
    msg.style.display = 'inline';
    msg.textContent   = d.status === 'ok' ? 'Gespeichert.' : 'Fehler: ' + (d.msg||'');
    msg.style.color   = d.status === 'ok' ? 'var(--signal)' : 'var(--red)';
    setTimeout(() => msg.style.display = 'none', 3000);
    if (d.status === 'ok') fillSettingsForm();  // Formular direkt aus der gespeicherten Config auffrischen (Key sichtbar, Secret -> "gespeichert")
  } catch(e) { alert('Verbindungsfehler: ' + e.message); }
}

let _pollFails = 0;

async function poll() {
  try {
    const r = await fetch('/api/state');
    const d = await r.json();
    update(d);
    _pollFails = 0;
    if (activePanel === 'overview') { loadPositions(); loadFGHistory(); }
    if (activePanel === 'grid' && d.grid_instances) {
      _gridStates = d.grid_instances;
      renderGridInstances();
    }
  } catch(e) {
    _pollFails++;
    if (_pollFails >= 3) {
      document.getElementById('last-update').textContent = 'Verbindung unterbrochen...';
    }
  }
  setTimeout(poll, 5000);
}

poll();
// Sprache sofort beim Laden anwenden (vor erstem Render)
applyLang();
if (_lang !== 'de') {
  const lb = document.getElementById('lang-btn');
  if (lb) lb.textContent = 'EN / DE';
}
</script>
</body>
</html>"""

# ─────────────────────────────────────────────
#  SETUP-ASSISTENT (Erst-Einrichtung im Browser, headless)
# ─────────────────────────────────────────────
SETUP_HTML = """<!doctype html>
<html lang="de"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ersteinrichtung | Trading Platform</title>
<style>
  :root{--bg:#0b0e14;--card:#151a24;--border:#243044;--fg:#e6edf3;--muted:#8b97a7;
        --accent:#3b82f6;--red:#f87171;--green:#34d399}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
       display:flex;min-height:100vh;align-items:center;justify-content:center;padding:20px}
  .box{background:var(--card);border:1px solid var(--border);border-radius:12px;
       padding:28px;max-width:420px;width:100%}
  h1{font-size:18px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13px;margin:0 0 20px;line-height:1.5}
  label{display:block;font-size:12px;font-weight:600;color:var(--muted);
        margin:14px 0 6px;letter-spacing:.03em}
  input{width:100%;background:var(--bg);border:1px solid var(--border);color:var(--fg);
        border-radius:8px;padding:11px 12px;font-size:14px}
  input:focus{outline:none;border-color:var(--accent)}
  button{width:100%;margin-top:20px;background:var(--accent);color:#fff;border:0;
         border-radius:8px;padding:12px;font-size:14px;font-weight:700;cursor:pointer}
  button:disabled{opacity:.5;cursor:not-allowed}
  .msg{margin-top:14px;font-size:13px;min-height:18px}
  .err{color:var(--red)} .ok{color:var(--green)}
  .hint{color:var(--muted);font-size:11px;margin-top:6px}
</style></head>
<body>
  <div class="box">
    <h1>Ersteinrichtung &middot; First-time setup</h1>
    <p class="sub">Lege Benutzername und Passwort fuer das Dashboard fest.<br>
       Set your dashboard username and password.</p>
    <label>Benutzername / Username</label>
    <input id="u" autocomplete="username" value="admin">
    <label>Passwort / Password (min. 8)</label>
    <input id="p" type="password" autocomplete="new-password">
    <label>Passwort wiederholen / Confirm</label>
    <input id="p2" type="password" autocomplete="new-password">
    <button id="btn" onclick="submit()">Speichern &amp; einloggen / Save &amp; log in</button>
    <div class="msg" id="msg"></div>
    <div class="hint">Danach fragt der Browser nach diesen Zugangsdaten (HTTP Basic Auth).</div>
  </div>
<script>
function submit(){
  var u=document.getElementById('u').value.trim();
  var p=document.getElementById('p').value;
  var p2=document.getElementById('p2').value;
  var msg=document.getElementById('msg'); var btn=document.getElementById('btn');
  msg.className='msg'; msg.textContent='';
  if(!u){msg.className='msg err';msg.textContent='Benutzername fehlt / Username required';return;}
  if(p.length<8){msg.className='msg err';msg.textContent='Passwort min. 8 Zeichen / Password min 8 chars';return;}
  if(p!==p2){msg.className='msg err';msg.textContent='Passwoerter stimmen nicht ueberein / Passwords do not match';return;}
  btn.disabled=true; msg.textContent='...';
  fetch('/api/setup',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({dashboard_user:u,dashboard_password:p})})
   .then(function(r){return r.json();})
   .then(function(d){
     if(d.status==='ok'){
       msg.className='msg ok';
       msg.textContent='Gespeichert. Neu laden & einloggen... / Saved. Reloading...';
       setTimeout(function(){location.reload();},1500);
     } else {
       btn.disabled=false; msg.className='msg err';
       msg.textContent=d.msg||'Fehler / Error';
     }
   })
   .catch(function(){btn.disabled=false;msg.className='msg err';msg.textContent='Netzwerkfehler / Network error';});
}
document.getElementById('p2').addEventListener('keydown',function(e){if(e.key==='Enter')submit();});
</script>
</body></html>"""

# ─────────────────────────────────────────────
#  HTTP SERVER
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _serve_setup(self):
        html = SETUP_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _json(self, data, code=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self):
        """HTTP Basic Auth. Verhindert unauthorisierten Zugriff und CSRF: Browser
        haengen Basic-Auth-Credentials nie automatisch an Cross-Origin-Requests an,
        eine boesartige Seite kann also nicht per fetch()/Formular Bot-Aktionen ausloesen."""
        cfg  = load_config()
        user = cfg.get("dashboard_user","admin")
        pw   = cfg.get("dashboard_password","")
        auth = self.headers.get("Authorization","")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded  = base64.b64decode(auth[6:]).decode("utf-8")
            u, _, p  = decoded.partition(":")
        except Exception:
            return False
        return hmac.compare_digest(u, user) and hmac.compare_digest(p, pw)

    def _deny_auth(self):
        body = b"Authentication required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Trading Platform"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        # Erst-Einrichtung offen: nur den Setup-Assistenten ausliefern, sonst alles sperren.
        if _setup_required():
            if self.path.startswith("/api/"):
                self._json({"status":"setup_required"}, 403)
            else:
                self._serve_setup()
            return
        if not self._check_auth():
            self._deny_auth(); return
        if self.path == "/api/state":
            with plock:
                state_copy = dict(pstate)
                state_copy["grid_instances"] = dict(pstate.get("grid_instances",{}))
            # Deduplizierter Gesamtbestand: mehrere Bots koennen denselben Account teilen
            # (gleicher API-Key) - dann darf der Bestand nur EINMAL zaehlen. Pro einzigartigem
            # API-Key eine Balance addieren (Haupt-Bots + Grid-Instanzen).
            try:
                _cfg = load_config(); _seen = set(); _tot = 0.0; _all = []
                for _bid in ("signal", "grid", "dca"):
                    _b = float(state_copy["bots"][_bid].get("balance", 0) or 0)
                    if _b: _all.append(_b)
                    _ak = _cfg["bots"].get(_bid, {}).get("api_key", "")
                    if _ak and _ak not in _seen:
                        _seen.add(_ak); _tot += _b
                for _inst in _cfg.get("grid_instances", []):
                    _b = float(state_copy["grid_instances"].get(_inst.get("id"), {}).get("balance", 0) or 0)
                    if _b: _all.append(_b)
                    _ak = _inst.get("api_key", "")
                    if _ak and _ak not in _seen:
                        _seen.add(_ak); _tot += _b
                # Demo: alle Bots teilen sich EINE virtuelle Wallet -> nicht summieren, sondern
                # die (identische) Einzel-Balance zeigen. Live: verschiedene Konten = verschiedene
                # Toepfe -> deduplizierte Summe (pro API-Key einmal).
                if not _cfg.get("live_mode", False):
                    state_copy["total_balance"] = round(max(_all), 2) if _all else 0.0
                else:
                    state_copy["total_balance"] = round(_tot, 2)
            except Exception:
                state_copy["total_balance"] = None
            state_copy["platform_started_at"] = PLATFORM_START
            state_copy["server_now"] = time.time()
            self._json(state_copy)
        elif self.path == "/api/config":
            self._json(load_config())
        elif self.path == "/api/market":
            self._json(fetch_market_overview())
        elif self.path == "/api/trades":
            # Aus der lokalen DB (eigene gebuchte Trades) statt vom Bitget-Fills-Endpunkt -
            # zuverlaessig UND UTA-kompatibel. Auf die Felder mappen, die das Frontend erwartet.
            _out = []
            for _t in db_get_trades(None, 200):
                try:    _ts = datetime.fromtimestamp(int(_t.get("ts",0))/1000).strftime("%d.%m %H:%M")
                except: _ts = ""
                _out.append({
                    "bot":      _t.get("bot",""),
                    "time_str": _ts,
                    "symbol":   _t.get("symbol",""),
                    "side":     "buy" if str(_t.get("side","")).upper() == "LONG" else "sell",
                    "price":    float(_t.get("exit",0) or 0),
                    "size":     float(_t.get("size",0) or 0),
                    "pnl":      float(_t.get("pnl",0) or 0),
                    "fee":      float(_t.get("fee",0) or 0),
                })
            self._json(_out)
        elif self.path == "/api/positions":
            self._json(fetch_all_positions())
        elif self.path == "/api/fg_history":
            self._json(fetch_fg_history())
        elif self.path == "/api/alert_log":
            with _alert_lock:
                self._json(list(_alert_log))
        elif self.path == "/api/syslog/download":
            # Komplette aktuelle Logdatei als Download (text/plain). Auth ist oben geprueft.
            try:
                with open("platform.log", "rb") as f:
                    raw = f.read()
            except Exception as e:
                raw = f"Log nicht lesbar: {e}".encode()
            fname = "platform-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".log"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        elif self.path.startswith("/api/syslog"):
            # Letzte N Zeilen der Plattform-Logdatei (Standard 400, max 3000). Bindet die
            # rotierte Vorgaenger-Datei (platform.log.1) mit ein, damit ueber einen Neustart
            # hinweg genug Historie sichtbar ist.
            qs   = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:    n = max(20, min(3000, int(qs.get("lines", ["400"])[0])))
            except: n = 400
            lines, size = [], 0
            for fn in ("platform.log.1", "platform.log"):   # aelter zuerst, dann aktuell
                try:
                    with open(fn, "r", encoding="utf-8", errors="replace") as f:
                        part = f.read()
                    size += len(part.encode("utf-8", "replace"))
                    lines.extend(part.splitlines())
                except FileNotFoundError:
                    continue
                except Exception as e:
                    lines.append(f"[{fn} nicht lesbar: {e}]")
            self._json({"lines": lines[-n:], "count": len(lines), "shown": min(n, len(lines)),
                        "size": size, "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        elif self.path.startswith("/api/klines"):
            # Klines-Proxy zu Bitget (Dashboard-Auth wird oben in do_GET bereits geprueft)
            qs     = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sym    = qs.get("symbol",["BTCUSDT"])[0]
            gran   = qs.get("granularity",["1H"])[0]
            try:
                r = requests.get(f"{BASE_URL}/api/v2/mix/market/candles",
                    params={"symbol": sym, "productType": PRODUCT_TYPE,
                            "granularity": gran, "limit": "100"},
                    timeout=10)
                self._json(r.json())
            except Exception as e:
                self._json({"error": str(e)}, 500)
        else:
            html = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

    def do_POST(self):
        setup = _setup_required()
        # Solange die Erst-Einrichtung offen ist, ist NUR /api/setup erreichbar (ohne Auth).
        # Ist sie abgeschlossen, gibt es /api/setup nicht mehr (kein spaeteres Zuruecksetzen).
        if setup:
            if self.path != "/api/setup":
                self._json({"status":"setup_required"}, 403); return
        else:
            if self.path == "/api/setup":
                self._json({"status":"error","msg":"Bereits eingerichtet"}, 403); return
            if not self._check_auth():
                self._deny_auth(); return
        length = int(self.headers.get("Content-Length",0))
        body   = self.rfile.read(length).decode("utf-8")
        try:   data = json.loads(body)
        except:data = {}
        try:
            if setup:
                self._handle_setup(data)
            else:
                self._dispatch_post(data)
        except Exception as e:
            self._json({"status":"error","msg":f"Ungueltige Anfrage: {e}"}, 400)

    def _handle_setup(self, data):
        """Erst-Einrichtung: Benutzername + Passwort festlegen. Nur wirksam, solange
        noch kein Passwort gesetzt ist (do_POST prueft das)."""
        user = str(data.get("dashboard_user","")).strip()
        pw   = str(data.get("dashboard_password",""))
        if not user:
            self._json({"status":"error","msg":"Benutzername fehlt / Username required"}, 400); return
        if len(pw) < 8:
            self._json({"status":"error","msg":"Passwort min. 8 Zeichen / Password min 8 chars"}, 400); return
        cfg = load_config()
        cfg["dashboard_user"]     = user
        cfg["dashboard_password"] = pw
        save_config(cfg)
        log.warning("="*55)
        log.warning(f"  Dashboard-Zugang eingerichtet: user='{user}' (Setup-Assistent).")
        log.warning("="*55)
        self._json({"status":"ok"})

    def _dispatch_post(self, data):
        if self.path == "/api/config":
            cfg = load_config()
            for k in ("finnhub_key","cryptopanic_key","coinalyze_key","telegram_token","telegram_chat_id"):
                if k in data: cfg[k] = data[k]
            # Dashboard-Login nur ueberschreiben wenn tatsaechlich ein Wert gesendet wurde -
            # ein leerer String wuerde sonst das Passwort effektiv aussperren.
            if data.get("dashboard_user"):     cfg["dashboard_user"]     = str(data["dashboard_user"])
            if data.get("dashboard_password"): cfg["dashboard_password"] = str(data["dashboard_password"])
            if "live_mode" in data:
                cfg["live_mode"] = bool(data["live_mode"])
                with plock:
                    pstate["live_mode"] = cfg["live_mode"]
            for bid in ("signal","grid","dca"):
                bd = data.get("bots",{}).get(bid,{})
                for k, v in bd.items():
                    if k not in cfg["bots"][bid]:
                        continue
                    # Key/Secret/Passphrase nur ueberschreiben, wenn wirklich ein Wert kam -
                    # ein leeres Feld (z.B. nach Seiten-Reload) darf gespeicherte Keys NICHT
                    # loeschen. So muss man beim Update nicht alles neu eintippen.
                    if k in ("api_key", "api_secret", "passphrase") and not v:
                        continue
                    if k == "tokens" and (not isinstance(v, list) or not v):
                        continue   # leere Coins-Liste nicht speichern -> gesetzte behalten
                    cfg["bots"][bid][k] = v
            save_config(cfg)
            _macro_cache["ts"] = 0
            # Re-init telegram if keys changed
            tg_init(cfg.get("telegram_token",""), cfg.get("telegram_chat_id",""))
            self._json({"status":"ok"})

        elif self.path == "/api/bot/start":
            bid = data.get("bot_id","")
            ok, msg = start_bot(bid) if bid else (False,"Kein bot_id")
            self._json({"status":"ok" if ok else "error","msg":msg})

        elif self.path == "/api/bot/stop":
            bid = data.get("bot_id","")
            ok, msg = stop_bot(bid) if bid else (False,"Kein bot_id")
            self._json({"status":"ok" if ok else "error","msg":msg})

        elif self.path == "/api/panic":
            result = emergency_stop()
            self._json({"status":"ok","result":result})

        elif self.path == "/api/backtest":
            result = run_backtest(
                symbol       = str(data.get("symbol","BTCUSDT"))[:20],
                period_days  = max(1,   min(730, int(data.get("period_days", 14)))),
                leverage     = max(1,   min(125, int(data.get("leverage", 3)))),
                threshold    = max(1,   min(10,  int(data.get("threshold", 2)))),
                sl_pct       = max(0.001, min(0.5, float(data.get("sl_pct", 0.010)))),
                tp_pct       = max(0.001, min(1.0, float(data.get("tp_pct", 0.020)))),
                walk_forward = bool(data.get("walk_forward", False)),
                pos_frac     = max(0.01, min(1.0, float(data.get("pos_pct", 10)) / 100)),
            )
            self._json(result)

        elif self.path == "/api/multi_backtest":
            symbols = data.get("symbols", ["BTCUSDT","ETHUSDT","SOLUSDT"])
            if not isinstance(symbols, list): symbols = ["BTCUSDT"]
            symbols = [str(s)[:20] for s in symbols[:10]]
            result  = run_multi_backtest(
                symbols,
                period_days = max(1, min(730, int(data.get("period_days", 14)))),
                leverage    = max(1, min(125, int(data.get("leverage", 3)))),
                threshold   = max(1, min(10,  int(data.get("threshold", 2)))),
                sl_pct      = max(0.001, min(0.5, float(data.get("sl_pct", 0.010)))),
                tp_pct      = max(0.001, min(1.0, float(data.get("tp_pct", 0.020)))),
                pos_frac    = max(0.01, min(1.0, float(data.get("pos_pct", 10)) / 100)),
            )
            self._json(result)

        elif self.path == "/api/db_trades":
            self._json(db_get_trades(data.get("bot"), int(data.get("limit",200))))

        elif self.path == "/api/db_pnl":
            self._json(db_get_pnl_history(data.get("bot","signal"),
                                           int(data.get("days",30))))

        elif self.path == "/api/trade_timing":
            self._json(db_trade_timing())

        elif self.path == "/api/circuit_status":
            self._json({"open": _circuit_open, "until": _circuit_until})

        elif self.path == "/api/alerts/save":
            raw = data.get("alerts", [])
            if not isinstance(raw, list): raw = []
            clean = []
            for a in raw[:100]:
                if not isinstance(a, dict): continue
                clean.append({
                    "id":        str(a.get("id",""))[:40],
                    "name":      str(a.get("name",""))[:80],
                    "type":      str(a.get("type",""))[:40],
                    "symbol":    str(a.get("symbol",""))[:20],
                    "value":     a.get("value", 0) if isinstance(a.get("value"), (int,float)) else 0,
                    "enabled":   bool(a.get("enabled", True)),
                    "triggered": bool(a.get("triggered", False)),
                })
            cfg = load_config()
            cfg["alerts"] = clean
            save_config(cfg)
            self._json({"status":"ok"})

        elif self.path == "/api/alerts/get":
            cfg = load_config()
            self._json(cfg.get("alerts", []))

        elif self.path == "/api/grid/instances":
            cfg = load_config()
            with plock:
                self._json({
                    "instances": cfg.get("grid_instances",[]),
                    "states":    pstate.get("grid_instances",{}),
                })

        elif self.path == "/api/grid/add":
            cfg  = load_config()
            inst = cfg.get("grid_instances",[])
            new  = {
                "id":         "g" + str(int(time.time())),
                "name":        str(data.get("name","Grid "+str(len(inst)+2)))[:60],
                "api_key":     data.get("api_key",""),
                "api_secret":  data.get("api_secret",""),
                "passphrase":  data.get("passphrase",""),
                "symbol":      str(data.get("symbol","BTCUSDT"))[:20],
                "upper_price": max(0.0, float(data.get("upper_price",0))),
                "lower_price": max(0.0, float(data.get("lower_price",0))),
                "step_size":   max(0.0, float(data.get("step_size",0))),
                "grid_count":  max(2, min(50, int(data.get("grid_count",10)))),
                "investment":  max(0.0, float(data.get("investment",100))),
                "seed_position": True,
                "smart_range_hours": max(6, min(168, int(data.get("smart_range_hours",24)))),
                "leverage":    max(0, min(125, int(data.get("leverage",0)))),
                "stop_loss_pct": max(0.0, min(0.9, float(data.get("stop_loss_pct",0)))),
                "check_interval": 10,
            }
            inst.append(new)
            cfg["grid_instances"] = inst
            save_config(cfg)
            self._json({"status":"ok","id":new["id"]})

        elif self.path == "/api/grid/remove":
            inst_id = data.get("id","")
            stop_grid_instance(inst_id)
            cfg = load_config()
            cfg["grid_instances"] = [i for i in cfg.get("grid_instances",[]) if i["id"]!=inst_id]
            save_config(cfg)
            with plock:
                pstate["grid_instances"].pop(inst_id, None)
            grid_save_state(inst_id, None)   # persistierten Stand mitloeschen
            self._json({"status":"ok"})

        elif self.path == "/api/grid/start_instance":
            ok, msg = start_grid_instance(data.get("id",""))
            self._json({"status":"ok" if ok else "error","msg":msg})

        elif self.path == "/api/grid/stop_instance":
            ok, msg = stop_grid_instance(data.get("id",""))
            self._json({"status":"ok" if ok else "error","msg":msg})

        elif self.path == "/api/kalender":
            if data.get("refresh"): _macro_cache["ts"] = 0
            cfg = load_config()
            blackout, mscore, soft, events = fetch_macro(cfg.get("finnhub_key",""))
            self._json({"events":events,"blackout":blackout,"macro_score":mscore})

        elif self.path == "/api/validate":
            try:
                bot_id = data.get("bot_id", "")
                # Im selben Modus testen, in dem die Bots laufen (vorher hart DEMO -> Live-Keys
                # zeigten faelschlich 0/Fehler).
                _cfg   = load_config()
                live   = _cfg.get("live_mode", False)
                # Secret/Passphrase (und Key) werden aus Sicherheitsgruenden NICHT ins Formular
                # zurueckgefuellt. Leere Felder -> gespeicherte Werte nehmen, damit "Verbindung
                # testen" auch ohne Neu-Eintippen funktioniert (sonst 40012 apikey/password).
                _saved = _cfg.get("bots", {}).get(bot_id, {}) if bot_id else {}
                client = BitgetClient(
                    data.get("api_key","")     or _saved.get("api_key",""),
                    data.get("api_secret","")  or _saved.get("api_secret",""),
                    data.get("passphrase","")  or _saved.get("passphrase",""),
                    live_mode=live
                )
                if bot_id == "dca":
                    # DCA nutzt Spot-Markt, nicht Futures
                    spot = client.spot_balance("USDT")
                    fut  = client.balance(retries=1)
                    ok   = True
                    msg  = f"Verbindung OK - Spot: {spot:.2f} USDT | Futures: {fut:.2f} USDT"
                else:
                    ok, msg = client.validate()
                self._json({"status":"ok" if ok else "error","msg":msg})
            except Exception as e:
                self._json({"status":"error","msg":str(e)})

        else:
            self._json({"status":"not found"},404)

def start_server():
    ThreadingHTTPServer(("0.0.0.0", DASHBOARD_PORT), Handler).serve_forever()

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    log.info("="*55)
    log.info("  Trading Platform | Signal | Grid | DCA")
    log.info("="*55)

    cfg = load_config()
    if sys.stdin.isatty() and not _credentials_just_created:
        _verify_login_at_startup(cfg)
    log.info(f"Config: {CONFIG_FILE}")
    log.info(f"Modus: {'LIVE' if cfg.get('live_mode') else 'DEMO'}")
    log.info(f"Dashboard: http://localhost:{DASHBOARD_PORT}")

    # Init Telegram
    tg_init(cfg.get("telegram_token",""), cfg.get("telegram_chat_id",""))

    # Sync live_mode into pstate
    with plock:
        pstate["live_mode"] = cfg.get("live_mode", False)

    init_db()
    threading.Thread(target=start_server, daemon=True, name="dashboard").start()
    threading.Thread(target=daily_summary_thread, daemon=True, name="daily-summary").start()
    threading.Thread(target=alert_check_thread,   daemon=True, name="alerts").start()
    threading.Thread(target=volatility_circuit_breaker, daemon=True, name="circuit-breaker").start()

    # Auto-Start: Bots mit gesetztem 'autostart' nach (Neu-)Start automatisch hochfahren
    # (z.B. nach Stromausfall). Positionen sind zwischenzeitlich durch SL/TP auf Bitget
    # geschuetzt. Nur Bots mit hinterlegten Keys starten wirklich.
    for _bid in ("signal","grid","dca"):
        if cfg["bots"].get(_bid, {}).get("autostart"):
            _ok, _msg = start_bot(_bid)
            log.info(f"Auto-Start {_bid}: {_msg}")

    log.info("Platform bereit. Bots koennen im Dashboard gestartet werden.")
    log.info("Strg+C zum Beenden.")

    try:
        while True:
            time.sleep(60)
            for bid in ("signal","grid","dca"):
                if bid in bot_threads and not bot_threads[bid].is_alive():
                    with plock:
                        if pstate["bots"][bid]["status"] not in ("STOPPED","STOPPING","EMERGENCY STOP"):
                            pstate["bots"][bid]["status"] = "STOPPED"
    except KeyboardInterrupt:
        log.info("Platform gestoppt.")
