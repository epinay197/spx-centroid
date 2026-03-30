#!/usr/bin/env python3
"""
GammaEdge SPX Centroid  v6  -  Tradier edition
CORRECT methodology matching GammaEdge VOLM:
  - PURE 0DTE cumulative VOLUME weighted centroid (no OI blend)
  - OI kept separately as structural context only
  - Linear regression on each centroid -> settlement projection
  - Day character: TREND / CHOP / REVERSAL
  - Rate of change tracking (CenTab equivalent)
  - Works best after 11:30am ET (London close)
"""

import requests, json, time, threading, math, os, base64
from http.server  import HTTPServer, BaseHTTPRequestHandler
from datetime     import date, datetime, timedelta
from pathlib      import Path

PORT             = int(os.environ.get("PORT", 8765))
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()
BASE_DIR         = Path(__file__).parent

TRADIER_PROD    = "https://api.tradier.com/v1"
TRADIER_SANDBOX = "https://sandbox.tradier.com/v1"

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
    # 1. Check environment variables first (cloud deployment)
    env_prod    = os.environ.get("TRADIER_TOKEN_PRODUCTION", "").strip()
    env_sandbox = os.environ.get("TRADIER_TOKEN_SANDBOX", "").strip()
    env_generic = os.environ.get("TRADIER_TOKEN", "").strip()

    # 2. Fall back to credentials.env file (local development)
    if not env_prod and not env_sandbox and not env_generic:
        env_file = BASE_DIR / "credentials.env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"').strip("'").strip()
                    if not v: continue
                    if k.strip() == "TRADIER_TOKEN_PRODUCTION": env_prod    = v
                    if k.strip() == "TRADIER_TOKEN_SANDBOX":    env_sandbox = v
                    if k.strip() == "TRADIER_TOKEN":            env_generic = v

    candidates = []
    if env_prod:    candidates.append((env_prod,    TRADIER_PROD,    "Production (live)"))
    if env_sandbox: candidates.append((env_sandbox, TRADIER_SANDBOX, "Sandbox (15-min delay)"))
    if env_generic:
        candidates.append((env_generic, TRADIER_PROD,    "Production (live)"))
        candidates.append((env_generic, TRADIER_SANDBOX, "Sandbox (15-min delay)"))

    if not candidates:
        print("  ERROR: No Tradier token found in env vars or credentials.env")
        raise SystemExit(1)

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

    print("  ERROR: No token worked.")
    raise SystemExit(1)

def ah(token):
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

