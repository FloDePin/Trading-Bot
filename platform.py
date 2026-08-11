"""
Trading Platform v1
Signal | Grid | Funding | DCA
Bitget Sub-Account Support | Demo & Live
"""

import time, json, hmac, hashlib, base64, logging, requests
import urllib.parse, threading, os, math, sqlite3, sys, secrets, uuid, getpass
import signal as _signal
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
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

# ─────────────────────────────────────────────
#  KONFIGURATION
# ─────────────────────────────────────────────
CONFIG_FILE    = "platform_config.json"
DB_FILE        = "platform.db"
DASHBOARD_PORT = 5000
BASE_URL       = "https://api.bitget.com"
PRODUCT_TYPE   = "USDT-FUTURES"
MARGIN_COIN    = "USDT"

# ─────────────────────────────────────────────
#  SQLITE – PERSISTENTE DATEN
# ─────────────────────────────────────────────
_db_lock = threading.Lock()

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
            conn.execute('INSERT INTO trades (ts,bot,symbol,side,entry,exit_price,pnl,fee,size) VALUES (?,?,?,?,?,?,?,?,?)',
                (int(time.time()*1000), bot, symbol, side, entry, exit_price,
                 round(pnl,4), round(fee,6), size))
            conn.commit(); conn.close()
    except Exception as e:
        log.debug(f"db_save_trade: {e}")

def db_save_pnl(bot, pnl, balance):
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            conn.execute('INSERT INTO pnl_snapshots (ts,bot,pnl,balance) VALUES (?,?,?,?)',
                (int(time.time()*1000), bot, round(pnl,4), round(balance,2)))
            conn.commit(); conn.close()
    except Exception as e:
        log.debug(f"db_save_pnl: {e}")

def db_get_pnl_history(bot, days=30):
    try:
        since = int((time.time() - days*86400)*1000)
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            rows = conn.execute(
                'SELECT ts,pnl FROM pnl_snapshots WHERE bot=? AND ts>? ORDER BY ts',
                (bot, since)).fetchall()
            conn.close()
        return [{"ts": r[0], "pnl": r[1]} for r in rows]
    except: return []

def db_get_trades(bot=None, limit=200):
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            if bot and bot != "all":
                rows = conn.execute(
                    'SELECT ts,bot,symbol,side,entry,exit_price,pnl,fee FROM trades WHERE bot=? ORDER BY ts DESC LIMIT ?',
                    (bot, limit)).fetchall()
            else:
                rows = conn.execute(
                    'SELECT ts,bot,symbol,side,entry,exit_price,pnl,fee FROM trades ORDER BY ts DESC LIMIT ?',
                    (limit,)).fetchall()
            conn.close()
        cols = ['ts','bot','symbol','side','entry','exit','pnl','fee']
        return [dict(zip(cols, r)) for r in rows]
    except: return []

def db_trade_timing():
    """PnL nach Stunde des Tages fuer Trade-Timing-Analyse."""
    try:
        with _db_lock:
            conn = sqlite3.connect(DB_FILE)
            rows = conn.execute('SELECT ts, pnl FROM trades').fetchall()
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
            "name": "Signal Bot", "enabled": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "tokens": ["SOLUSDT","ETHUSDT","XRPUSDT","DOGEUSDT"],
            "leverage": 3, "usdt_per_trade": 30,
            "risk_pct": 3.0,
            "use_risk_pct": True,
            "stop_loss_pct": 0.010, "take_profit_pct": 0.020,
            "use_atr_sl": True,
            "atr_sl_mult": 1.5, "atr_tp_mult": 2.5,
            "max_concurrent": 2,
            "use_correlation_filter": True, "max_correlation": 0.85,
            "use_adx_filter": True, "min_adx": 20,
            "use_orderbook_signal": True,
            "signal_threshold": 3, "check_interval": 30,
        },
        "grid": {
            "name": "Grid Bot", "enabled": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "symbol": "BTCUSDT", "upper_price": 0.0, "lower_price": 0.0,
            "grid_count": 10, "investment": 100.0, "check_interval": 10,
        },
        "funding": {
            "name": "Funding Bot", "enabled": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "watch": ["SOLUSDT","ETHUSDT","XRPUSDT","DOGEUSDT","BTCUSDT"],
            "min_funding_rate": 0.0003, "max_position_usdt": 200.0,
            "check_interval": 60,
        },
        "dca": {
            "name": "DCA Bot", "enabled": False,
            "api_key": "", "api_secret": "", "passphrase": "",
            "symbol": "BTCUSDT", "interval_hours": 24,
            "amount_per_buy": 20.0, "check_interval": 300,
        },
    }
}

_credentials_just_created = False  # verhindert doppelte Abfrage direkt nach der Ersteinrichtung

def _prompt_first_run_credentials():
    """Interaktive Ersteinrichtung: laesst den Nutzer Benutzername/Passwort selbst waehlen.
    Nur moeglich wenn ein echtes Terminal angehaengt ist (sonst Fallback auf Auto-Generierung,
    z.B. bei systemd/Hintergrund-Diensten ohne TTY)."""
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
    global _credentials_just_created
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
            user, pw = _prompt_first_run_credentials()
            data["dashboard_user"]     = user
            data["dashboard_password"] = pw
        else:
            data["dashboard_password"] = secrets.token_urlsafe(12)
        needs_save = True
        _credentials_just_created = True
        log.warning("="*55)
        log.warning(f"  Dashboard-Zugang: user='{data.get('dashboard_user','admin')}' "
                    f"password='{data['dashboard_password']}'")
        log.warning("  Bitte merken/aendern (Settings -> Dashboard-Zugang).")
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

# ─────────────────────────────────────────────
#  BITGET API CLIENT
# ─────────────────────────────────────────────
class BitgetClient:
    def __init__(self, api_key, api_secret, passphrase, live_mode=False):
        self.key   = api_key
        self.sec   = api_secret
        self.pass_ = passphrase
        self.live  = live_mode

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

    def balance(self, retries=4):
        for _ in range(retries):
            r = self.get("/api/v2/mix/account/accounts",
                {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN})
            try:
                for acc in r.get("data", []):
                    if acc.get("marginCoin") == MARGIN_COIN:
                        return float(acc.get("available", 0))
            except: pass
            time.sleep(2)
        return 0.0

    def price(self, symbol):
        r = self.get("/api/v2/mix/market/ticker",
            {"symbol": symbol, "productType": PRODUCT_TYPE})
        try: return float(r["data"][0]["lastPr"])
        except: return 0.0

    def klines(self, symbol, limit=100):
        r = self.get("/api/v2/mix/market/candles", {
            "symbol": symbol, "productType": PRODUCT_TYPE,
            "granularity": "1m", "limit": str(limit),
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
        r = self.get("/api/v2/mix/position/single-position", {
            "symbol": symbol, "productType": PRODUCT_TYPE,
            "marginCoin": MARGIN_COIN,
        })
        for pos in r.get("data", []):
            if float(pos.get("total", 0)) > 0: return pos
        return None

    def set_leverage(self, symbol, leverage):
        for side in ["long","short"]:
            self.post("/api/v2/mix/account/set-leverage", {
                "symbol": symbol, "productType": PRODUCT_TYPE,
                "marginCoin": MARGIN_COIN, "leverage": str(leverage),
                "holdSide": side,
            })

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
        """Testet die API-Verbindung. Gibt (ok, nachricht) zurueck."""
        try:
            bal = self.balance(retries=1)
            return True, f"Verbindung OK – Balance: {bal:.2f} USDT"
        except Exception as e:
            return False, f"Verbindungsfehler: {e}"

    # ── SPOT-MARKT METHODEN ───────────────────────────────────
    def spot_price(self, symbol):
        """Aktueller Spot-Preis (kein Auth noetig, aber Client-Methode fuer Konsistenz)."""
        r = self.get("/api/v2/spot/market/tickers", {"symbol": symbol})
        try: return float(r["data"][0]["lastPr"])
        except: return 0.0

    def spot_balance(self, coin):
        """Verfuegbares Guthaben einer Spot-Coin (z.B. 'BTC', 'USDT')."""
        r = self.get("/api/v2/spot/account/assets", {"coin": coin})
        try: return float(r["data"][0].get("available", 0))
        except: return 0.0

    def spot_buy(self, symbol, usdt_amount):
        """
        Spot Market-Kauf: kauft mit einem fixen USDT-Betrag.
        Bei Spot-Market-Buy ist 'size' die Quote-Currency (USDT).
        Gibt (ok: bool, qty_bought: float, error_msg: str) zurueck.
        """
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
    "BNB": "binancecoin", "MATIC": "matic-network", "DOT": "polkadot",
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
        now = datetime.utcnow()
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
                  "MATICUSDT","LTCUSDT","ATOMUSDT","NEARUSDT","AAVEUSDT"]
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
                    for i in range(1, len(closes)) if closes[i-1] > 0]
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

def fetch_coingecko_regime():
    """Markt-Regime von CoinGecko (oeffentlich, kein Key): BTC-Dominanz, Gesamt-Market-Cap
    (24h), und Trending-Coins. Fail-safe: bei Fehler letzter Cache/leer."""
    if _regime_cache["data"] and time.time() - _regime_cache["ts"] < 120:
        return _regime_cache["data"]
    out = {"btc_dominance": None, "eth_dominance": None,
           "total_mcap_usd": None, "mcap_change_24h": None, "trending": []}
    try:
        g = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        if g.status_code == 200:
            d = g.json().get("data", {})
            mcp = d.get("market_cap_percentage", {})
            out["btc_dominance"]   = round(float(mcp.get("btc", 0)), 2)
            out["eth_dominance"]   = round(float(mcp.get("eth", 0)), 2)
            out["total_mcap_usd"]  = float(d.get("total_market_cap", {}).get("usd", 0))
            out["mcap_change_24h"] = round(float(d.get("market_cap_change_percentage_24h_usd", 0)), 2)
    except Exception as e:
        log.debug(f"CG global: {e}")
    try:
        t = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if t.status_code == 200:
            for c in t.json().get("coins", [])[:7]:
                it = c.get("item", {})
                out["trending"].append({
                    "name":   it.get("name", ""),
                    "symbol": (it.get("symbol", "") or "").upper(),
                    "rank":   it.get("market_cap_rank"),
                })
    except Exception as e:
        log.debug(f"CG trending: {e}")
    _regime_cache.update({"data": out, "ts": time.time()})
    return out

