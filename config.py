"""
SPX Centroid Configuration
Reads from environment variables and .env files
Master config: C:\Users\Anwender\AppData\Local\trading_config.env
"""
import os
from pathlib import Path

# Load credentials from multiple sources (in priority order)
def _env(key, default=""):
    # 1. System environment variables (highest priority)
    if key in os.environ:
        return os.environ[key].strip()
    
    # 2. Local .env file (development override)
    local_env = Path(__file__).parent / ".env"
    if local_env.exists():
        for line in local_env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    
    # 3. Master config file (shared across machines)
    master_env = Path.home() / "AppData" / "Local" / "trading_config.env"
    if master_env.exists():
        for line in master_env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    
    # 4. Fall back to credentials.env (legacy)
    legacy_env = Path(__file__).parent / "credentials.env"
    if legacy_env.exists():
        for line in legacy_env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    
    # 5. Default value
    return default

# Tradier API Configuration
TRADIER_TOKEN    = _env("TRADIER_TOKEN", "YOUR_TRADIER_API_TOKEN_HERE")
TRADIER_BASE_URL = _env("TRADIER_BASE_URL", "https://api.tradier.com/v1")

# Tastytrade OAuth (live data fallback)
TT_CLIENT_ID      = _env("TASTYTRADE_CLIENT_ID")
TT_CLIENT_SECRET  = _env("TASTYTRADE_CLIENT_SECRET")
TT_REFRESH_TOKEN  = _env("TASTYTRADE_REFRESH_TOKEN")
TT_API_BASE       = "https://api.tastytrade.com"
TT_DXFEED_WS      = "wss://tasty-openapi-ws.dxfeed.com/realtime"

# Symbol Configuration
SPX_SYMBOL   = "SPX"
SPXW_SYMBOL  = "SPXW"
SPY_SYMBOL   = "SPY"

# Trading Parameters
REFRESH_INTERVAL_MIN = 10
STRIKE_RANGE         = 300   # ± strikes around spot
RISK_FREE_RATE       = 0.05
SPX_MULTIPLIER       = 100

# Data Parameters
STABILITY_WINDOW    = 6
HIRO_LOOKBACK_MIN   = 30
STATS_LOOKBACK_DAYS = 30

# Timezone & Market Hours
TIMEZONE     = "America/New_York"
MARKET_OPEN  = "09:30"
MARKET_CLOSE = "16:00"

# Port Configuration
PORT = int(_env("SPX_PORT", "8052"))
HOST = "0.0.0.0"

# Debug Mode
DEBUG = _env("DEBUG", "false").lower() == "true"