# ── Globals ───────────────────────────────────────────────────
G_TOKEN    = None
G_BASE_URL = None
G_LABEL    = None

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
    GammaEdge VoIM day character — exact methodology definitions:
      TREND UP:    call centroid UP  + put centroid UP   (calls opening, puts closing)
      TREND DOWN:  call centroid DOWN + put centroid DOWN (puts opening, calls closing)
      EXPANSION:   call centroid UP  + put centroid DOWN  (wide range, early divergence)
      CHOP:        call centroid DOWN + put centroid UP    (ONLY this combination)
      SETTLING:    both ROC → 0 AND high R² confidence (trajectory locked)
    """
    if len(history) < 6:
        return "DEVELOPING", 0, "Need more data (min ~2 min)"

    call_vals = [h["call_c"] for h in history[-12:] if h.get("call_c")]
    put_vals  = [h["put_c"]  for h in history[-12:] if h.get("put_c")]
    if len(call_vals) < 6 or len(put_vals) < 6:
        return "DEVELOPING", 0, "Insufficient volume data"

    c_slope, _, c_r2 = linreg(call_vals)
    p_slope, _, p_r2 = linreg(put_vals)

    # Require R² ≥ 0.65 for a slope to be considered meaningful
    c_strong = abs(c_slope) > 0.3 and c_r2 > 0.65
    p_strong = abs(p_slope) > 0.3 and p_r2 > 0.65

    # SETTLING: ROC near zero AND high confidence (trajectory locked)
    near_zero = abs(c_slope) < 0.15 and abs(p_slope) < 0.15
    settled   = near_zero and c_r2 > 0.65 and p_r2 > 0.65

    conf = int(min((c_r2 + p_r2) / 2 * 100, 99))

    if settled:
        return "SETTLING", conf, "Centroids locked - range/butterfly setup"

    # TREND: both centroids moving in same direction
    if c_slope > 0 and p_slope > 0 and c_strong and p_strong:
        return "TREND UP", conf, "Calls UP + Puts UP - calls opening, puts closing - bullish bias"
    if c_slope < 0 and p_slope < 0 and c_strong and p_strong:
        return "TREND DOWN", conf, "Calls DOWN + Puts DOWN - puts opening, calls closing - bearish bias"

    # EXPANSION: calls UP + puts DOWN (early divergence, wide range expected)
    if c_slope > 0 and p_slope < 0 and (c_strong or p_strong):
        return "EXPANSION", conf, "Calls UP + Puts DOWN - wide range day developing, avoid tight stops"

    # CHOP: SPECIFICALLY calls DOWN + puts UP (not any opposing slopes)
    if c_slope < 0 and p_slope > 0 and (c_strong or p_strong):
        return "CHOP", conf, "Calls DOWN + Puts UP - range-bound, avoid directional"

    # Weak same-direction trend (below confidence threshold)
    if (c_slope > 0 and p_slope > 0) or (c_slope < 0 and p_slope < 0):
        direction = "UP" if c_slope > 0 else "DOWN"
        return f"TREND {direction}", max(conf - 20, 10), "Weak trend - wait for R² confirmation"

    return "MIXED", 20, "No clear structure yet"

# ── MAIN REFRESH ──────────────────────────────────────────────
def refresh_data():
    try:
        spot = get_spot()
        exp  = get_0dte_exp()
        opts = get_chain_0dte(exp)
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

            # 5-period smoothed RoC (avg of call + put ROC over 5 ticks)
            roc5 = 0
            if len(h) >= 5:
                c5 = [x["call_c"] for x in h[-5:] if x.get("call_c")]
                p5 = [x["put_c"]  for x in h[-5:] if x.get("put_c")]
                c_roc5 = round((c5[-1] - c5[0]) / (len(c5) - 1), 2) if len(c5) >= 2 else 0
                p_roc5 = round((p5[-1] - p5[0]) / (len(p5) - 1), 2) if len(p5) >= 2 else 0
                roc5 = round((c_roc5 + p_roc5) / 2, 2)

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
        print(f"  [{ts} ET] SPX {spot:.2f} | Call {cc:.0f} ({call_roc:+.1f}) | Put {pc:.0f} ({put_roc:+.1f}) | Vol {c['total_vol']:,} | {day_char}")

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
    def log_message(self, *a): pass

    def _check_auth(self):
        """Returns True if auth passes or no password is set."""
        if not DASHBOARD_PASSWORD:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8", errors="ignore")
                _, password = decoded.split(":", 1)
                if password == DASHBOARD_PASSWORD:
                    return True
            except Exception:
                pass
        return False

    def _require_auth(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="SPX Centroid"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]

        # /health is always public — needed for Railway uptime checks
        if path == "/health":
            self.send_json({"status": "ok", "version": "v6"})
            return

        if not self._check_auth():
            self._require_auth()
            return

        if path in ("/", "/index.html"):
            f = BASE_DIR / "spx_centroid.html"
            body = f.read_bytes() if f.exists() else b"<h1>spx_centroid.html missing</h1>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/data":
            with CACHE_LOCK: self.send_json(dict(CACHE))
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

# ── Main ──────────────────────────────────────────────────────
def main():
    global G_TOKEN, G_BASE_URL, G_LABEL
    print("\n  === GammaEdge SPX Centroid  v6  (VOLM methodology) ===\n")
    G_TOKEN, G_BASE_URL, G_LABEL = load_credentials()
    is_open, secs, session = market_status()
    print(f"  API:     {G_LABEL}")
    print(f"  Session: {session} ({et_str()} ET)")
    print(f"  Method:  Pure 0DTE volume-weighted centroid")
    print(f"  Port:    {PORT}")
    if DASHBOARD_PASSWORD:
        print(f"  Auth:    Basic Auth enabled")
    else:
        print(f"  Auth:    OPEN (set DASHBOARD_PASSWORD env var to protect)")
    print()

    # Bind to 0.0.0.0 so the server is reachable from cloud/LAN as well as localhost
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"  Server listening on 0.0.0.0:{PORT}")

    # Only open browser when running locally (SERVER_MODE env var not set)
    if not os.environ.get("SERVER_MODE"):
        import webbrowser
        url = f"http://localhost:{PORT}"
        print(f"  Browser -> {url}")
        webbrowser.open(url)

    print(f"  Ctrl+C to stop\n")

    # Start data fetch in background
    threading.Thread(target=data_loop, daemon=True).start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Stopped.")

if __name__ == "__main__": main()