def fetch_derivatives(bases=None):
    """Aggregierte Derivate-Daten (Open Interest, Funding, Long/Short, Liquidationen) von
    Coinalyze. Braucht einen kostenlosen API-Key (Settings). Nutzt Binance-Perps (`.A`),
    da dort alle Majors verfuegbar sind. Fail-safe: kein Key/Fehler -> {error, rows:[]}."""
    cfg = load_config()
    key = str(cfg.get("coinalyze_key", "")).strip()
    if not key:
        return {"error": "no_key", "rows": []}
    if _deriv_cache["data"] and time.time() - _deriv_cache["ts"] < 120:
        return _deriv_cache["data"]

    if not bases:
        toks  = cfg.get("bots", {}).get("signal", {}).get("tokens", [])
        bases = ["BTC", "ETH"] + [str(t).replace("USDT", "") for t in toks]
    seen  = set()
    bases = [b for b in bases if b and not (b in seen or seen.add(b))][:12]

    syms = [f"{b}USDT_PERP.A" for b in bases]
    csv  = ",".join(syms)
    hdr  = {"api_key": key}

    def _get(path, params):
        try:
            r = requests.get(f"https://api.coinalyze.net/v1/{path}",
                             params=params, headers=hdr, timeout=12)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                return {"__auth__": True}
            return []
        except Exception:
            return []

    oi_raw = _get("open-interest", {"symbols": csv, "convert_to_usd": "true"})
    if isinstance(oi_raw, dict) and oi_raw.get("__auth__"):
        return {"error": "bad_key", "rows": []}
    fr_raw = _get("funding-rate", {"symbols": csv})

    now, frm = int(time.time()), int(time.time()) - 3 * 86400
    ls_raw  = _get("long-short-ratio-history", {"symbols": csv, "interval": "daily", "from": frm, "to": now})
    liq_raw = _get("liquidation-history", {"symbols": csv, "interval": "daily", "from": frm, "to": now, "convert_to_usd": "true"})

    def _val_map(arr):
        m = {}
        if isinstance(arr, list):
            for d in arr:
                if isinstance(d, dict) and d.get("symbol") is not None:
                    m[d["symbol"]] = d.get("value")
        return m

    def _last_hist(arr):
        m = {}
        if isinstance(arr, list):
            for d in arr:
                if isinstance(d, dict) and d.get("history"):
                    m[d.get("symbol")] = d["history"][-1]
        return m

    oi, fr = _val_map(oi_raw), _val_map(fr_raw)
    ls, liq = _last_hist(ls_raw), _last_hist(liq_raw)

    def _num(d, *keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)):
                return v
        return None

    rows = []
    for b in bases:
        s   = f"{b}USDT_PERP.A"
        lsp = ls.get(s, {}) if isinstance(ls.get(s), dict) else {}
        lqp = liq.get(s, {}) if isinstance(liq.get(s), dict) else {}
        # Long/Short: Ratio bevorzugt (r), sonst aus long%/short% ableiten
        ratio = _num(lsp, "r", "ratio")
        long_pct = _num(lsp, "l", "long", "longs")
        short_pct = _num(lsp, "s", "short", "shorts")
        if ratio is None and long_pct is not None and short_pct not in (None, 0):
            try: ratio = round(long_pct / short_pct, 2)
            except Exception: ratio = None
        rows.append({
            "coin":      b,
            "oi_usd":    oi.get(s),
            "funding":   fr.get(s),
            "ratio":     ratio,
            "long_pct":  long_pct,
            "short_pct": short_pct,
            "liq_long":  _num(lqp, "l", "long", "longs"),
            "liq_short": _num(lqp, "s", "short", "shorts"),
        })

    result = {"rows": rows}
    _deriv_cache.update({"data": result, "ts": time.time()})
    return result

# ─────────────────────────────────────────────
#  ORDER-BOOK-DRUCK (Kauf/Verkauf, Bitget public)
# ─────────────────────────────────────────────
_ob_cache = {}

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

def fetch_orderbook_all(bases=None):
    """Order-Book-Druck fuer mehrere Coins (fuer das Dashboard-Panel)."""
    if not bases:
        cfg   = load_config()
        toks  = cfg.get("bots", {}).get("signal", {}).get("tokens", [])
        bases = ["BTC", "ETH"] + [str(t).replace("USDT", "") for t in toks]
    seen = set()
    bases = [b for b in bases if b and not (b in seen or seen.add(b))][:12]
    rows = []
    for b in bases:
        ob = fetch_orderbook_pressure(f"{b}USDT")
        rows.append({"coin": b,
                     "ratio":      (ob or {}).get("ratio"),
                     "bid_vol":    (ob or {}).get("bid_vol"),
                     "ask_vol":    (ob or {}).get("ask_vol"),
                     "spread_bps": (ob or {}).get("spread_bps")})
        time.sleep(0.05)
    return {"rows": rows}

# ─────────────────────────────────────────────
#  TRADE-HISTORIE (alle Sub-Accounts)
# ─────────────────────────────────────────────
_trades_cache = {"data": [], "ts": 0}

def fetch_all_trades(limit=100):
    if time.time() - _trades_cache["ts"] < 60:
        return _trades_cache["data"]
    cfg       = load_config()
    live      = cfg.get("live_mode", False)
    all_fills = []
    for bot_id in ("signal","grid","funding","dca"):
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
    for bot_id in ("signal","grid","funding","dca"):
        bc = cfg["bots"].get(bot_id, {})
        if not bc.get("api_key") or not bc.get("api_secret"): continue
        try:
            client = BitgetClient(bc["api_key"], bc["api_secret"],
                                  bc["passphrase"], live)
            r = client.get("/api/v2/mix/position/all-position", {
                "productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN
            })
            for pos in r.get("data", []):
                if float(pos.get("total", 0)) <= 0: continue
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
                              fee_rate=0.0004):
    """Core Backtest-Logik auf einem Candle-Array."""
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
                gross   = equity * 0.1 * leverage * (tp_pct_actual if hit_tp else -sl_pct_actual)
                fees    = equity * 0.1 * leverage * fee_rate * 2  # entry + exit
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
                    "result": "WIN" if hit_tp else "LOSS",
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
                 walk_forward=False):
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
            result     = _run_backtest_on_candles(test_raw, leverage, threshold, sl_pct, tp_pct)
            result["walk_forward"] = True
            result["train_pct"]    = 70
            result["test_pct"]     = 30
            result["test_candles"] = len(test_raw)
        else:
            result = _run_backtest_on_candles(raw, leverage, threshold, sl_pct, tp_pct)
            result["walk_forward"] = False

        result["symbol"]      = symbol
        result["period_days"] = period_days
        result["candles"]     = len(raw)
        return result
    except Exception as e:
        return {"error": str(e)}

def run_multi_backtest(symbols, period_days=14, leverage=3,
                       threshold=2, sl_pct=0.010, tp_pct=0.020):
    """Backtest auf mehreren Symbolen gleichzeitig."""
    results = {}
    for sym in symbols:
        results[sym] = run_backtest(sym, period_days, leverage, threshold, sl_pct, tp_pct)
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
                            # Funding Bot handelt nicht real - Schaetzung zaehlt nicht in den Alert
                            total = sum(pstate["bots"][b].get("pnl",0)
                                        for b in pstate["bots"] if b != "funding")
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
def fmt_q(sym, q):
    mq = MIN_QTY.get(sym, 0.1)
    if q < mq: q = mq
    return str(int(q)) if sym in ("XRPUSDT","DOGEUSDT") else f"{q:.1f}"

