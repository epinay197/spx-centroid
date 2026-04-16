#!/usr/bin/env python3
"""
GammaEdge SPX Centroid  v6  -  Tradier edition (Tastytrade + Massive fallback)
CORRECT methodology matching GammaEdge VOLM:
  - PURE 0DTE cumulative VOLUME weighted centroid (no OI blend)
  - OI kept separately as structural context only
  - Linear regression on each centroid -> settlement projection
  - Day character: TREND / CHOP / REVERSAL
  - Rate of change tracking (CenTab equivalent)
  - Works best after 11:30am ET (London close)
  - Fallback chain: Tradier -> Tastytrade -> Massive
"""

import requests, json, time, threading, webbrowser, math
from http.server  import HTTPServer, BaseHTTPRequestHandler
from datetime     import date, datetime, timedelta
from pathlib      import Path

try:
    import websocket  # websocket-client library
    HAS_WEBSOCKET = True
except ImportError:
    HAS_WEBSOCKET = False

PORT      = 8052
BASE_DIR  = Path(__file__).parent

TRADIER_PROD    = "https://api.tradier.com/v1"
TRADIER_SANDBOX = "https://sandbox.tradier.com/v1"
MASSIVE_BASE    = "https://api.massive.com"
TT_BASE_URL     = "https://api.tastytrade.com"
DXFEED_WS_URL   = "wss://tasty-openapi-ws.dxfeed.com/realtime"

# ── ET helpers (no tzdata needed) ────────────────────────────
def _et_offset():
    utc = datetime.utcnow()
    y   = utc.year
    mar1 = datetime(y, 3, 1)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(y, 11, 1)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return -4 if dst_start <= utc < dst_end else -5

def _now_et():
    return datetime.utcnow() + timedelta(hours=_et_offset())

def et_str():
    return _now_et().strftime("%H:%M:%S")

SESSION_OPEN  = (9,  30)
SESSION_CLOSE = (16, 15)
LONDON_CLOSE  = (11, 30)   # signals more reliable after this

def market_status():
    now = _now_et()
    wd  = now.weekday()
    h, m, s = now.hour, now.minute, now.second
    open_m  = SESSION_OPEN[0]*60  + SESSION_OPEN[1]
    close_m = SESSION_CLOSE[0]*60 + SESSION_CLOSE[1]
    cur_m   = h*60 + m
    if wd >= 5:
        days = 7 - wd
        nxt  = (now + timedelta(days=days)).replace(hour=9,minute=30,second=0,microsecond=0)
        return False, int((nxt-now).total_seconds()), "WEEKEND"
    if cur_m < open_m:
        return False, (open_m-cur_m)*60-s, "PRE-MARKET"
    if cur_m >= close_m:
        days = 3 if wd == 4 else 1
        nxt  = (now + timedelta(days=days)).replace(hour=9,minute=30,second=0,microsecond=0)
        return False, int((nxt-now).total_seconds()), "AFTER-HOURS"
    secs_left = (close_m-cur_m)*60-s
    post_london = cur_m >= LONDON_CLOSE[0]*60+LONDON_CLOSE[1]
    return True, secs_left, "POST-LONDON" if post_london else "PRE-LONDON"

# ── Credentials ───────────────────────────────────────────────
def load_credentials():
    global G_MASSIVE_KEY, G_USE_MASSIVE
    global G_TT_CLIENT_ID, G_TT_CLIENT_SECRET, G_TT_REFRESH_TOKEN, G_USE_TASTYTRADE
    env_file = BASE_DIR / "credentials.env"
    creds = {}
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.strip().strip('"').strip("'").strip()
                if v: creds[k.strip()] = v
    # Load Tastytrade credentials (PRIMARY source)
    tt_id     = creds.get("TASTY_CLIENT_ID", "") or creds.get("TT_CLIENT_ID", "")
    tt_secret = creds.get("TASTY_CLIENT_SECRET", "") or creds.get("TT_CLIENT_SECRET", "")
    tt_refresh = creds.get("TASTY_REFRESH_TOKEN", "") or creds.get("TT_REFRESH_TOKEN", "")
    if tt_id and tt_secret and tt_refresh:
        G_TT_CLIENT_ID     = tt_id
        G_TT_CLIENT_SECRET = tt_secret
        G_TT_REFRESH_TOKEN = tt_refresh
        G_USE_TASTYTRADE   = True
        print("  Tastytrade OAuth credentials loaded (PRIMARY source)")
        return None, None, "Tastytrade (PRIMARY)"
    elif (BASE_DIR / "tasty_token.json").exists():
        G_USE_TASTYTRADE = True
        print("  Tastytrade token file found (tasty_token.json)")
        return None, None, "Tastytrade (PRIMARY)"

    # Load Massive API key (fallback only)
    massive_key = creds.get("MASSIVE_API_KEY", "")
    if massive_key:
        G_MASSIVE_KEY = massive_key
        G_USE_MASSIVE = True
        print("  Massive API key loaded (fallback ready)")
        return None, None, "Massive (fallback)"

    print("  ERROR: No Tastytrade credentials found in credentials.env")
    input("Press Enter to exit..."); exit(1)
    for token, base_url, label in candidates:
        print(f"  Testing {label}...")
        try:
            r = requests.get(f"{base_url}/markets/quotes",
                             params={"symbols":"SPX","greeks":"false"},
                             headers={"Authorization":f"Bearer {token}","Accept":"application/json"},
                             timeout=8)
            if r.status_code == 200:
                d = r.json().get("quotes",{})
                if d and d != "null":
                    print(f"  OK: {label}")
                    return token, base_url, label
            print(f"    HTTP {r.status_code}")
        except Exception as e:
            print(f"    {e}")
    if G_USE_TASTYTRADE:
        print("  Tradier failed -- will use Tastytrade as primary fallback")
        return None, None, "Tastytrade (fallback)"
    if G_USE_MASSIVE:
        print("  Tradier failed -- will use Massive API as fallback")
        return None, None, "Massive (fallback)"
    print("  ERROR: No token worked.")
    input("Press Enter to exit..."); exit(1)

def ah(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

# ── Globals ───────────────────────────────────────────────────
G_TOKEN    = None
G_BASE_URL = None
G_LABEL    = None
G_TT_CLIENT_ID     = None   # Tastytrade OAuth client ID
G_TT_CLIENT_SECRET = None   # Tastytrade OAuth client secret
G_TT_REFRESH_TOKEN = None   # Tastytrade OAuth refresh token
G_USE_TASTYTRADE   = False  # True when Tastytrade is available as fallback
G_TT_ACCESS_TOKEN  = None   # Cached Tastytrade access token
G_TT_TOKEN_EXPIRY  = 0.0    # Unix timestamp when access token expires
G_TT_STREAMER_TOKEN = None  # Cached dxFeed streamer token
G_TT_STREAMER_EXPIRY = 0.0  # Unix timestamp when streamer token expires
G_MASSIVE_KEY = None   # Fallback: Massive.com API key
G_USE_MASSIVE = False  # True when Massive is available as fallback

CACHE = {
    "status":"connecting","error":None,"spot":None,
    "timestamp":None,"et_time":None,
    # Core VOLM centroids (pure 0DTE volume)
    "call_centroid":None,"put_centroid":None,
    "call_vol":0,"put_vol":0,"total_vol":0,
    # Structural OI levels (separate from centroids)
    "max_call_oi":None,"max_put_oi":None,
    "call_oi":0,"put_oi":0,"pcr_oi":None,
    # Historical series for chart + regression
    "history":[],        # [{t,spot,call_c,put_c,call_roc,put_roc}]
    # Regression projections
    "call_proj":None,"put_proj":None,"settlement_range":None,
    # Day character
    "day_char":"UNKNOWN","day_conf":0,"day_signal":"",
    # Rate of change
    "call_roc":0,"put_roc":0,"roc_5":0,
    # Session
    "market_open":False,"session_label":"","secs_until_open":0,
    "post_london":False,"mins_to_close":0,
    # Strike distribution for bar chart
    "by_strike":[],
    "expiration":None,
}
CACHE_LOCK = threading.Lock()

# ── Spot ─────────────────────────────────────────────────────
def get_spot():
    r = requests.get(f"{G_BASE_URL}/markets/quotes",
                     params={"symbols":"SPX","greeks":"false"},
                     headers=ah(G_TOKEN), timeout=10)
    if r.status_code == 200:
        q = r.json().get("quotes",{}).get("quote",{})
        for f in ["last","close","prevclose"]:
            v = q.get(f)
            if v and float(v) > 100: return float(v)
        b, a = q.get("bid"), q.get("ask")
        if b and a: return (float(b)+float(a))/2
    raise RuntimeError(f"SPX spot failed: HTTP {r.status_code}")

# ── 0DTE expiration ───────────────────────────────────────────
def get_0dte_exp():
    """Get today's SPX 0DTE expiration date."""
    r = requests.get(f"{G_BASE_URL}/markets/options/expirations",
                     params={"symbol":"SPX","includeAllRoots":"true","strikes":"false"},
                     headers=ah(G_TOKEN), timeout=15)
    r.raise_for_status()
    data  = r.json().get("expirations",{})
    dates = data.get("date") or data.get("expiration") or []
    if isinstance(dates,str): dates=[dates]
    td = date.today().isoformat()
    # Prefer today; fall back to next expiry (Friday if no daily)
    today_exps = [d for d in dates if d == td]
    return today_exps[0] if today_exps else (sorted(dates)[0] if dates else td)

# ── Chain - 0DTE volume only ─────────────────────────────────
def get_chain_0dte(exp):
    r = requests.get(f"{G_BASE_URL}/markets/options/chains",
                     params={"symbol":"SPX","expiration":exp,"greeks":"false"},
                     headers=ah(G_TOKEN), timeout=30)
    if r.status_code != 200: return []
    opts = r.json().get("options") or {}
    if not opts or opts=="null": return []
    items = opts.get("option",[])
    if isinstance(items,dict): items=[items]
    return items

# ── Massive.com API (fallback) ───────────────────────────────
def massive_headers():
    return {"Authorization": f"Bearer {G_MASSIVE_KEY}", "Accept": "application/json"}

def get_spot_massive():
    """Fetch SPX spot price via Massive API."""
    url = f"{MASSIVE_BASE}/v2/last/trade/SPX"
    r = requests.get(url, headers=massive_headers(), timeout=10)
    r.raise_for_status()
    data = r.json()
    # Massive returns results.p for price
    results = data.get("results", {})
    price = results.get("p")
    if price and float(price) > 100:
        return float(price)
    raise RuntimeError(f"Massive spot failed: unexpected response {data}")

def get_chain_massive(exp):
    """Fetch 0DTE options chain from Massive API with pagination.
    Returns list of dicts matching Tradier-style keys for calc_centroids().
    """
    url = f"{MASSIVE_BASE}/v3/snapshot/options/SPX"
    params = {"expiration_date": exp, "limit": 250}
    all_opts = []

    while url:
        r = requests.get(url, params=params, headers=massive_headers(), timeout=30)
        if r.status_code != 200:
            print(f"  [Massive] chain HTTP {r.status_code}")
            break
        data = r.json()
        results = data.get("results", [])
        for item in results:
            details = item.get("details", {})
            greeks  = item.get("greeks", {})
            oi      = int(item.get("open_interest", 0) or 0)
            iv      = item.get("implied_volatility", 0)
            last_tr = item.get("last_trade", {})
            und     = item.get("underlying_asset", {})
            ctype   = str(details.get("contract_type", "")).lower()
            strike  = float(details.get("strike_price", 0) or 0)
            volume  = int(last_tr.get("size", 0) or 0) if last_tr else 0
            # Build Tradier-compatible dict
            opt = {
                "strike":        strike,
                "option_type":   ctype,
                "volume":        volume,
                "open_interest": oi,
                "greeks": {
                    "gamma": float(greeks.get("gamma", 0) or 0),
                    "delta": float(greeks.get("delta", 0) or 0),
                    "theta": float(greeks.get("theta", 0) or 0),
                    "vega":  float(greeks.get("vega", 0) or 0),
                },
                "implied_volatility": float(iv or 0),
                "last_price": float(last_tr.get("price", 0) or 0) if last_tr else 0,
                "underlying_price": float(und.get("price", 0) or 0) if und else 0,
            }
            all_opts.append(opt)

        # Handle pagination
        next_url = data.get("next_url")
        if next_url:
            # next_url is a full URL; clear params so we don't double-add them
            url = next_url
            params = {}
        else:
            url = None

    return all_opts

# ── Tastytrade API (second fallback) ─────────────────────────
def _tt_load_token_file():
    """Try to load access token from tasty_token.json (for when OAuth creds are missing)."""
    global G_TT_ACCESS_TOKEN, G_TT_TOKEN_EXPIRY
    token_file = BASE_DIR / "tasty_token.json"
    if not token_file.exists():
        return False
    try:
        data = json.loads(token_file.read_text())
        token = data.get("access_token")
        expires_at = data.get("expires_at", 0)
        if token and time.time() < expires_at - 60:
            G_TT_ACCESS_TOKEN = token
            G_TT_TOKEN_EXPIRY = expires_at
            remaining = int(expires_at - time.time())
            print(f"  [Tastytrade] Loaded cached token from tasty_token.json ({remaining}s remaining)")
            return True
        else:
            print("  [Tastytrade] tasty_token.json expired")
    except Exception as e:
        print(f"  [Tastytrade] Error reading tasty_token.json: {e}")
    return False

def _tt_save_token_file():
    """Save current access token to tasty_token.json for reuse."""
    if not G_TT_ACCESS_TOKEN:
        return
    token_file = BASE_DIR / "tasty_token.json"
    try:
        token_file.write_text(json.dumps({
            "access_token": G_TT_ACCESS_TOKEN,
            "expires_in": 900,
            "expires_at": G_TT_TOKEN_EXPIRY,
            "fetched_at": time.time(),
        }, indent=2))
    except Exception:
        pass

def _tt_get_access_token():
    """Get/refresh Tastytrade OAuth access token (15 min TTL).
    Supports: OAuth creds refresh > cached tasty_token.json file."""
    global G_TT_ACCESS_TOKEN, G_TT_TOKEN_EXPIRY
    now = time.time()
    if G_TT_ACCESS_TOKEN and now < G_TT_TOKEN_EXPIRY - 60:
        return G_TT_ACCESS_TOKEN
    # Try OAuth refresh if credentials available
    if G_TT_CLIENT_ID and G_TT_CLIENT_SECRET and G_TT_REFRESH_TOKEN:
        print("  [Tastytrade] Refreshing OAuth access token...")
        resp = requests.post(
            f"{TT_BASE_URL}/oauth/token",
            data={
                "grant_type":    "refresh_token",
                "refresh_token": G_TT_REFRESH_TOKEN,
                "client_id":     G_TT_CLIENT_ID,
                "client_secret": G_TT_CLIENT_SECRET,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        G_TT_ACCESS_TOKEN = payload["access_token"]
        expires_in = int(payload.get("expires_in", 900))
        G_TT_TOKEN_EXPIRY = now + expires_in
        print(f"  [Tastytrade] Access token obtained (expires in {expires_in}s)")
        _tt_save_token_file()
        return G_TT_ACCESS_TOKEN
    # Fallback: try loading from tasty_token.json
    if _tt_load_token_file():
        return G_TT_ACCESS_TOKEN
    raise RuntimeError("[Tastytrade] No OAuth creds and no valid tasty_token.json")

def _tt_get_streamer_token():
    """Get/refresh dxFeed streamer token (20h TTL)."""
    global G_TT_STREAMER_TOKEN, G_TT_STREAMER_EXPIRY
    now = time.time()
    if G_TT_STREAMER_TOKEN and now < G_TT_STREAMER_EXPIRY - 300:
        return G_TT_STREAMER_TOKEN
    print("  [Tastytrade] Fetching dxFeed streamer token...")
    access_token = _tt_get_access_token()
    resp = requests.get(
        f"{TT_BASE_URL}/api-quote-tokens",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})
    G_TT_STREAMER_TOKEN = data.get("token") or data.get("dxlink-url", "")
    if not G_TT_STREAMER_TOKEN:
        raise RuntimeError(f"[Tastytrade] No streamer token in response")
    G_TT_STREAMER_EXPIRY = now + 19.5 * 3600
    print("  [Tastytrade] Streamer token obtained")
    return G_TT_STREAMER_TOKEN

def _tt_rest_get(path, params=None):
    """Authenticated GET request to Tastytrade REST API."""
    access_token = _tt_get_access_token()
    resp = requests.get(
        f"{TT_BASE_URL}{path}",
        headers={"Authorization": f"Bearer {access_token}"},
        params=params or {},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()

def _tt_parse_feed_data(feed_data, collected, lock):
    """Parse dxFeed FEED_DATA payload (compact or dict format)."""
    if not isinstance(feed_data, list):
        return
    i = 0
    while i < len(feed_data):
        item = feed_data[i]
        if isinstance(item, dict):
            sym = item.get("eventSymbol") or item.get("symbol", "")
            if sym:
                with lock:
                    if sym not in collected:
                        collected[sym] = {}
                    collected[sym].update(item)
            i += 1
            continue
        if isinstance(item, str) and i + 1 < len(feed_data):
            headers = feed_data[i + 1]
            if not isinstance(headers, list):
                i += 1
                continue
            j = i + 2
            while j < len(feed_data) and isinstance(feed_data[j], list):
                row = feed_data[j]
                row_dict = dict(zip(headers, row))
                sym = row_dict.get("eventSymbol") or row_dict.get("symbol", "")
                if sym:
                    with lock:
                        if sym not in collected:
                            collected[sym] = {}
                        collected[sym].update(row_dict)
                j += 1
            i = j
            continue
        i += 1

def _tt_collect_dxfeed(symbols, event_types, timeout_sec=25.0):
    """
    Open a short-lived dxFeed WebSocket session, subscribe to event_types
    for the given symbols, collect data, then disconnect.
    Returns dict keyed by symbol -> dict of field values.
    """
    if not HAS_WEBSOCKET:
        raise RuntimeError("websocket-client library not installed")

    streamer_token = _tt_get_streamer_token()
    collected = {}
    collect_lock = threading.Lock()
    done_event = threading.Event()
    last_data = [time.time()]
    field_map = {}  # event_type -> [field_names] from FEED_CONFIG

    def _on_open(ws):
        ws.send(json.dumps({"type": "SETUP", "channel": 0,
                            "version": "0.1-DXF-JS/0.3.0"}))

    def _on_message(ws, raw):
        try:
            msg = json.loads(raw)
        except Exception:
            return
        msg_type = msg.get("type", "")
        if msg_type == "SETUP":
            ws.send(json.dumps({"type": "AUTH", "channel": 0,
                                "token": streamer_token}))
        elif msg_type == "AUTH_STATE":
            if msg.get("state") == "AUTHORIZED":
                ws.send(json.dumps({"type": "CHANNEL_REQUEST", "channel": 1,
                                    "service": "FEED",
                                    "parameters": {"contract": "AUTO"}}))
        elif msg_type == "CHANNEL_OPENED" and msg.get("channel") == 1:
            ws.send(json.dumps({"type": "FEED_SETUP", "channel": 1,
                                "acceptAggregationPeriod": 0,
                                "acceptDataFormat": "COMPACT"}))
            subs = [{"type": ev, "symbol": sym}
                    for sym in symbols for ev in event_types]
            ws.send(json.dumps({"type": "FEED_SUBSCRIPTION", "channel": 1,
                                "reset": True, "add": subs}))
        elif msg_type == "FEED_CONFIG":
            # Capture field names for each event type
            ef = msg.get("eventFields", {})
            field_map.update(ef)
        elif msg_type == "FEED_DATA":
            last_data[0] = time.time()
            data = msg.get("data", [])
            # COMPACT format: ["EventType", [val1,val2,...], [val1,val2,...], ...]
            i = 0
            while i < len(data):
                item = data[i]
                if isinstance(item, str):
                    # Event type name, followed by data rows
                    ev_type = item
                    headers = field_map.get(ev_type, [])
                    i += 1
                    while i < len(data) and isinstance(data[i], list):
                        row = data[i]
                        if headers:
                            row_dict = dict(zip(headers, row))
                        else:
                            # Fallback: first two are usually eventType, eventSymbol
                            row_dict = {"eventType": row[0] if len(row)>0 else "",
                                        "eventSymbol": row[1] if len(row)>1 else ""}
                        sym = row_dict.get("eventSymbol", "")
                        if sym:
                            with collect_lock:
                                if sym not in collected:
                                    collected[sym] = {}
                                collected[sym].update(row_dict)
                        i += 1
                else:
                    i += 1

    def _on_error(ws, error):
        print(f"  [Tastytrade] dxFeed WS error: {error}")

    def _on_close(ws, code, msg):
        done_event.set()

    ws_app = websocket.WebSocketApp(
        DXFEED_WS_URL,
        on_open=_on_open, on_message=_on_message,
        on_error=_on_error, on_close=_on_close,
    )
    t = threading.Thread(target=ws_app.run_forever,
                         kwargs={"ping_interval": 10, "ping_timeout": 5},
                         daemon=True)
    t.start()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        time.sleep(0.5)
        if done_event.is_set():
            break
        with collect_lock:
            has_data = len(collected) > 0
        if has_data and (time.time() - last_data[0]) > 1.5:
            break
    ws_app.close()
    t.join(timeout=3)
    return collected

_TT_LAST_SPOT = [0.0]  # cached spot from last successful derivation

def _tt_get_nested_chain(expiration):
    """
    Fetch option chain from /option-chains/SPX/nested REST API.
    Filters to ±200 strikes from last known spot.
    Returns sym_map: dxfeed_symbol -> {strike, option_type, expiration}.
    """
    access_token = _tt_get_access_token()
    resp = requests.get(
        f"{TT_BASE_URL}/option-chains/SPX/nested",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=20,
    )
    resp.raise_for_status()
    items = resp.json().get("data", {}).get("items", [])

    # Use cached spot or middle of strikes for filtering
    spot_est = _TT_LAST_SPOT[0] if _TT_LAST_SPOT[0] > 100 else 0
    strike_range = 200

    sym_map = {}
    for chain in items:
        for exp in chain.get("expirations", []):
            if exp.get("expiration-date") != expiration:
                continue
            all_strikes_raw = exp.get("strikes", [])
            # If no cached spot, use middle strike as estimate
            if spot_est == 0 and all_strikes_raw:
                mid = all_strikes_raw[len(all_strikes_raw)//2]
                spot_est = float(mid.get("strike-price", 0))
            for s in all_strikes_raw:
                strike = float(s.get("strike-price", 0))
                if strike <= 0:
                    continue
                if spot_est > 0 and abs(strike - spot_est) > strike_range:
                    continue
                call_sym = s.get("call-streamer-symbol")
                put_sym  = s.get("put-streamer-symbol")
                if call_sym:
                    sym_map[call_sym] = {"strike": strike, "option_type": "C",
                                         "expiration": expiration}
                if put_sym:
                    sym_map[put_sym]  = {"strike": strike, "option_type": "P",
                                         "expiration": expiration}
    return sym_map

def get_spot_tastytrade():
    """Derive SPX spot from 0DTE option chain put-call parity.
    Uses a single ATM pair for speed."""
    access_token = _tt_get_access_token()
    resp = requests.get(
        f"{TT_BASE_URL}/option-chains/SPX/nested",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("data", {}).get("items", [])
    td = date.today().isoformat()
    for chain in items:
        for exp in chain.get("expirations", []):
            if exp.get("expiration-date") != td:
                continue
            strikes = exp.get("strikes", [])
            mid_idx = len(strikes) // 2
            # Try single ATM pair
            for s in strikes[mid_idx:mid_idx+2]:
                k = float(s.get("strike-price", 0))
                cs = s.get("call-streamer-symbol")
                ps = s.get("put-streamer-symbol")
                if k > 0 and cs and ps:
                    data = _tt_collect_dxfeed([cs, ps], ["Quote"], timeout_sec=6.0)
                    cev, pev = data.get(cs, {}), data.get(ps, {})
                    cb = float(cev.get("bidPrice") or 0)
                    ca = float(cev.get("askPrice") or 0)
                    pb = float(pev.get("bidPrice") or 0)
                    pa = float(pev.get("askPrice") or 0)
                    if cb > 0 and ca > 0 and (pb > 0 or pa > 0):
                        cmid = (cb + ca) / 2
                        pmid = (pb + pa) / 2 if pb > 0 and pa > 0 else max(pb, pa)
                        spot = round(k + cmid - pmid, 2)
                        if spot > 100:
                            _TT_LAST_SPOT[0] = spot
                            print(f"  [Tastytrade] SPX spot: {spot}")
                            return spot
            break
        break
    raise RuntimeError("[Tastytrade] Could not derive SPX spot")

def get_0dte_exp_tastytrade():
    """Get today's 0DTE expiration via Tastytrade nested chain."""
    try:
        access_token = _tt_get_access_token()
        resp = requests.get(
            f"{TT_BASE_URL}/option-chains/SPX/nested",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("items", [])
        td = date.today().isoformat()
        exps = set()
        for chain in items:
            for exp in chain.get("expirations", []):
                exps.add(exp.get("expiration-date", ""))
        sorted_exps = sorted(e for e in exps if e)
        today_exps = [d for d in sorted_exps if d == td]
        return today_exps[0] if today_exps else (sorted_exps[0] if sorted_exps else td)
    except Exception as e:
        print(f"  [Tastytrade] get_0dte_exp failed: {e}")
        return date.today().isoformat()

def get_chain_tastytrade(exp):
    """
    Fetch 0DTE options chain from Tastytrade via nested chain REST + dxFeed WS.
    Returns list of dicts matching Tradier-style keys for calc_centroids().
    """
    sym_map = _tt_get_nested_chain(exp)
    if not sym_map:
        print("  [Tastytrade] No option symbols found")
        return []

    dxfeed_syms = list(sym_map.keys())
    print(f"  [Tastytrade] Subscribing to {len(dxfeed_syms)} dxFeed symbols...")

    # Single WS connection for all symbols (faster than chunking)
    all_data = _tt_collect_dxfeed(
        symbols=dxfeed_syms,
        event_types=["Summary", "Trade"],
        timeout_sec=15.0,
    )

    options = []
    for dxfeed_sym, meta in sym_map.items():
        ev = all_data.get(dxfeed_sym, {})
        strike = meta["strike"]
        option_type = "call" if meta["option_type"] == "C" else "put"
        def _safe_int(v):
            try:
                f = float(v)
                return int(f) if f == f else 0  # NaN check
            except (ValueError, TypeError):
                return 0
        volume = _safe_int(ev.get("dayVolume") or ev.get("volume") or 0)
        oi     = _safe_int(ev.get("openInterest") or 0)

        opt = {
            "strike":        strike,
            "option_type":   option_type,
            "volume":        volume,
            "open_interest": oi,
        }
        options.append(opt)

    filled = sum(1 for o in options if o["volume"] > 0 or o["open_interest"] > 0)
    print(f"  [Tastytrade] Got {len(options)} contracts, {filled} with data")
    return options

# ── CORE CENTROID ENGINE ──────────────────────────────────────
# GammaEdge VOLM = pure volume-weighted average strike
# No OI blending. Separate OI tracking for structural levels only.
def calc_centroids(opts, spot, strike_range=200):
    lo, hi = spot - strike_range, spot + strike_range
    cVw=cV=pVw=pV = 0
    cOIw=cOI=pOIw=pOI = 0
    by_strike = {}

    for o in opts:
        k   = float(o.get("strike",0))
        vol = int(o.get("volume",0) or 0)
        oi  = int(o.get("open_interest",0) or 0)
        t   = str(o.get("option_type","")).lower()
        if k < lo or k > hi: continue
        if k not in by_strike:
            by_strike[k]={"strike":k,"cVol":0,"pVol":0,"cOI":0,"pOI":0}
        if t == "call":
            if vol>0: cVw+=k*vol; cV+=vol; by_strike[k]["cVol"]+=vol
            if oi >0: cOIw+=k*oi;  cOI+=oi;  by_strike[k]["cOI"] +=oi
        elif t == "put":
            if vol>0: pVw+=k*vol; pV+=vol; by_strike[k]["pVol"]+=vol
            if oi >0: pOIw+=k*oi;  pOI+=oi;  by_strike[k]["pOI"] +=oi

    # VOLM centroids = pure volume weighted average (GammaEdge methodology)
    call_c = cVw/cV if cV>0 else None
    put_c  = pVw/pV if pV>0 else None

    # OI structural levels (separate - not blended)
    sl = sorted(by_strike.values(), key=lambda x: x["strike"])
    def best_oi(key): return max(sl, key=lambda x:x[key], default=None)
    mc_oi = best_oi("cOI"); mp_oi = best_oi("pOI")

    return {
        "call_c": round(call_c,2) if call_c else None,
        "put_c":  round(put_c,2)  if put_c  else None,
        "call_vol": cV, "put_vol": pV, "total_vol": cV+pV,
        "call_oi":  cOI,"put_oi":  pOI,
        "max_call_oi": mc_oi["strike"] if mc_oi else None,
        "max_put_oi":  mp_oi["strike"] if mp_oi else None,
        "by_strike": sl,
    }

# ── LINEAR REGRESSION ─────────────────────────────────────────
def linreg(series):
    """Returns (slope, intercept, r2) for a series of y values."""
    n = len(series)
    if n < 3: return 0, (series[-1] if series else 0), 0
    x_mean = (n-1)/2
    y_mean = sum(series)/n
    sx = sum((i-x_mean)**2 for i in range(n))
    sxy= sum((i-x_mean)*(y-y_mean) for i,y in enumerate(series))
    slope = sxy/sx if sx else 0
    intercept = y_mean - slope*x_mean
    # R-squared
    ss_res = sum((series[i]-(slope*i+intercept))**2 for i in range(n))
    ss_tot = sum((y-y_mean)**2 for y in series)
    r2 = 1 - ss_res/ss_tot if ss_tot>0 else 0
    return slope, intercept, r2

def project_to_close(history, key, now_et):
    """Project centroid value at 16:15 ET using linear regression."""
    vals = [h[key] for h in history if h.get(key) is not None]
    if len(vals) < 4: return None
    slope, intercept, r2 = linreg(vals)
    # How many more 15s ticks until close?
    close_m = 16*60+15
    cur_m   = now_et.hour*60+now_et.minute
    ticks_left = (close_m - cur_m)*4   # 4 ticks per minute at 15s refresh
    proj = intercept + slope*(len(vals)-1+ticks_left)
    return round(proj, 2)

# ── DAY CHARACTER CLASSIFICATION ──────────────────────────────
def classify_day(history):
    """
    GammaEdge day character logic:
    - BOTH centroids trending SAME direction -> TREND day
    - Centroids trending OPPOSITE directions -> CHOP (range)
    - One centroid changing direction -> REVERSAL potential
    """
    if len(history) < 6:
        return "DEVELOPING", 0, "Need more data (min ~2 min)"

    call_vals = [h["call_c"] for h in history[-12:] if h.get("call_c")]
    put_vals  = [h["put_c"]  for h in history[-12:] if h.get("put_c")]
    if len(call_vals)<4 or len(put_vals)<4:
        return "DEVELOPING", 0, "Insufficient volume data"

    c_slope, _, c_r2 = linreg(call_vals)
    p_slope, _, p_r2 = linreg(put_vals)

    # Slopes in same direction?
    same_dir = (c_slope > 0 and p_slope > 0) or (c_slope < 0 and p_slope < 0)
    c_strong = abs(c_slope) > 0.3 and c_r2 > 0.5
    p_strong = abs(p_slope) > 0.3 and p_r2 > 0.5

    # Rate of change near zero -> settling
    near_zero = abs(c_slope) < 0.15 and abs(p_slope) < 0.15
    conf = int(min((c_r2 + p_r2)/2 * 100, 99))

    if near_zero:
        return "SETTLING", conf, "Centroids stable - range/butterfly setup"
    if same_dir and c_strong and p_strong:
        direction = "UP" if c_slope > 0 else "DOWN"
        return f"TREND {direction}", conf, f"Both centroids trending {direction} - directional bias"
    if not same_dir and (c_strong or p_strong):
        return "CHOP", conf, "Centroids diverging - range-bound, avoid directional"
    if same_dir:
        return f"TREND {('UP' if c_slope>0 else 'DOWN')}", max(conf-20,10), "Weak trend - wait for confirmation"
    return "MIXED", 20, "No clear structure yet"

# ── MAIN REFRESH ──────────────────────────────────────────────
def refresh_data():
    try:
        source = "Tastytrade"
        spot = None
        exp  = None
        opts = None

        # Use Tastytrade as PRIMARY source
        if G_USE_TASTYTRADE:
            if G_USE_TASTYTRADE:
                source = "Tastytrade"
                try:
                    if exp is None:
                        exp = get_0dte_exp_tastytrade()
                    # Get spot + chain in combined flow (single REST + single WS)
                    sym_map = _tt_get_nested_chain(exp)
                    if sym_map:
                        # Spot: derive from ATM pair quotes
                        all_strikes = sorted(set(m["strike"] for m in sym_map.values()))
                        mid_k = all_strikes[len(all_strikes)//2]
                        spot_syms = [s for s,m in sym_map.items()
                                     if abs(m["strike"] - mid_k) < 10][:2]
                        # Fetch chain + spot quotes in one WS connection
                        all_syms = list(sym_map.keys()) + spot_syms
                        all_data = _tt_collect_dxfeed(
                            all_syms, ["Summary", "Trade", "Quote"],
                            timeout_sec=6.0)
                        # Derive spot from ATM Quote data
                        if spot is None:
                            for s, m in sym_map.items():
                                if abs(m["strike"] - mid_k) > 5:
                                    continue
                                ev = all_data.get(s, {})
                                bid = float(ev.get("bidPrice") or 0)
                                ask = float(ev.get("askPrice") or 0)
                                if bid > 0 and ask > 0:
                                    mid = (bid + ask) / 2
                                    if m["option_type"] == "C":
                                        spot = round(m["strike"] + mid, 2)
                                    else:
                                        spot = round(m["strike"] - mid, 2)
                                    _TT_LAST_SPOT[0] = spot
                                    break
                        # Build opts from collected data
                        def _si(v):
                            try:
                                f=float(v); return int(f) if f==f else 0
                            except: return 0
                        opts = []
                        for dxs, meta in sym_map.items():
                            ev = all_data.get(dxs, {})
                            opts.append({
                                "strike": meta["strike"],
                                "option_type": "call" if meta["option_type"]=="C" else "put",
                                "volume": _si(ev.get("dayVolume") or ev.get("volume") or 0),
                                "open_interest": _si(ev.get("openInterest") or 0),
                            })
                        print(f"  [Tastytrade] {len(opts)} contracts, spot={spot}")
                except Exception as tte:
                    print(f"  [Tastytrade failed] {tte}")
                    opts = None

        # Fallback to Massive (secondary source only)
        if not opts:
            if G_USE_MASSIVE:
                source = "Massive"
                print("  Falling back to Massive API...")
                try:
                    if spot is None:
                        spot = get_spot_massive()
                    if exp is None:
                        exp = date.today().isoformat()
                    opts = get_chain_massive(exp)
                except Exception as me:
                    raise RuntimeError(f"All sources failed. Massive: {me}")
            else:
                raise RuntimeError("Tastytrade failed and no fallback available")

        if spot is None or opts is None:
            raise RuntimeError("No data source returned valid data")

        c    = calc_centroids(opts, spot)
        now  = _now_et()
        ts   = now.strftime("%H:%M:%S")
        tsd  = now.strftime("%Y-%m-%d %H:%M:%S ET")

        pcr_oi = (c["put_oi"]/c["call_oi"]) if c["call_oi"]>0 else None

        with CACHE_LOCK:
            h = CACHE["history"]
            # Rate of change vs previous tick
            call_roc = 0; put_roc = 0
            if h and h[-1].get("call_c") and c["call_c"]:
                call_roc = round(c["call_c"] - h[-1]["call_c"], 2)
            if h and h[-1].get("put_c") and c["put_c"]:
                put_roc  = round(c["put_c"]  - h[-1]["put_c"], 2)

            # 5-period smoothed RoC
            roc5 = 0
            if len(h)>=5:
                c5  = [x["call_c"] for x in h[-5:] if x.get("call_c")]
                p5  = [x["put_c"]  for x in h[-5:] if x.get("put_c")]
                if len(c5)>=2: roc5 = round((c5[-1]-c5[0])/len(c5),2)

            h.append({
                "t": ts, "spot": round(spot,2),
                "call_c": c["call_c"], "put_c": c["put_c"],
                "call_roc": call_roc,  "put_roc": put_roc,
            })
            if len(h) > 400: h.pop(0)  # ~100 min at 15s

            # Regression projections
            call_proj = project_to_close(h, "call_c", now) if len(h)>=6 else None
            put_proj  = project_to_close(h, "put_c",  now) if len(h)>=6 else None

            # Settlement range
            settlement = None
            if call_proj and put_proj:
                lo = min(call_proj, put_proj)
                hi = max(call_proj, put_proj)
                mid = round((lo+hi)/2)
                settlement = {"low":lo,"high":hi,"mid":mid}

            # Day character
            is_open,secs,session = market_status()
            post_london = session == "POST-LONDON"
            mins_to_close = secs//60 if is_open else 0
            day_char, day_conf, day_signal = classify_day(h)

            CACHE.update({
                "status":"live","error":None,"spot":round(spot,2),
                "timestamp":tsd,"et_time":ts,
                "call_centroid":c["call_c"],"put_centroid":c["put_c"],
                "call_vol":c["call_vol"],"put_vol":c["put_vol"],"total_vol":c["total_vol"],
                "max_call_oi":c["max_call_oi"],"max_put_oi":c["max_put_oi"],
                "call_oi":c["call_oi"],"put_oi":c["put_oi"],"pcr_oi":round(pcr_oi,2) if pcr_oi else None,
                "call_proj":call_proj,"put_proj":put_proj,"settlement_range":settlement,
                "day_char":day_char,"day_conf":day_conf,"day_signal":day_signal,
                "call_roc":call_roc,"put_roc":put_roc,"roc_5":roc5,
                "by_strike":c["by_strike"],
                "expiration":exp,
                "market_open":is_open,"session_label":session,
                "secs_until_open":secs if not is_open else 0,
                "post_london":post_london,"mins_to_close":mins_to_close,
            })

        cc = c["call_c"] or 0; pc = c["put_c"] or 0
        print(f"  [{ts} ET] SPX {spot:.2f} | Call {cc:.0f} ({call_roc:+.1f}) | Put {pc:.0f} ({put_roc:+.1f}) | Vol {c['total_vol']:,} | {day_char} [{source}]")

    except Exception as e:
        err=str(e); print(f"  [ERROR] {err}")
        with CACHE_LOCK:
            CACHE["error"]=err
            if CACHE.get("spot") is None: CACHE["status"]="error"

def data_loop():
    while True:
        is_open, secs_left, session = market_status()
        if is_open:
            with CACHE_LOCK:
                CACHE["market_open"]   = True
                CACHE["session_label"] = session
                CACHE["post_london"]   = session=="POST-LONDON"
            refresh_data()
            time.sleep(15)
        else:
            with CACHE_LOCK:
                CACHE["market_open"]     = False
                CACHE["session_label"]   = session
                CACHE["secs_until_open"] = secs_left
                CACHE["status"]          = "closed"
            mins = secs_left//60
            print(f"  [{et_str()} ET] {session} - opens in {mins//60}h {mins%60:02d}m")
            time.sleep(min(60, max(10, secs_left-30)))

# ── HTTP server ───────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_json(self,obj):
        body=json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",len(body))
        self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path=self.path.split("?")[0]
        if path in ("/","/index.html"):
            f=BASE_DIR/"spx_centroid.html"
            body=f.read_bytes() if f.exists() else b"<h1>spx_centroid.html missing</h1>"
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Content-Length",len(body))
            self.end_headers(); self.wfile.write(body)
        elif path=="/data":
            with CACHE_LOCK: self.send_json(dict(CACHE))
        else: self.send_response(404); self.end_headers()
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type")
        self.end_headers()

# ── Main ──────────────────────────────────────────────────────
def main():
    global G_TOKEN, G_BASE_URL, G_LABEL
    print("\n  === GammaEdge SPX Centroid  v6  (VOLM methodology) ===\n")
    G_TOKEN, G_BASE_URL, G_LABEL = load_credentials()
    is_open, secs, session = market_status()
    print(f"  API:     {G_LABEL}")
    if G_USE_TASTYTRADE:
        print(f"  Fallback 1: Tastytrade API (ready)")
    if G_USE_MASSIVE:
        fb_num = "2" if G_USE_TASTYTRADE else "1"
        print(f"  Fallback {fb_num}: Massive.com API (ready)")
    print(f"  Session: {session} ({et_str()} ET)")
    print(f"  Method:  Pure 0DTE volume-weighted centroid\n")
    # Start HTTP server FIRST so browser can connect immediately
    server = HTTPServer(("localhost", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"  Server listening on port {PORT}")

    # Open browser right away - it shows boot screen while data loads
    url = f"http://localhost:{PORT}"
    print(f"  Browser -> {url}\n  Ctrl+C to stop\n")
    try:
        webbrowser.open(url)
    except:
        pass  # Silent fail if browser can't open (headless/automated environments)

    # Start data fetch in background (after browser is open)
    threading.Thread(target=data_loop, daemon=True).start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Stopped.")

if __name__=="__main__": main()