pstate = {
    "bots": {
        "signal":  {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,"pnl_pct":0.0,
                    "trade_count":0,"wins":0,"logs":[],"tokens":{},"blackout":False,
                    "macro_events":[],"last_update":""},
        "grid":    {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,
                    "trade_count":0,"filled":0,"logs":[],"grid_orders":[],
                    "symbol":"","upper":0,"lower":0,"last_update":""},
        "funding": {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,
                    "earned":0.0,"logs":[],"rates":{},"opportunities":[],"last_update":""},
        "dca":     {"status":"STOPPED","balance":0.0,"start_bal":0.0,"pnl":0.0,
                    "invested":0.0,"buys":0,"avg_price":0.0,"next_buy":"","logs":[],"last_update":""},
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
    thresh       = bc.get("signal_threshold", 3)
    check        = bc.get("check_interval", 30)
    fkey         = cfg.get("finnhub_key","")
    fee_rate     = 0.0004  # Bitget Taker Fee (0.04%)
    win_streak   = 0
    loss_streak  = 0

    with plock:
        for t in tokens:
            pstate["bots"]["signal"]["tokens"][t] = {
                "signal":"NEUTRAL","score":0,"rsi":0,"ema_fast":0,"ema_slow":0,
                "macd":0,"macd_signal":0,"volume_ratio":1,"funding_rate":0,
                "bb_upper":0,"bb_lower":0,"atr":0,"adx":0,"ob_ratio":None,
                "fear_greed":50,"sentiment":"neutral","position":None,
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
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal})
    blog("signal", f"Start: {start_bal:.2f} USDT | ATR-SL: {'ja' if use_atr_sl else 'nein'} | Risk: {risk_pct if use_risk_pct else usdt_pt} {'%' if use_risk_pct else 'USDT'}")

    while not flag["stop"]:
        try:
            bal = client.balance(retries=3) or start_bal
            pnl = bal - start_bal
            pct = pnl / start_bal if start_bal > 0 else 0
            db_save_pnl("signal", pnl, bal)
            with plock:
                pstate["bots"]["signal"].update({
                    "balance":round(bal,2),"pnl":round(pnl,2),
                    "pnl_pct":round(pct*100,2),
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
            if start_bal > 0 and pct <= -0.02:
                blog("signal","Tageslimit erreicht. Pause 1h.","WARN")
                with plock: pstate["bots"]["signal"]["status"] = "PAUSED"
                pause_until = time.time() + 3600
                while time.time() < pause_until and not flag["stop"]:
                    time.sleep(5)
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

            # Aktuell offene Positionen (Symbol + Richtung) fuer Limit- und Korrelations-Check
            open_positions = []
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

            for sym in tokens:
                try:
                    _, highs, lows, closes, vols = client.klines(sym, 100)
                    if len(closes) < 30: continue
                    rv       = rsi(closes, 14)
                    ef       = ema(closes, 8)
                    es       = ema(closes, 20)
                    ml,ms    = macd_calc(closes)
                    vr       = vol_ratio(vols)
                    atr_val  = atr(highs, lows, closes, 14)
                    bb_u, bb_m, bb_l = bollinger(closes, 20, 2.0)
                    fr       = client.funding_rate(sym)
                    fg       = fear_greed()
                    cur      = sym.replace("USDT","")
                    sent     = news_sentiment(cur)

                    sc = 0
                    sc += 1 if ef > es else -1
                    if rv < 38:   sc += 1
                    elif rv > 62: sc -= 1
                    sc += 1 if ml > ms else -1
                    # Bollinger Bands: Preis nahe unterem Band = bullish, oberem = bearish
                    price_now = closes[-1]
                    if price_now < bb_l:  sc += 1
                    elif price_now > bb_u: sc -= 1
                    if vr > 1.2:  sc += 1 if ef > es else -1
                    elif vr < 0.7: sc = int(sc * 0.5)
                    if fr > 0.0003: sc -= 1
                    elif fr < -0.0003: sc += 1
                    if fg < 30: sc += 1
                    elif fg > 70: sc -= 1
                    if sent == "bullish":  sc += 1
                    elif sent == "bearish": sc -= 1
                    sc += max(-1, min(1, mscore))
                    sc += max(-2, min(0, ssoft))

                    # Order-Book-Kaufdruck als zusaetzlicher Faktor (fail-open)
                    ob_ratio = None
                    if use_ob:
                        ob = fetch_orderbook_pressure(sym)
                        if ob and ob.get("ratio"):
                            ob_ratio = ob["ratio"]
                            if   ob_ratio >= 1.3:  sc += 1   # klarer Kaufdruck
                            elif ob_ratio <= 0.77: sc -= 1   # klarer Verkaufsdruck

                    # ADX-Trendfilter: bei schwachem Trend Signal daempfen (kein Handel im Gezappel).
                    # Fail-open: ADX==0 (zu wenig Daten) -> keine Daempfung.
                    adx_val = adx(highs, lows, closes, 14)
                    if use_adx and 0 < adx_val < min_adx:
                        sc = int(sc * 0.5)

                    sig = "LONG" if sc >= thresh else "SHORT" if sc <= -thresh else "NEUTRAL"
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
                        })
                    blog("signal",f"{cur}: RSI={rv:.1f} ADX={adx_val:.0f} OB={ob_ratio if ob_ratio else '-'} BB={'low' if price_now<bb_l else 'high' if price_now>bb_u else 'mid'} Score={sc:+d} -> {sig}")

                    pos = client.position(sym)
                    with plock:
                        pstate["bots"]["signal"]["tokens"][sym]["position"] = pos

                    # Dynamische Position-Groesse
                    trade_usdt = (bal * risk_pct / 100) if use_risk_pct else usdt_pt

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
                        if open_pos_count >= max_conc:
                            blog("signal",f"{cur}: Max. Positionen ({max_conc}) erreicht – kein neuer Trade","WARN")
                            return
                        # Korrelations-Filter: keine neue Position, die zu stark mit einer
                        # bereits offenen zusammenhaengt (geballtes Risiko). Fail-open.
                        if use_corr_filter and corr_data:
                            conflict = _correlation_conflict(sym, direction, corr_data, open_positions, max_corr)
                            if conflict:
                                blog("signal",f"{cur}: uebersprungen – zu stark korreliert mit offener {conflict}-Position (r>={max_corr:.2f})","WARN")
                                return
                        px = client.price(sym)
                        if px <= 0: return
                        qs   = fmt_q(sym, (trade_usdt * lever) / px)
                        sl, tp = calc_sl_tp(px, direction)
                        resp = client.post("/api/v2/mix/order/place-order", {
                            "symbol":sym,"productType":PRODUCT_TYPE,
                            "marginMode":"isolated","marginCoin":MARGIN_COIN,
                            "size":qs,"side":"buy" if direction=="LONG" else "sell",
                            "tradeSide":"open","orderType":"market","force":"ioc",
                            "presetStopSurplusPrice":fmt_p(sym,tp),
                            "presetStopLossPrice":fmt_p(sym,sl),
                        })
                        if resp.get("code") == "00000":
                            open_pos_count += 1
                            # neue Position in die Zyklus-Liste aufnehmen, damit spaetere
                            # Symbole im selben Zyklus den Korrelations-Check gegen sie sehen
                            open_positions.append((sym, direction))
                            with plock:
                                pstate["bots"]["signal"]["trade_count"] += 1
                            blog("signal",f"{cur}: {direction} @ {px:.2f} | SL={sl:.2f} TP={tp:.2f} ({trade_usdt:.0f} USDT)","TRADE")

                    if pos:
                        ps = "LONG" if pos["holdSide"]=="long" else "SHORT"
                        if (ps=="LONG" and sig=="SHORT") or (ps=="SHORT" and sig=="LONG"):
                            client.post("/api/v2/mix/order/place-order", {
                                "symbol":sym,"productType":PRODUCT_TYPE,
                                "marginMode":"isolated","marginCoin":MARGIN_COIN,
                                "size":str(pos["total"]),
                                "side":"sell" if pos["holdSide"]=="long" else "buy",
                                "tradeSide":"close","orderType":"market","force":"ioc",
                            })
                            open_pos_count = max(0, open_pos_count - 1)
                            open_positions[:] = [(s,d) for (s,d) in open_positions if s != sym]
                            blog("signal",f"{cur}: Position gedreht","TRADE")
                            if not blackout: _open(sig)
                    else:
                        # Pruefe ob eine zuvor offene Position seit dem letzten Zyklus
                        # durch SL/TP (oder manuell) geschlossen wurde
                        prev_pos = pstate["bots"]["signal"]["tokens"][sym].get("_last_pos")
                        if prev_pos:
                            ps    = "LONG" if prev_pos["holdSide"]=="long" else "SHORT"
                            entry = float(prev_pos.get("openPriceAvg",0))
                            upnl  = float(prev_pos.get("unrealizedPL",0))
                            if upnl > 0:
                                win_streak += 1; loss_streak = 0
                                db_save_trade("signal", cur, ps, entry, 0, upnl*(1-fee_rate))
                            else:
                                loss_streak += 1; win_streak = 0
                                db_save_trade("signal", cur, ps, entry, 0, upnl*(1+fee_rate))
                            with plock:
                                pstate["bots"]["signal"].update({
                                    "win_streak":win_streak, "loss_streak":loss_streak})
                            open_pos_count = max(0, open_pos_count - 1)
                            open_positions[:] = [(s,d) for (s,d) in open_positions if s != sym]
                        if sig in ("LONG","SHORT") and not blackout:
                            _open(sig)
                    with plock:
                        pstate["bots"]["signal"]["tokens"][sym]["_last_pos"] = pos
                    time.sleep(0.5)
                except Exception as e:
                    blog("signal",f"{sym}: {e}","ERROR")
        except Exception as e:
            blog("signal",f"Loop: {e}","ERROR")
        time.sleep(check)

    with plock: pstate["bots"]["signal"]["status"] = "STOPPED"
    blog("signal","Gestoppt.")

# ─────────────────────────────────────────────
#  GRID BOT
# ─────────────────────────────────────────────
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

    start_bal = client.balance(retries=5)
    client.fetch_market_precision(TICK_DEC, MIN_QTY)
    cur_price = client.price(sym)

    if upper == 0 or lower == 0 or upper <= lower:
        if cur_price > 0:
            upper = cur_price * 1.05
            lower = cur_price * 0.95
            blog("grid",f"Auto-Range: {lower:.2f} - {upper:.2f}")

    step    = (upper - lower) / n
    levels  = [lower + i * step for i in range(n + 1)]
    qty_lvl = (invest / n) / ((upper + lower) / 2)
    filled  = [False] * (n + 1)
    pnl     = 0.0
    net_qty = 0.0  # aktuell durch diesen Grid-Bot gehaltene Long-Menge (lokale Buchhaltung)

    with plock:
        pstate["bots"]["grid"].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,
            "symbol":sym,"upper":round(upper,2),"lower":round(lower,2),
            "grid_orders":[{"price":round(l,2),"filled":False,"side":"BUY" if l<=(upper+lower)/2 else "SELL"} for l in levels],
        })
    blog("grid",f"Grid aktiv: {sym} | {n} Levels | {lower:.2f} - {upper:.2f} USDT")

    while not flag["stop"]:
        try:
            px = client.price(sym)
            if px <= 0: time.sleep(check); continue

            for i, level in enumerate(levels):
                if abs(px - level) / level < 0.002 and not filled[i]:
                    filled[i] = True
                    side = "BUY" if px <= (upper+lower)/2 else "SELL"

                    if side == "SELL" and net_qty <= 0:
                        # Kein offener Bestand aus diesem Grid zum Verkaufen -
                        # Level wird uebersprungen statt einen naked Short zu eroeffnen
                        with plock:
                            pstate["bots"]["grid"]["grid_orders"][i]["filled"] = True
                        blog("grid",f"Grid SELL @ {level:.2f} [Level {i+1}/{n}] uebersprungen - kein offener Bestand","WARN")
                        continue

                    if side == "BUY":
                        order_side, trade_side = "buy", "open"
                        qty_trade = qty_lvl
                    else:
                        order_side, trade_side = "sell", "close"
                        qty_trade = min(qty_lvl, net_qty)
                    qty_str = fmt_q(sym, qty_trade)
                    # Market-Order: sofortige Ausfuehrung zum aktuellen Preis,
                    # kein Risiko dass der Wick vorbei ist bevor die Limit-Order greift
                    resp = client.post("/api/v2/mix/order/place-order", {
                        "symbol": sym, "productType": PRODUCT_TYPE,
                        "marginMode":"isolated","marginCoin":MARGIN_COIN,
                        "size": qty_str, "side": order_side,
                        "tradeSide": trade_side, "orderType":"market","force":"ioc",
                    })
                    ok = resp.get("code") == "00000"
                    if ok:
                        if side == "BUY":
                            net_qty += qty_lvl
                        else:
                            pnl     += qty_trade * step
                            net_qty  = max(0.0, net_qty - qty_trade)
                    with plock:
                        pstate["bots"]["grid"]["grid_orders"][i]["filled"] = True
                        pstate["bots"]["grid"]["filled"]      = sum(filled)
                        pstate["bots"]["grid"]["trade_count"] = sum(filled)
                        pstate["bots"]["grid"]["pnl"]         = round(pnl,4)
                    status = "✓" if ok else f"Fehler {resp.get('msg','')}"
                    blog("grid",f"Grid {side} @ {level:.2f} [Level {i+1}/{n}] {status}",
                         "TRADE" if ok else "ERROR")
                elif filled[i] and abs(px - level) / level > 0.005:
                    filled[i] = False
                    with plock:
                        pstate["bots"]["grid"]["grid_orders"][i]["filled"] = False

            bal = client.balance(retries=2) or start_bal
            with plock:
                pstate["bots"]["grid"].update({
                    "balance":round(bal,2),
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
        except Exception as e:
            blog("grid",f"Loop: {e}","ERROR")
        time.sleep(check)

    with plock: pstate["bots"]["grid"]["status"] = "STOPPED"
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
    log.info(f"[grid:{name}] {msg}")

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

    start_bal = client.balance(retries=5)
    client.fetch_market_precision(TICK_DEC, MIN_QTY)
    cur_price = client.price(sym)

    if upper == 0 or lower == 0 or upper <= lower:
        if cur_price > 0:
            upper = cur_price * 1.05
            lower = cur_price * 0.95
            _ilog(inst_id, name, f"Auto-Range: {lower:.2f} - {upper:.2f}")

    step   = (upper - lower) / n
    levels = [lower + i * step for i in range(n+1)]
    qty_l  = (invest / n) / ((upper + lower) / 2)
    filled = [False] * (n+1)
    pnl    = 0.0
    net_qty = 0.0  # aktuell durch diese Grid-Instanz gehaltene Long-Menge (lokale Buchhaltung)

    with plock:
        pstate["grid_instances"][inst_id].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,
            "symbol":sym,"upper":round(upper,2),"lower":round(lower,2),
            "grid_orders":[{"price":round(l,2),"filled":False,
                            "side":"BUY" if l<=(upper+lower)/2 else "SELL"}
                           for l in levels],
        })
    _ilog(inst_id, name, f"Grid aktiv: {sym} | {n} Levels | {lower:.2f}-{upper:.2f}")

    while not flag["stop"]:
        try:
            px = client.price(sym)
            if px <= 0: time.sleep(check); continue

            for i, level in enumerate(levels):
                if abs(px - level) / level < 0.002 and not filled[i]:
                    filled[i]  = True
                    side_label = "BUY" if px <= (upper+lower)/2 else "SELL"

                    if side_label == "SELL" and net_qty <= 0:
                        with plock:
                            gi = pstate["grid_instances"].get(inst_id,{})
                            if "grid_orders" in gi and i < len(gi["grid_orders"]):
                                gi["grid_orders"][i]["filled"] = True
                        _ilog(inst_id, name, f"Grid SELL @ {level:.2f} L{i+1}/{n} uebersprungen - kein offener Bestand","WARN")
                        continue

                    if side_label == "BUY":
                        order_side, trade_side = "buy", "open"
                        qty_trade = qty_l
                    else:
                        order_side, trade_side = "sell", "close"
                        qty_trade = min(qty_l, net_qty)
                    resp = client.post("/api/v2/mix/order/place-order", {
                        "symbol":sym,"productType":PRODUCT_TYPE,
                        "marginMode":"isolated","marginCoin":MARGIN_COIN,
                        "size":fmt_q(sym, qty_trade),"side":order_side,
                        "tradeSide":trade_side,"orderType":"market","force":"ioc",
                    })
                    ok = resp.get("code") == "00000"
                    if ok:
                        if side_label == "BUY":
                            net_qty += qty_l
                        else:
                            pnl     += qty_trade * step
                            net_qty  = max(0.0, net_qty - qty_trade)
                    with plock:
                        gi = pstate["grid_instances"].get(inst_id,{})
                        if "grid_orders" in gi and i < len(gi["grid_orders"]):
                            gi["grid_orders"][i]["filled"] = True
                        gi["filled"]      = sum(filled)
                        gi["trade_count"] = sum(filled)
                        gi["pnl"]         = round(pnl,4)
                    istatus = "OK" if ok else f"Fehler {resp.get('msg','')}"
                    _ilog(inst_id, name, f"Grid {side_label} @ {level:.2f} L{i+1}/{n} {istatus}",
                          "TRADE" if ok else "ERROR")
                elif filled[i] and abs(px - level)/level > 0.005:
                    filled[i] = False
                    with plock:
                        gi = pstate["grid_instances"].get(inst_id,{})
                        if "grid_orders" in gi and i < len(gi["grid_orders"]):
                            gi["grid_orders"][i]["filled"] = False

            bal = client.balance(retries=2) or start_bal
            with plock:
                pstate["grid_instances"].get(inst_id,{}).update({
                    "balance":round(bal,2),
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
        except Exception as e:
            _ilog(inst_id, name, f"Loop: {e}", "ERROR")
        time.sleep(check)

    with plock:
        pstate["grid_instances"].get(inst_id,{}).update({"status":"STOPPED"})
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
#  FUNDING BOT
# ─────────────────────────────────────────────
def run_funding(flag):
    cfg    = load_config()
    bc     = cfg["bots"]["funding"]
    client = BitgetClient(bc["api_key"], bc["api_secret"], bc["passphrase"],
                          cfg.get("live_mode", False))
    watch  = bc.get("watch",["SOLUSDT","ETHUSDT","XRPUSDT","DOGEUSDT","BTCUSDT"])
    min_fr = float(bc.get("min_funding_rate",0.0003))
    max_p  = float(bc.get("max_position_usdt",200))
    check  = int(bc.get("check_interval",60))

    start_bal = client.balance(retries=5)
    total_earned = 0.0

    with plock:
        pstate["bots"]["funding"].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal})
    blog("funding",f"Aktiv | Min FR: {min_fr*100:.4f}% | Max: {max_p} USDT | "
                   f"HINWEIS: Beobachtungs-Modus, dieser Bot platziert KEINE echten Orders. "
                   f"'Verdient' ist eine Schaetzung.","WARN")

    while not flag["stop"]:
        try:
            rates = {}
            opps  = []
            for sym in watch:
                fr = client.funding_rate(sym)
                cur = sym.replace("USDT","")
                rates[cur] = round(fr * 100, 6)
                if abs(fr) >= min_fr:
                    est_8h = abs(fr) * max_p
                    direction = "Short Futures / Long Spot" if fr > 0 else "Long Futures / Short Spot"
                    opps.append({
                        "symbol": cur, "rate": round(fr*100,4),
                        "est_8h": round(est_8h,4), "direction": direction,
                    })
                    total_earned += est_8h * (check / 28800)
                    blog("funding",f"{cur}: FR={fr*100:.4f}% → {direction} | ~{est_8h:.4f} USDT/8h")
                time.sleep(0.2)

            bal = client.balance(retries=2) or start_bal
            with plock:
                pstate["bots"]["funding"].update({
                    "balance":round(bal,2),"rates":rates,
                    "opportunities":opps,"earned":round(total_earned,4),
                    "pnl":round(total_earned,4),
                    "last_update":datetime.now().strftime("%H:%M:%S"),
                })
        except Exception as e:
            blog("funding",f"Loop: {e}","ERROR")
        time.sleep(check)

    with plock:
        pstate["bots"]["funding"]["status"] = "STOPPED"
        pstate["bots"]["funding"]["earned"]  = 0.0
        pstate["bots"]["funding"]["pnl"]     = 0.0
    blog("funding","Gestoppt.")

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
    total_inv = 0.0
    total_qty = 0.0
    buy_count = 0
    last_buy  = 0.0
    pnl       = 0.0

    with plock:
        pstate["bots"]["dca"].update({
            "status":"RUNNING","balance":start_bal,"start_bal":start_bal,
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

    with plock: pstate["bots"]["dca"]["status"] = "STOPPED"
    blog("dca","Gestoppt.")

# ─────────────────────────────────────────────
#  NOTFALL-STOPP (PANIC BUTTON)
# ─────────────────────────────────────────────
def emergency_stop():
    """Stoppt alle Bots, storniert offene Orders und schliesst alle Positionen per Market."""
    log.warning("!!! NOTFALL-STOPP AUSGELOEST !!!")
    notify("[NOTFALL-STOPP] Alle Positionen werden geschlossen.", True)

    # Alle Bot-Threads stoppen
    for bid in ("signal","grid","funding","dca"):
        if bid in bot_flags:
            bot_flags[bid]["stop"] = True
        with plock:
            pstate["bots"][bid]["status"] = "EMERGENCY STOP"

    cfg  = load_config()
    live = cfg.get("live_mode", False)
    closed, errors = 0, 0

    for bid in ("signal","grid","funding","dca"):
        bc = cfg["bots"].get(bid, {})
        if not bc.get("api_key") or not bc.get("api_secret"):
            continue
        try:
            client = BitgetClient(bc["api_key"], bc["api_secret"], bc["passphrase"], live)

            # Alle offenen Positionen abfragen und schliessen
            r = client.get("/api/v2/mix/position/all-position",
                {"productType": PRODUCT_TYPE, "marginCoin": MARGIN_COIN})
            for pos in r.get("data", []):
                if float(pos.get("total", 0)) <= 0:
                    continue
                sym = pos.get("symbol","")
                ok, last_msg = False, ""
                for close_attempt in range(3):
                    resp = client.post("/api/v2/mix/order/place-order", {
                        "symbol": sym, "productType": PRODUCT_TYPE,
                        "marginMode":"isolated","marginCoin":MARGIN_COIN,
                        "size": str(pos["total"]),
                        "side": "sell" if pos["holdSide"]=="long" else "buy",
                        "tradeSide":"close","orderType":"market","force":"ioc",
                    })
                    if resp.get("code") == "00000":
                        ok = True
                        break
                    last_msg = resp.get("msg","")
                    time.sleep(1)
                if ok:
                    closed += 1
                    log.info(f"[PANIC] {bid}: {sym} {pos['holdSide']} geschlossen")
                else:
                    errors += 1
                    log.error(f"[PANIC] {bid}: {sym} Fehler nach 3 Versuchen: {last_msg}")
                    notify(f"[NOTFALL-STOPP] FEHLER: {bid} {sym} konnte nicht geschlossen werden: {last_msg}", True)

            # Alle offenen Limit-Orders stornieren (z.B. Grid-Orders)
            for sym in list(TICK_DEC.keys()):
                client.post("/api/v2/mix/order/cancel-all",
                    {"symbol": sym, "productType": PRODUCT_TYPE,
                     "marginCoin": MARGIN_COIN})
        except Exception as e:
            errors += 1
            log.error(f"[PANIC] {bid}: {e}")

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
                # Funding Bot fuehrt keine echten Trades aus (reine Rate-Beobachtung) -
                # sein "pnl" ist eine Schaetzung und wird aus der echten Gesamt-PnL ausgeschlossen.
                real_bots = [b for b in bots if b != "funding"]
                total_pnl = sum(bots[b].get("pnl",0) for b in real_bots)
                funding_est = bots.get("funding",{}).get("pnl",0)
                active    = sum(1 for b in bots if bots[b].get("status")=="RUNNING")
                trades    = sum(bots[b].get("trade_count",0) for b in bots)

            notify(
                f"[DAILY SUMMARY] {datetime.now().strftime('%d.%m.%Y')}\n"
                f"Modus: {'LIVE' if pstate.get('live_mode') else 'DEMO'}\n"
                f"PnL gesamt (Signal/Grid/DCA): {total_pnl:+.2f} USDT\n"
                f"Funding Bot Schaetzung: {funding_est:+.2f} USDT (kein echter Trade)\n"
                f"Aktive Bots: {active}/4\n"
                f"Trades heute: {trades}"
            )
        except Exception as e:
            log.debug(f"daily_summary: {e}")
        time.sleep(60)  # Verhindert Doppelsendung in derselben Minute

# ─────────────────────────────────────────────
#  BOT MANAGER
# ─────────────────────────────────────────────
RUNNERS = {"signal":run_signal,"grid":run_grid,"funding":run_funding,"dca":run_dca}

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
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trading Platform v1</title>
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
  <div class="nav-brand">TRADING PLATFORM v1</div>
  <button class="tab" data-tab="overview" onclick="switchTab('overview')">OVERVIEW</button>
  <button class="tab" data-tab="signal" data-bot="signal" onclick="switchTab('signal')">
    SIGNAL<span class="status-dot dot-stop" id="dot-signal"></span>
  </button>
  <button class="tab" data-tab="grid" data-bot="grid" onclick="switchTab('grid')">
    GRID<span class="status-dot dot-stop" id="dot-grid"></span>
  </button>
  <button class="tab" data-tab="funding" data-bot="funding" onclick="switchTab('funding')">
    FUNDING<span class="status-dot dot-stop" id="dot-funding"></span>
  </button>
  <button class="tab" data-tab="dca" data-bot="dca" onclick="switchTab('dca')">
    DCA<span class="status-dot dot-stop" id="dot-dca"></span>
  </button>
  <button class="tab" data-tab="markt" onclick="switchTab('markt')">MARKT</button>
  <button class="tab" data-tab="trades" onclick="switchTab('trades')">TRADES</button>
  <button class="tab" data-tab="backtest" onclick="switchTab('backtest')">BACKTEST</button>
  <button class="tab" data-tab="correlation" onclick="switchTab('correlation')">KORRELATION</button>
  <button class="tab" data-tab="derivate" onclick="switchTab('derivate')">DERIVATE</button>
  <button class="tab" data-tab="alerts" onclick="switchTab('alerts')">ALERTS</button>
  <button class="tab" data-tab="settings" onclick="switchTab('settings')">SETTINGS</button>
  <button id="lang-btn" onclick="toggleLang()" style="margin-left:auto;background:var(--dim);border:1px solid var(--border);color:var(--muted);font-family:inherit;font-size:10px;padding:5px 12px;border-radius:4px;cursor:pointer;white-space:nowrap">DE / EN</button>
  <div style="flex:1"></div>
  <span class="mode-badge mode-demo" id="mode-badge">DEMO</span>
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
    <div class="card"><div class="card-label" data-i18n="total_pnl_nofund">Gesamt PnL (ohne Funding)</div>
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
        <option value="funding">Funding Bot</option>
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
        <div><div class="card-label" style="margin-bottom:4px">Investment (USDT)</div>
          <input type="number" id="ng-inv" value="100" min="10" style="background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:7px 10px;border-radius:5px;width:100%"></div>
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

<!-- FUNDING BOT -->
<div id="panel-funding" class="panel">
  <div class="bot-header">
    <div>
      <div class="bot-title" style="color:var(--funding)">FUNDING BOT</div>
      <div class="bot-meta" data-i18n="meta_funding">Beobachtungs-Modus: zeigt Funding-Rate-Opportunities, platziert aber KEINE echten Orders. "Verdient" ist eine Schaetzung.</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <button class="btn-help" onclick="showHelp('funding')" title="Erklaerung">?</button>
      <span id="funding-status-badge" class="ov-status s-stopped">STOPPED</span>
      <button class="btn btn-start" style="--accent:var(--funding)" id="funding-btn" onclick="toggleBot('funding')">START</button>
    </div>
  </div>
  <div class="grid g3" style="margin-bottom:14px">
    <div class="card"><div class="card-label" data-i18n="balance">Balance</div>
      <div class="card-value blue" id="f-balance">0.00</div><div class="card-sub">USDT</div></div>
    <div class="card"><div class="card-label" data-i18n="earned">Verdient (est.)</div>
      <div class="card-value green" id="f-earned">0.0000</div><div class="card-sub" data-i18n="usdt_funding">USDT Funding</div></div>
    <div class="card"><div class="card-label" data-i18n="opportunities">Opportunities</div>
      <div class="card-value white" id="f-opps">0</div><div class="card-sub" data-i18n="over_thresh">Ueber Schwelle</div></div>
  </div>
  <div class="pnl-card">
    <div class="pnl-card-label"><span data-i18n="funding_cum">Kumulierter Funding-Ertrag</span><span id="f-trend" class="trend-flat">- -</span></div>
    <svg class="spark" id="f-spark" viewBox="0 0 400 40" preserveAspectRatio="none"></svg>
  </div>
  <div class="rate-table">
    <div class="rt-head"><span>Symbol</span><span>Funding Rate</span><span>Est. / 8h</span><span class="rt-col-dir" data-i18n="strategy">Strategie</span></div>
    <div id="f-rates"></div>
  </div>
  <div class="log-wrap">
    <div class="log-head"><span>Funding Bot Log</span><span id="f-logcount"></span></div>
    <div class="log-body" id="f-log"></div>
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

<!-- KORRELATION -->
<div id="panel-correlation" class="panel">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;flex-wrap:wrap;gap:8px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="corr_title">KORRELATIONS-MATRIX</span>
    <div style="display:flex;gap:8px;align-items:center">
      <label style="font-size:10px;color:var(--muted)" data-i18n="corr_period">Zeitraum</label>
      <select id="corr-period" onchange="loadCorrelation()" style="background:var(--bg3);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:11px;padding:4px 8px;border-radius:4px">
        <option value="14">14 T</option>
        <option value="30" selected>30 T</option>
        <option value="60">60 T</option>
        <option value="90">90 T</option>
      </select>
      <button class="btn" onclick="loadCorrelation()" style="--accent:var(--signal);padding:5px 12px" data-i18n="corr_refresh">Aktualisieren</button>
    </div>
  </div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:12px;line-height:1.5" data-i18n="corr_hint">
    Korrelation der Tagesrenditen deiner Signal-Bot-Coins. Hohe positive Werte (rot) = die Coins bewegen sich gemeinsam &rarr; gleichzeitige Positionen erhoehen dein Risiko. Niedrige/negative Werte (gruen) = bessere Diversifikation.
  </div>
  <div id="corr-status" style="font-size:11px;color:var(--muted);padding:20px;text-align:center">Lade...</div>
  <div id="corr-matrix" style="overflow-x:auto"></div>
  <div style="display:flex;gap:14px;align-items:center;margin-top:14px;font-size:10px;color:var(--muted);flex-wrap:wrap">
    <span data-i18n="corr_legend">Legende:</span>
    <span><span style="display:inline-block;width:12px;height:12px;background:var(--red);border-radius:2px;vertical-align:middle"></span> &ge; 0.7 stark</span>
    <span><span style="display:inline-block;width:12px;height:12px;background:var(--dca);border-radius:2px;vertical-align:middle"></span> 0.4&ndash;0.7 mittel</span>
    <span><span style="display:inline-block;width:12px;height:12px;background:var(--dim);border-radius:2px;vertical-align:middle"></span> &plusmn;0.4 gering</span>
    <span><span style="display:inline-block;width:12px;height:12px;background:var(--signal);border-radius:2px;vertical-align:middle"></span> &le; -0.4 negativ</span>
  </div>
</div>

<!-- DERIVATE + MARKT-REGIME -->
<div id="panel-derivate" class="panel">

  <!-- Markt-Regime (CoinGecko) -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="reg_title">MARKT-REGIME (COINGECKO)</span>
    <button class="btn" onclick="loadDerivate()" style="--accent:var(--signal);padding:5px 12px" data-i18n="reg_refresh">Aktualisieren</button>
  </div>
  <div class="grid g4" style="margin-bottom:10px">
    <div class="card"><div class="card-label" data-i18n="reg_btc_dom">BTC-Dominanz</div>
      <div class="card-value white" id="reg-btc">–</div><div class="card-sub">%</div></div>
    <div class="card"><div class="card-label" data-i18n="reg_eth_dom">ETH-Dominanz</div>
      <div class="card-value white" id="reg-eth">–</div><div class="card-sub">%</div></div>
    <div class="card"><div class="card-label" data-i18n="reg_mcap">Market Cap 24h</div>
      <div class="card-value" id="reg-mcap">–</div><div class="card-sub">%</div></div>
    <div class="card"><div class="card-label" data-i18n="reg_trending">Trending</div>
      <div id="reg-trending" style="font-size:11px;color:var(--text);line-height:1.6;margin-top:2px">–</div></div>
  </div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:20px;line-height:1.5" data-i18n="reg_hint">
    Hohe/steigende BTC-Dominanz = Kapital fliesst in BTC, Alts schwaecheln oft. Nutze das als groben Regime-Filter.
  </div>

  <!-- Derivate (Coinalyze) -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="deriv_title">DERIVATE-DATEN (COINALYZE)</span>
  </div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:12px;line-height:1.5" data-i18n="deriv_hint">
    Aggregierte Futures-Daten (Binance-Perps): Open Interest (offenes Kontraktvolumen), Funding Rate, Long/Short-Verhaeltnis und Liquidationen der letzten 24h. Braucht einen kostenlosen Coinalyze-API-Key (Settings → Globale API-Keys).
  </div>
  <div id="deriv-status" style="font-size:11px;color:var(--muted);padding:16px;text-align:center"></div>
  <div class="rate-table" id="deriv-table-wrap" style="display:none">
    <div class="ov-head" style="grid-template-columns:70px 1fr 90px 90px 110px">
      <span data-i18n="deriv_coin">Coin</span>
      <span data-i18n="deriv_oi">Open Interest</span>
      <span data-i18n="deriv_funding">Funding</span>
      <span data-i18n="deriv_ls">Long/Short</span>
      <span data-i18n="deriv_liq">Liq. 24h (L/S)</span>
    </div>
    <div id="deriv-rows"></div>
  </div>

  <!-- Order-Book-Druck (Bitget public) -->
  <div style="display:flex;justify-content:space-between;align-items:center;margin:22px 0 10px">
    <span style="font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)" data-i18n="ob_title">ORDER-BOOK-DRUCK (BITGET)</span>
  </div>
  <div style="font-size:10px;color:var(--muted);margin-bottom:12px;line-height:1.5" data-i18n="ob_hint">
    Kauf-/Verkaufsdruck aus dem Live-Orderbuch: Verhaeltnis von Kauf- zu Verkaufsvolumen im Bereich &plusmn;1 % um den Preis. &gt;1 (gruen) = Kaufdruck ueberwiegt, &lt;1 (rot) = Verkaufsdruck. Kurzfristig und kann durch Fake-Walls verzerrt sein.
  </div>
  <div id="ob-status" style="font-size:11px;color:var(--muted);padding:16px;text-align:center">Lade...</div>
  <div class="rate-table" id="ob-table-wrap" style="display:none">
    <div class="ov-head" style="grid-template-columns:70px 1fr 110px 110px 90px">
      <span data-i18n="deriv_coin">Coin</span>
      <span data-i18n="ob_pressure">Druck (Bid/Ask)</span>
      <span data-i18n="ob_bidvol">Kaufvol.</span>
      <span data-i18n="ob_askvol">Verkaufsvol.</span>
      <span data-i18n="ob_spread">Spread</span>
    </div>
    <div id="ob-rows"></div>
  </div>
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
          <button class="preset-btn low" onclick="applyPreset('low')">🟢 LOW RISK</button>
          <button class="preset-btn med" onclick="applyPreset('medium')">🔵 MEDIUM RISK</button>
          <button class="preset-btn degen" onclick="applyPreset('degen')">🔴 DEGEN</button>
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
          <button class="preset-btn low"    onclick="applyBotPreset('signal','low')" data-i18n="bp_cons">KONSERVATIV</button>
          <button class="preset-btn med"    onclick="applyBotPreset('signal','medium')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen"  onclick="applyBotPreset('signal','high')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="sig-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="sig-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="sig-pass" placeholder="Bitget Passphrase"></div>
        <div class="field-row"><label>Leverage (1-10)</label><input type="number" id="sig-lever" placeholder="3" min="1" max="10"></div>
        <div class="field-row"><label data-i18n="lbl_risk_trade">Risiko pro Trade (%)</label><input type="number" id="sig-risk-pct" placeholder="3.0" step="0.5" min="0.5" max="10"></div>
        <div class="field-row"><label data-i18n="lbl_usdt_trade">USDT pro Trade (fallback)</label><input type="number" id="sig-usdt" placeholder="30" min="5"></div>
        <div class="field-row"><label data-i18n="lbl_max_conc">Max. gleichzeitige Pos.</label><input type="number" id="sig-max-conc" placeholder="2" min="1" max="4"></div>
        <div class="field-row"><label data-i18n="lbl_corr_filter">Korrelations-Filter</label><input type="checkbox" id="sig-corr-filter" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_max_corr">Max. Korrelation (0.5-1.0)</label><input type="number" id="sig-max-corr" placeholder="0.85" step="0.05" min="0.5" max="1.0"></div>
        <div class="settings-note" data-i18n="note_corr">Korrelations-Filter: verhindert, dass der Bot eine neue Position eroeffnet, die zu stark mit einer bereits offenen, gleichgerichteten Position korreliert (Diversifikation). Bei fehlenden Daten wird normal weitergehandelt.</div>
        <div class="field-row"><label data-i18n="lbl_adx_filter">ADX-Trendfilter</label><input type="checkbox" id="sig-adx-filter" style="width:auto"></div>
        <div class="field-row"><label data-i18n="lbl_min_adx">Min. ADX (10-40)</label><input type="number" id="sig-min-adx" placeholder="20" step="1" min="10" max="40"></div>
        <div class="settings-note" data-i18n="note_adx">ADX-Trendfilter: daempft das Signal, wenn kein klarer Trend da ist (ADX unter Schwelle) – handelt weniger im Seitwaerts-Gezappel. Fail-open bei zu wenig Daten.</div>
        <div class="field-row"><label data-i18n="lbl_ob">Order-Book-Kaufdruck</label><input type="checkbox" id="sig-ob-signal" style="width:auto"></div>
        <div class="settings-note" data-i18n="note_ob">Order-Book-Kaufdruck: bezieht den Kauf-/Verkaufsdruck aus dem Live-Orderbuch als zusaetzlichen Signal-Faktor mit ein. Fail-open, wenn keine Daten verfuegbar sind.</div>
        <div class="field-row"><label data-i18n="lbl_sig_thresh">Signal-Schwelle (2-5)</label><input type="number" id="sig-thresh" placeholder="3" min="2" max="5"></div>
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
          <button class="preset-btn low"   onclick="applyBotPreset('grid','low')" data-i18n="bp_cons">KONSERVATIV</button>
          <button class="preset-btn med"   onclick="applyBotPreset('grid','medium')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen" onclick="applyBotPreset('grid','high')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="grd-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="grd-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="grd-pass" placeholder="Bitget Passphrase"></div>
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
        <div class="field-row"><label data-i18n="lbl_levels">Anzahl Levels</label><input type="number" id="grd-n" placeholder="10" min="2" max="50"></div>
        <div class="field-row"><label>Investment (USDT)</label><input type="number" id="grd-inv" placeholder="100" min="10"></div>
        <div class="validate-row">
          <button class="btn-validate" onclick="validateKey('grid')">Verbindung testen</button>
          <span class="val-result" id="val-grid"></span>
        </div>
      </div>
    </div>

    <!-- FUNDING BOT -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-funding')" style="color:var(--funding)"><span>Funding Bot – Sub-Account API</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-funding" class="settings-body">
        <div class="preset-wrap" style="margin-bottom:12px">
          <span style="font-size:10px;color:var(--muted);margin-right:6px" data-i18n="set_preset">Preset:</span>
          <button class="preset-btn low"   onclick="applyBotPreset('funding','low')" data-i18n="bp_cons">KONSERVATIV</button>
          <button class="preset-btn med"   onclick="applyBotPreset('funding','medium')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen" onclick="applyBotPreset('funding','high')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="fnd-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="fnd-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="fnd-pass" placeholder="Bitget Passphrase"></div>
        <div class="field-row"><label data-i18n="lbl_min_funding">Min. Funding Rate (%)</label><input type="number" id="fnd-minrate" placeholder="0.03" step="0.001"></div>
        <div class="field-row"><label data-i18n="lbl_max_pos">Max. Position (USDT)</label><input type="number" id="fnd-maxpos" placeholder="200" min="10"></div>
        <div class="validate-row">
          <button class="btn-validate" onclick="validateKey('funding')">Verbindung testen</button>
          <span class="val-result" id="val-funding"></span>
        </div>
      </div>
    </div>

    <!-- DCA BOT -->
    <div class="settings-section">
      <div class="settings-head" onclick="toggle('s-dca')" style="color:var(--dca)"><span>DCA Bot – Sub-Account API</span><span style="color:var(--muted)">▾</span></div>
      <div id="s-dca" class="settings-body">
        <div class="preset-wrap" style="margin-bottom:12px">
          <span style="font-size:10px;color:var(--muted);margin-right:6px" data-i18n="set_preset">Preset:</span>
          <button class="preset-btn low"   onclick="applyBotPreset('dca','low')" data-i18n="bp_cons">KONSERVATIV</button>
          <button class="preset-btn med"   onclick="applyBotPreset('dca','medium')" data-i18n="bp_std">STANDARD</button>
          <button class="preset-btn degen" onclick="applyBotPreset('dca','high')" data-i18n="bp_agg">AGGRESSIV</button>
        </div>
        <div style="background:rgba(0,214,143,.06);border:1px solid rgba(0,214,143,.15);border-radius:5px;padding:8px 12px;margin-bottom:12px;font-size:10px;color:var(--signal)" data-i18n="dca_spot_note">
          DCA kauft immer auf dem Spot-Markt (kein Hebel, keine Funding-Kosten). Das Guthaben muss auf dem Spot-Konto des Sub-Accounts liegen.
        </div>
        <div class="field-row"><label>API Key</label><input type="text" id="dca-key" placeholder="Bitget API Key"></div>
        <div class="field-row"><label>API Secret</label><input type="password" id="dca-sec" placeholder="Bitget API Secret"></div>
        <div class="field-row"><label>Passphrase</label><input type="password" id="dca-pass" placeholder="Bitget Passphrase"></div>
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
const BOT_COLORS = {signal:'#00d68f',grid:'#4da6ff',funding:'#a78bfa',dca:'#fbbf24'};
const BOT_NAMES  = {signal:'Signal Bot',grid:'Grid Bot',funding:'Funding Bot',dca:'DCA Bot'};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// -- SPRACHE / LANGUAGE ---------------------------------------
let _lang = (typeof localStorage !== 'undefined' && localStorage.getItem('tp_lang')) || 'de';

const STRINGS = {
  de: {
    // Nav
    nav_overview:'OVERVIEW', nav_signal:'SIGNAL', nav_grid:'GRID',
    nav_funding:'FUNDING', nav_dca:'DCA', nav_markt:'MARKT',
    nav_trades:'TRADES',
    nav_backtest:'BACKTEST', nav_correlation:'KORRELATION', nav_derivate:'DERIVATE', nav_alerts:'ALERTS', nav_settings:'SETTINGS',
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
    total_pnl_nofund:'Gesamt PnL (ohne Funding)', running_total:'Laufen / Gesamt',
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
    bt_period:'Zeitraum', bt_lever:'Hebel', bt_thresh:'Signal-Schwelle (1-3)',
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
    set_auth_hint:'Beim ersten Start wurde ein zufaelliges Passwort generiert (siehe platform.log). Hier aendern und SPEICHERN nicht vergessen - danach fragt der Browser beim naechsten Laden neu nach Login.',
    lbl_user:'Benutzername', lbl_pass:'Passwort', ph_pass_unchanged:'Leer lassen = unveraendert',
    set_global_head:'Globale API-Keys',
    set_global_hint:'Wichtig: Nach dem Eintragen immer unten auf SPEICHERN klicken, dann START druecken.',
    ph_finnhub:'Fuer Makro-Kalender (kostenlos)', ph_coinalyze:'Fuer Derivate-Tab (kostenlos, coinalyze.net)',
    ph_tg_chat:'Deine Chat-ID (z.B. 123456789)',
    set_notify_note:'Telegram: @BotFather → /newbot → Token. Chat-ID von @userinfobot.<br>Discord: Server-Einstellungen → Integrationen → Webhooks → URL kopieren.<br>Beide koennen gleichzeitig aktiv sein. News-Sentiment: CoinGecko (kostenlos, kein Key).',
    set_preset:'Preset:', bp_cons:'KONSERVATIV', bp_std:'STANDARD', bp_agg:'AGGRESSIV',
    lbl_risk_trade:'Risiko pro Trade (%)', lbl_usdt_trade:'USDT pro Trade (fallback)',
    lbl_max_conc:'Max. gleichzeitige Pos.', lbl_corr_filter:'Korrelations-Filter', lbl_max_corr:'Max. Korrelation (0.5-1.0)',
    note_corr:'Korrelations-Filter: verhindert, dass der Bot eine neue Position eroeffnet, die zu stark mit einer bereits offenen, gleichgerichteten Position korreliert (Diversifikation). Bei fehlenden Daten wird normal weitergehandelt.',
    lbl_adx_filter:'ADX-Trendfilter', lbl_min_adx:'Min. ADX (10-40)',
    note_adx:'ADX-Trendfilter: daempft das Signal, wenn kein klarer Trend da ist (ADX unter Schwelle) – handelt weniger im Seitwaerts-Gezappel. Fail-open bei zu wenig Daten.',
    lbl_ob:'Order-Book-Kaufdruck',
    note_ob:'Order-Book-Kaufdruck: bezieht den Kauf-/Verkaufsdruck aus dem Live-Orderbuch als zusaetzlichen Signal-Faktor mit ein. Fail-open, wenn keine Daten verfuegbar sind.',
    lbl_sig_thresh:'Signal-Schwelle (2-5)',
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
    nav_funding:'FUNDING', nav_dca:'DCA', nav_markt:'MARKET',
    nav_trades:'TRADES',
    nav_backtest:'BACKTEST', nav_correlation:'CORRELATION', nav_derivate:'DERIVATIVES', nav_alerts:'ALERTS', nav_settings:'SETTINGS',
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
    total_pnl_nofund:'Total PnL (excl. Funding)', running_total:'Running / Total',
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
    bt_period:'Period', bt_lever:'Leverage', bt_thresh:'Signal Threshold (1-3)',
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
    set_auth_hint:'A random password was generated on first start (see platform.log). Change it here and remember to SAVE — the browser will then ask for the new login on next load.',
    lbl_user:'Username', lbl_pass:'Password', ph_pass_unchanged:'Leave empty = unchanged',
    set_global_head:'Global API keys',
    set_global_hint:'Important: Always click SAVE below after entering keys, then press START.',
    ph_finnhub:'For the macro calendar (free)', ph_coinalyze:'For the Derivatives tab (free, coinalyze.net)',
    ph_tg_chat:'Your chat ID (e.g. 123456789)',
    set_notify_note:'Telegram: @BotFather → /newbot → token. Chat ID from @userinfobot.<br>Discord: Server settings → Integrations → Webhooks → copy URL.<br>Both can be active at once. News sentiment: CoinGecko (free, no key).',
    set_preset:'Preset:', bp_cons:'CONSERVATIVE', bp_std:'STANDARD', bp_agg:'AGGRESSIVE',
    lbl_risk_trade:'Risk per trade (%)', lbl_usdt_trade:'USDT per trade (fallback)',
    lbl_max_conc:'Max simultaneous pos.', lbl_corr_filter:'Correlation filter', lbl_max_corr:'Max correlation (0.5-1.0)',
    note_corr:'Correlation filter: prevents the bot from opening a new position that is too strongly correlated with an already-open one in the same direction (diversification). When data is missing it keeps trading as normal.',
    lbl_adx_filter:'ADX trend filter', lbl_min_adx:'Min ADX (10-40)',
    note_adx:'ADX trend filter: dampens the signal when there is no clear trend (ADX below threshold) – trades less in sideways chop. Fail-open when there is too little data.',
    lbl_ob:'Order-book buy pressure',
    note_ob:'Order-book buy pressure: includes buy/sell pressure from the live order book as an extra signal factor. Fail-open when no data is available.',
    lbl_sig_thresh:'Signal threshold (2-5)',
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
    funding:'nav_funding', dca:'nav_dca', markt:'nav_markt',
    trades:'nav_trades',
    backtest:'nav_backtest', correlation:'nav_correlation', derivate:'nav_derivate', alerts:'nav_alerts', settings:'nav_settings',
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


const pnlHistory = {signal:[],grid:[],funding:[],dca:[]};
const MAX_PTS = 80;

function trackPnl(state) {
  ['signal','grid','funding','dca'].forEach(id => {
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
  sparkline('f-spark', pnlHistory.funding);
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
const PRESETS = {
  low: {
    desc: '[NIEDRIG] LOW RISK: Hebel 1x, enger SL 0.5%, nur starke Signale (Schwelle 4), kleines Grid.',
    signal: {lever:1, usdt:20, thresh:4, sl:0.005, tp:0.015},
    grid: {n:6, inv:50, upper:0, lower:0},
  },
  medium: {
    desc: '[MITTEL] MEDIUM RISK: Standard-Werte. Hebel 3x, SL 1%, Schwelle 3, Grid 10 Levels.',
    signal: {lever:3, usdt:30, thresh:3, sl:0.010, tp:0.020},
    grid: {n:10, inv:100, upper:0, lower:0},
  },
  degen: {
    desc: '[HOCH] DEGEN: Hebel 5x, weiter SL 2%, niedrige Schwelle 2 (mehr Trades, mehr Risiko).',
    signal: {lever:5, usdt:50, thresh:2, sl:0.020, tp:0.040},
    grid: {n:20, inv:300, upper:0, lower:0},
  },
};

function applyPreset(id) {
  const p = PRESETS[id];
  if (!p) return;
  document.getElementById('preset-desc').textContent = p.desc;
  // Signal Bot
  if (p.signal) {
    document.getElementById('sig-lever').value  = p.signal.lever;
    document.getElementById('sig-usdt').value   = p.signal.usdt;
    document.getElementById('sig-thresh').value = p.signal.thresh;
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
    funding: {key:'fnd-key',  sec:'fnd-sec',  pass:'fnd-pass'},
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
  funding: {
    title: 'FUNDING BOT',
    sub: 'Delta-neutrale Strategie - verdient die Funding Rate ohne Preisrisiko',
    accent: '#a78bfa',
    sections: [
      {
        title: 'Risiko & Rendite',
        table: [
          ['Risikostufe', '[NIEDRIG] NIEDRIG (bei korrekter Ausfuehrung)'],
          ['Gutes Jahr', '20 - 50% p.a. auf eingesetztes Kapital in aktiven Bullmaerkten'],
          ['Durchschnittsjahr', '10 - 25% p.a. ueber verschiedene Marktphasen'],
          ['Schlechtes Jahr', '2 - 8% p.a. wenn Funding Rates dauerhaft niedrig sind'],
          ['Wichtig', 'Funding Rates sind variabel. In Baermaerkten koennen sie negativ werden - dann muss man die Seite wechseln oder pausieren'],
          ['Restrisiko', 'Exchange-Gegenparteirisiko bleibt immer. Kapital liegt auf der Boerse.'],
        ]
      },
      {
        title: 'Was ist die Funding Rate?',
        text: 'Auf Futures-Boersen gibt es alle 8 Stunden eine Zahlung zwischen Long- und Short-Haltern. Ist mehr Nachfrage nach Longs als Shorts, zahlen Long-Trader an Short-Trader (positive Rate). In Bullmaerkten kann diese Rate 0.05% - 0.3% alle 8h betragen - das entspricht 20% - 130% annualisiert.'
      },
      {
        title: 'Wie verdient der Bot?',
        text: 'Der Bot nimmt eine delta-neutrale Position ein: Short auf Futures und Long auf Spot gleichzeitig. Die Positionen heben sich im Preis gegenseitig auf - egal ob BTC steigt oder faellt, das Gesamtkapital bleibt stabil. Was uebrig bleibt, ist die Funding Rate als reiner Ertrag alle 8 Stunden.'
      },
      {
        title: 'Rate-Tabelle erklaert',
        table: [
          ['Funding Rate', 'Aktuelle Rate in %. Positiv = Longs zahlen an Shorts (Bot nimmt Short-Seite)'],
          ['Est. / 8h', 'Geschaetzter Ertrag alle 8h bei konfigurierter Max-Position in USDT'],
          ['Unter Schwelle', 'Rate zu niedrig - nach Handelsgebuehren kein Gewinn moeglich'],
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
       text:'Der Backtest verwendet nur technische Indikatoren (RSI, EMA, MACD). Makro-Blackouts, Funding Rates, News-Sentiment und Fear&Greed sind NICHT eingebaut. Das echte System hat dadurch oft bessere Ergebnisse als der Backtest zeigt, aber auch mehr Pausen.'},
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
         ['Funding Rate','Alarm wenn die Funding Rate eines Coins eine bestimmte Schwelle ueberschreitet (Opportunity-Alert fuer Funding Bot).'],
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
          ['Funding Bot', '[NIEDRIG] Niedrig | 10-50% p.a. | Jeder Markt (wenn Rate hoch)'],
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
    if (r.error) return '<div class="ov-row" style="grid-template-columns:90px 1fr 70px 70px 70px 70px 70px"><span>'+sym+'</span><span style="color:var(--red)">'+r.error+'</span></div>';
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

// -- KORRELATIONS-MATRIX --------------------------------------
async function loadCorrelation() {
  const status = document.getElementById('corr-status');
  const matrix = document.getElementById('corr-matrix');
  if (status) { status.style.display = 'block'; status.textContent = t('running') === 'RUNNING' ? 'Lade...' : 'Loading...'; }
  if (matrix) matrix.innerHTML = '';
  try {
    const days = parseInt(document.getElementById('corr-period')?.value) || 30;
    const r = await fetch('/api/correlation', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({period_days:days})});
    const d = await r.json();
    renderCorrelation(d);
  } catch(e) {
    if (status) { status.style.display='block'; status.textContent = 'Fehler: ' + e.message; }
  }
}

function corrColor(v) {
  // v in [-1,1]. Hohe positive Korrelation = Risiko (rot), negativ = gut (gruen).
  if (v >= 0.7)  return 'rgba(248,113,113,.85)';   // --red
  if (v >= 0.4)  return 'rgba(251,191,36,.75)';    // --dca amber
  if (v <= -0.4) return 'rgba(0,214,143,.75)';     // --signal green
  return 'rgba(80,80,90,.35)';                      // neutral
}

function renderCorrelation(d) {
  const status = document.getElementById('corr-status');
  const box    = document.getElementById('corr-matrix');
  if (!box) return;
  const syms = d.symbols || [];
  const m    = d.matrix || [];
  if (syms.length < 2) {
    if (status) { status.style.display='block'; status.textContent = 'Nicht genug Daten (mind. 2 Coins mit Kursdaten noetig).'; }
    box.innerHTML = ''; return;
  }
  if (status) status.style.display = 'none';
  let html = '<table style="border-collapse:collapse;font-size:11px">';
  html += '<tr><th style="padding:6px"></th>' +
    syms.map(s => '<th style="padding:6px 8px;color:var(--muted);font-weight:600">'+esc(s)+'</th>').join('') + '</tr>';
  for (let i=0; i<syms.length; i++) {
    html += '<tr><th style="padding:6px 8px;color:var(--muted);font-weight:600;text-align:right">'+esc(syms[i])+'</th>';
    for (let j=0; j<syms.length; j++) {
      const v = (m[i]||[])[j];
      const val = (typeof v === 'number') ? v : 0;
      const bg  = i===j ? 'rgba(80,80,90,.55)' : corrColor(val);
      const txt = i===j ? '—' : (val>=0?'+':'')+val.toFixed(2);
      html += '<td style="padding:6px 8px;text-align:center;background:'+bg+';color:var(--white);border:1px solid var(--bg);min-width:44px">'+txt+'</td>';
    }
    html += '</tr>';
  }
  html += '</table>';
  const note = (_lang==='en')
    ? `Based on ${d.period_days}-day daily returns · ${d.count} coins`
    : `Basiert auf ${d.period_days}-Tage-Tagesrenditen · ${d.count} Coins`;
  html += '<div style="font-size:10px;color:var(--muted);margin-top:8px">'+note+'</div>';
  box.innerHTML = html;
}

// -- DERIVATE + MARKT-REGIME ----------------------------------
async function loadDerivate() {
  loadRegime();
  loadDerivatives();
  loadOrderbook();
}

function obColor(r) {
  if (r == null) return '#888';
  if (r >= 1.3)  return 'var(--signal)';
  if (r <= 0.77) return 'var(--red)';
  if (r >= 1.05) return '#7fd8b0';
  if (r <= 0.95) return '#e59aa0';
  return '#888';
}

async function loadOrderbook() {
  const status = document.getElementById('ob-status');
  const wrap   = document.getElementById('ob-table-wrap');
  status.style.display = 'block'; wrap.style.display = 'none';
  status.textContent = (_lang==='en'?'Loading…':'Lade…');
  try {
    const r = await fetch('/api/orderbook');
    const d = await r.json();
    const rows = (d.rows||[]).filter(x => x.ratio != null);
    if (!rows.length) { status.textContent = (_lang==='en'?'No data.':'Keine Daten.'); return; }
    document.getElementById('ob-rows').innerHTML = (d.rows||[]).map(x => {
      const col = obColor(x.ratio);
      const bar = x.ratio==null ? '' :
        '<span style="display:inline-block;height:6px;width:'+Math.min(100,Math.max(6,x.ratio*40))+'px;background:'+col+';border-radius:3px;margin-left:8px;vertical-align:middle"></span>';
      return '<div class="ov-row" style="grid-template-columns:70px 1fr 110px 110px 90px">' +
        '<span style="font-weight:600;color:var(--white)">'+esc(x.coin)+'</span>' +
        '<span style="color:'+col+';font-weight:600">'+(x.ratio==null?'–':x.ratio)+bar+'</span>' +
        '<span class="green">'+fmtUsdShort(x.bid_vol)+'</span>' +
        '<span class="red">'+fmtUsdShort(x.ask_vol)+'</span>' +
        '<span style="font-size:10px;color:var(--muted)">'+(x.spread_bps==null?'–':x.spread_bps+' bps')+'</span>' +
      '</div>';
    }).join('');
    status.style.display = 'none';
    wrap.style.display = 'block';
  } catch(e) {
    status.textContent = 'Fehler: ' + e.message;
  }
}

async function loadRegime() {
  try {
    const r = await fetch('/api/regime');
    const d = await r.json();
    const fmtPct = v => (v==null?'–':(v>=0?'+':'')+Number(v).toFixed(2));
    document.getElementById('reg-btc').textContent  = d.btc_dominance==null?'–':Number(d.btc_dominance).toFixed(2);
    document.getElementById('reg-eth').textContent  = d.eth_dominance==null?'–':Number(d.eth_dominance).toFixed(2);
    const mc = document.getElementById('reg-mcap');
    mc.textContent = fmtPct(d.mcap_change_24h);
    mc.className = 'card-value ' + (Number(d.mcap_change_24h)>=0?'green':'red');
    const tr = document.getElementById('reg-trending');
    tr.innerHTML = (d.trending&&d.trending.length)
      ? d.trending.slice(0,5).map(c=>esc(c.symbol)).join(' · ')
      : '–';
  } catch(e) {}
}

function fmtUsdShort(v) {
  if (v==null || isNaN(v)) return '–';
  const a = Math.abs(v);
  if (a >= 1e9) return (v/1e9).toFixed(2)+'B';
  if (a >= 1e6) return (v/1e6).toFixed(1)+'M';
  if (a >= 1e3) return (v/1e3).toFixed(1)+'K';
  return Number(v).toFixed(0);
}

async function loadDerivatives() {
  const status = document.getElementById('deriv-status');
  const wrap   = document.getElementById('deriv-table-wrap');
  status.style.display = 'block'; wrap.style.display = 'none';
  status.textContent = (_lang==='en'?'Loading…':'Lade…');
  try {
    const r = await fetch('/api/derivatives');
    const d = await r.json();
    if (d.error === 'no_key') {
      status.innerHTML = (_lang==='en')
        ? 'No Coinalyze API key set. Add a free key under <b>Settings → Global API keys</b>.'
        : 'Kein Coinalyze-API-Key gesetzt. Trage einen kostenlosen Key unter <b>Settings → Globale API-Keys</b> ein.';
      return;
    }
    if (d.error === 'bad_key') {
      status.textContent = (_lang==='en')
        ? 'Coinalyze API key invalid — please check it in Settings.'
        : 'Coinalyze-API-Key ungueltig — bitte in den Settings pruefen.';
      return;
    }
    const rows = d.rows || [];
    if (!rows.length) { status.textContent = (_lang==='en'?'No data.':'Keine Daten.'); return; }
    document.getElementById('deriv-rows').innerHTML = rows.map(x => {
      const fr = x.funding==null ? '–' : (x.funding>=0?'+':'')+(x.funding*100).toFixed(4)+'%';
      const frCol = x.funding==null?'#888':(x.funding>0?'var(--red)':x.funding<0?'var(--signal)':'#888');
      let ls = '–', lsCol = '#888';
      if (x.ratio!=null) {
        ls = Number(x.ratio).toFixed(2);
        lsCol = x.ratio>1.1?'var(--signal)':x.ratio<0.9?'var(--red)':'#888';
      } else if (x.long_pct!=null && x.short_pct!=null) {
        ls = Math.round(x.long_pct)+'/'+Math.round(x.short_pct);
      }
      const liq = (x.liq_long!=null||x.liq_short!=null)
        ? fmtUsdShort(x.liq_long)+' / '+fmtUsdShort(x.liq_short) : '–';
      return '<div class="ov-row" style="grid-template-columns:70px 1fr 90px 90px 110px">' +
        '<span style="font-weight:600;color:var(--white)">'+esc(x.coin)+'</span>' +
        '<span class="blue">'+fmtUsdShort(x.oi_usd)+'</span>' +
        '<span style="color:'+frCol+'">'+fr+'</span>' +
        '<span style="color:'+lsCol+'">'+ls+'</span>' +
        '<span style="font-size:10px;color:var(--muted)">'+liq+'</span>' +
      '</div>';
    }).join('');
    status.style.display = 'none';
    wrap.style.display = 'block';
  } catch(e) {
    status.textContent = 'Fehler: ' + e.message;
  }
}

// -- CIRCUIT BREAKER BADGE ------------------------------------
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
const BOT_PRESETS={
  signal:{
    low:{lever:1,usdt:20,thresh:4},
    medium:{lever:3,usdt:30,thresh:3},
    high:{lever:5,usdt:50,thresh:2},
  },
  grid:{
    low:{n:6,inv:50},
    medium:{n:10,inv:100},
    high:{n:20,inv:300},
  },
  funding:{
    low:{minrate:0.0005,maxpos:100},
    medium:{minrate:0.0003,maxpos:200},
    high:{minrate:0.0001,maxpos:500},
  },
  dca:{
    low:{hrs:168,amt:20},
    medium:{hrs:24,amt:30},
    high:{hrs:12,amt:50},
  },
};

function applyBotPreset(botId,level) {
  const p=BOT_PRESETS[botId]?.[level]; if(!p) return;
  const set=(id,v)=>{const el=document.getElementById(id);if(el&&v!==undefined)el.value=v;};
  if(botId==='signal'){set('sig-lever',p.lever);set('sig-usdt',p.usdt);set('sig-thresh',p.thresh);}
  else if(botId==='grid'){set('grd-n',p.n);set('grd-inv',p.inv);}
  else if(botId==='funding'){set('fnd-minrate',p.minrate);set('fnd-maxpos',p.maxpos);}
  else if(botId==='dca'){set('dca-hrs',p.hrs);set('dca-amt',p.amt);}
  const labels={low:'Konservativ',medium:'Standard',high:'Aggressiv'};
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
  document.querySelectorAll('.tab').forEach(t => {
    if (t.getAttribute('data-bot') === id || t.textContent.toLowerCase().includes(id))
      t.classList.add('active');
  });
  activePanel = id;
  if (id === 'settings')  { if (lastState) fillSettingsForm(lastState); }
  if (id === 'overview')  { loadPositions(); loadFGHistory(); }
  if (id === 'markt')     { loadMarket(); loadKalender(false); }
  if (id === 'alerts')    { loadAlerts(); loadAlertLog(); }
  if (id === 'correlation') loadCorrelation();
  if (id === 'derivate')  loadDerivate();
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
    ${posHtml}
  </div>`;
}

function update(state) {
  lastState = state;
  document.getElementById('last-update').textContent = new Date().toLocaleTimeString('de-DE');
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
  ['signal','grid','funding','dca'].forEach(id => {
    const st = state.bots[id]?.status || 'STOPPED';
    const dot = document.getElementById('dot-' + id);
    if (dot) dot.className = 'status-dot ' + dotClass(st);
  });

  // OVERVIEW
  let totalBal = 0, totalPnl = 0, activeCount = 0, totalTrades = 0;
  const rows = ['signal','grid','funding','dca'].map(id => {
    const b = state.bots[id] || {};
    const bal = parseFloat(b.balance||0);
    const pnl = parseFloat(b.pnl||0);
    const st  = b.status || 'STOPPED';
    if (st === 'RUNNING') activeCount++;
    totalBal    += bal;
    if (id !== 'funding') totalPnl += pnl; // Funding Bot handelt nicht real, Schaetzung zaehlt nicht in den echten Gesamt-PnL
    totalTrades += parseInt(b.trade_count||0);
    return `<div class="ov-row">
      <span class="ov-bot-name" style="color:${BOT_COLORS[id]}">${BOT_NAMES[id]}</span>
      <span><span class="ov-status ${statusClass(st)}">${st}</span></span>
      <span class="blue">${bal.toFixed(2)}</span>
      <span class="${pnlColor(pnl)}">${(pnl>=0?'+':'')+pnl.toFixed(2)}${id==='funding'?` <span style="font-size:9px;color:var(--muted);cursor:help" title="Funding-Ertrag wird nur akkumuliert wenn Bot laeuft.">${st==='RUNNING'?'[est.]':'[inaktiv]'}</span>`:''}</span>
      <span style="color:var(--muted)">${id==='funding'? (b.trade_count||0)+' Zahl.' : (b.trade_count||0)+' Trades'}</span>
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
  document.getElementById('ov-balance').textContent = totalBal.toFixed(2);
  document.getElementById('ov-pnlpct').textContent = state.bots.signal?.pnl_pct?.toFixed(2)+'%' || '-';
  document.getElementById('ov-active').textContent = activeCount + ' / 4';
  document.getElementById('ov-trades').textContent = totalTrades;

  // Overview macro (from signal bot)
  const macroEvs = state.bots.signal?.macro_events || [];
  document.getElementById('ov-macro').innerHTML = renderMacro(macroEvs);

  // Combined recent log
  let allLogs = [];
  ['signal','grid','funding','dca'].forEach(id => {
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
  document.getElementById('s-balance').textContent = parseFloat(sg.balance||0).toFixed(2);
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
  document.getElementById('g-range').textContent = gg.lower&&gg.upper ? gg.lower+' - '+gg.upper : '-';
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

  // FUNDING
  const fg2 = state.bots.funding || {};
  updateBotHeader('funding', fg2);
  document.getElementById('f-balance').textContent = parseFloat(fg2.balance||0).toFixed(2);
  const earnedEl = document.getElementById('f-earned');
  const earnedVal = parseFloat(fg2.earned||0);
  earnedEl.textContent = earnedVal.toFixed(4);
  // Zeige nur Wert wenn Bot wirklich laeuft
  const fundingRunning = fg2.status === 'RUNNING';
  earnedEl.style.color = fundingRunning && earnedVal > 0 ? 'var(--signal)' : 'var(--muted)';
  const fundingEarnSub = document.querySelector('#f-earned + .card-sub') || document.querySelector('.card-sub');
  const opps = fg2.opportunities || [];
  document.getElementById('f-opps').textContent = opps.length;
  const rates = fg2.rates || {};
  const allSyms = ['SOL','ETH','XRP','DOGE','BTC'];
  document.getElementById('f-rates').innerHTML = allSyms.map(sym => {
    const r = parseFloat(rates[sym]||0);
    const est = Math.abs(r) / 100 * parseFloat(fg2.max_position_usdt||200);
    const rColor = r > 0.03 ? 'var(--red)' : r < -0.03 ? 'var(--signal)' : 'var(--muted)';
    const dir = r > 0 ? 'Short F / Long S' : r < 0 ? 'Long F / Short S' : '-';
    return `<div class="rt-row">
      <span style="font-weight:600">${sym}</span>
      <span style="color:${rColor}">${r.toFixed(4)}%</span>
      <span style="color:var(--muted)">${Math.abs(r)>=0.03?est.toFixed(4):'-'} USDT</span>
      <span style="color:${Math.abs(r)>=0.03?'var(--signal)':'var(--dim)'}">${Math.abs(r)>=0.03?dir:'Unter Schwelle'}</span>
    </div>`;
  }).join('');
  document.getElementById('f-log').innerHTML = renderLog(fg2.logs||[]);
  document.getElementById('f-logcount').textContent = (fg2.logs||[]).length + ' Eintraege';

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
    document.getElementById('cfg-cryptopanic').value = s(cfg.cryptopanic_key);
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
    document.getElementById('sig-max-conc').value    = s(b.signal?.max_concurrent||2);
    document.getElementById('sig-corr-filter').checked = (b.signal?.use_correlation_filter !== false);
    document.getElementById('sig-max-corr').value    = s(b.signal?.max_correlation ?? 0.85);
    document.getElementById('sig-adx-filter').checked = (b.signal?.use_adx_filter !== false);
    document.getElementById('sig-min-adx').value     = s(b.signal?.min_adx ?? 20);
    document.getElementById('sig-ob-signal').checked = (b.signal?.use_orderbook_signal !== false);
    document.getElementById('sig-thresh').value = s(b.signal?.signal_threshold||3);
    document.getElementById('grd-key').value   = s(b.grid?.api_key);
    document.getElementById('grd-sym').value   = s(b.grid?.symbol||'BTCUSDT');
    document.getElementById('grd-upper').value = s(b.grid?.upper_price||0);
    document.getElementById('grd-lower').value = s(b.grid?.lower_price||0);
    document.getElementById('grd-n').value     = s(b.grid?.grid_count||10);
    document.getElementById('grd-inv').value   = s(b.grid?.investment||100);
    document.getElementById('fnd-key').value     = s(b.funding?.api_key);
    document.getElementById('fnd-minrate').value = s(b.funding?.min_funding_rate||0.0003);
    document.getElementById('fnd-maxpos').value  = s(b.funding?.max_position_usdt||200);
    document.getElementById('dca-key').value = s(b.dca?.api_key);
    document.getElementById('dca-sym').value = s(b.dca?.symbol||'BTCUSDT');
    document.getElementById('dca-hrs').value = s(b.dca?.interval_hours||24);
    document.getElementById('dca-amt').value = s(b.dca?.amount_per_buy||20);  }).catch(()=>{});
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
    cryptopanic_key: val('cfg-cryptopanic'),
    telegram_token:  val('cfg-tg-token'),
    telegram_chat_id:val('cfg-tg-chat'),
    discord_webhook: val('cfg-discord-wh') || '',
    live_mode:       document.getElementById('cfg-live')?.checked || false,
    bots: {
      signal: {
        api_key:          val('sig-key'),
        api_secret:       val('sig-sec'),
        passphrase:       val('sig-pass'),
        leverage:         int('sig-lever')    || 3,
        risk_pct:         num('sig-risk-pct') || 3.0,
        use_risk_pct:     true,
        usdt_per_trade:   num('sig-usdt')     || 30,
        max_concurrent:   int('sig-max-conc') || 2,
        use_correlation_filter: document.getElementById('sig-corr-filter')?.checked ?? true,
        max_correlation:  num('sig-max-corr') || 0.85,
        use_adx_filter:   document.getElementById('sig-adx-filter')?.checked ?? true,
        min_adx:          int('sig-min-adx') || 20,
        use_orderbook_signal: document.getElementById('sig-ob-signal')?.checked ?? true,
        signal_threshold: int('sig-thresh')   || 3,
      },
      grid: {
        api_key:     val('grd-key'),
        api_secret:  val('grd-sec'),
        passphrase:  val('grd-pass'),
        symbol:      val('grd-sym')   || 'BTCUSDT',
        upper_price: num('grd-upper'),
        lower_price: num('grd-lower'),
        grid_count:  int('grd-n')     || 10,
        investment:  num('grd-inv')   || 100,
      },
      funding: {
        api_key:          val('fnd-key'),
        api_secret:       val('fnd-sec'),
        passphrase:       val('fnd-pass'),
        min_funding_rate: num('fnd-minrate') || 0.0003,
        max_position_usdt:num('fnd-maxpos')  || 200,
      },
      dca: {
        api_key:       val('dca-key'),
        api_secret:    val('dca-sec'),
        passphrase:    val('dca-pass'),
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
#  HTTP SERVER
# ─────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

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
        if not self._check_auth():
            self._deny_auth(); return
        if self.path == "/api/state":
            with plock:
                state_copy = dict(pstate)
                state_copy["grid_instances"] = dict(pstate.get("grid_instances",{}))
            self._json(state_copy)
        elif self.path == "/api/config":
            self._json(load_config())
        elif self.path == "/api/market":
            self._json(fetch_market_overview())
        elif self.path == "/api/trades":
            self._json(fetch_all_trades())
        elif self.path == "/api/positions":
            self._json(fetch_all_positions())
        elif self.path == "/api/fg_history":
            self._json(fetch_fg_history())
        elif self.path == "/api/regime":
            self._json(fetch_coingecko_regime())
        elif self.path == "/api/derivatives":
            self._json(fetch_derivatives())
        elif self.path == "/api/orderbook":
            self._json(fetch_orderbook_all())
        elif self.path == "/api/alert_log":
            with _alert_lock:
                self._json(list(_alert_log))
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
        if not self._check_auth():
            self._deny_auth(); return
        length = int(self.headers.get("Content-Length",0))
        body   = self.rfile.read(length).decode("utf-8")
        try:   data = json.loads(body)
        except:data = {}
        try:
            self._dispatch_post(data)
        except Exception as e:
            self._json({"status":"error","msg":f"Ungueltige Anfrage: {e}"}, 400)

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
            for bid in ("signal","grid","funding","dca"):
                bd = data.get("bots",{}).get(bid,{})
                for k, v in bd.items():
                    if k in cfg["bots"][bid]: cfg["bots"][bid][k] = v
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
            )
            self._json(result)

        elif self.path == "/api/db_trades":
            self._json(db_get_trades(data.get("bot"), int(data.get("limit",200))))

        elif self.path == "/api/db_pnl":
            self._json(db_get_pnl_history(data.get("bot","signal"),
                                           int(data.get("days",30))))

        elif self.path == "/api/trade_timing":
            self._json(db_trade_timing())

        elif self.path == "/api/correlation":
            syms = data.get("symbols") if isinstance(data.get("symbols"), list) else None
            self._json(compute_correlation(syms, data.get("period_days", 30)))

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
                "grid_count":  max(2, min(50, int(data.get("grid_count",10)))),
                "investment":  max(0.0, float(data.get("investment",100))),
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
                client = BitgetClient(
                    data.get("api_key",""),
                    data.get("api_secret",""),
                    data.get("passphrase",""),
                    live_mode=False
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
    log.info("  Trading Platform v1 | Signal | Grid | Funding | DCA")
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

    log.info("Platform bereit. Bots koennen im Dashboard gestartet werden.")
    log.info("Strg+C zum Beenden.")

    try:
        while True:
            time.sleep(60)
            for bid in ("signal","grid","funding","dca"):
                if bid in bot_threads and not bot_threads[bid].is_alive():
                    with plock:
                        if pstate["bots"][bid]["status"] not in ("STOPPED","STOPPING","EMERGENCY STOP"):
                            pstate["bots"][bid]["status"] = "STOPPED"
    except KeyboardInterrupt:
        log.info("Platform gestoppt.")
