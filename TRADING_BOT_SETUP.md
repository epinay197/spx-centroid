# Trading Bot Automatic Setup Guide

## Overview
Your trading system is now configured for **100% automatic operation** with:
- ✅ Silent execution (no visible console windows)
- ✅ Automatic credential management (no manual .env editing)
- ✅ Auto-startup on login
- ✅ All repos synced to GitHub

---

## Phase 1: One-Time Setup (per machine)

### Step 1: Configure Master Credentials
Edit this file with your actual credentials:
```
C:\Users\Anwender\AppData\Local\trading_config.env
```

Required values:
```
TRADIER_TOKEN=your_tradier_api_token
TASTYTRADE_CLIENT_ID=your_client_id
TASTYTRADE_CLIENT_SECRET=your_client_secret
TASTYTRADE_REFRESH_TOKEN=your_refresh_token
```

### Step 2: Run Setup Script (as Administrator)
```bash
C:\Users\Anwender\setup_autostart.bat
```

This creates Windows scheduled tasks:
- **TradeBot-Hiro** → Hiro Engine on port 8050
- **TradeBot-SPX** → SPX Centroid on port 8052
- **TradeBot-TRACE** → TRACE SpotGamma on port 8051
- **Docker-Daemon** → Docker Desktop (optional)

### Step 3: Restart Your Computer
All applications will launch silently on login.

---

## Phase 2: Access Your Applications

After setup, access via web browser:

| Service | URL | Purpose |
|---------|-----|---------|
| **Hiro Engine** | http://localhost:8050 | Options analysis dashboard |
| **SPX Centroid** | http://localhost:8052 | SPX gamma/spot tracking |
| **TRACE SpotGamma** | http://localhost:8051 | Real-time spot & gamma |

---

## How It Works

### Credential Loading (Automatic)
Each application loads credentials in this priority:
1. **Windows Environment Variables** (setx command)
2. **Local .env file** (development override)
3. **Master config** (`C:\Users\Anwender\AppData\Local\trading_config.env`)
4. **Legacy config files** (fallback only)

**Result:** Update once in master file, all machines pick it up.

### Silent Execution
- Uses `pythonw.exe` (no console window)
- All tasks hidden in background
- Access via browser only (no terminal windows ever pop up)

### Automatic Startup
- Windows Task Scheduler runs on login
- HighestPrivileges for port binding
- All processes restart if they crash

---

## Deploying to Another Computer

### On Computer 2:
1. Clone repos:
```bash
cd C:\Users\[username]\Documents
git clone https://github.com/epinay197/spx-centroid.git
git clone https://github.com/epinay197/hiro-engine.git
cd C:\Users\[username]\Desktop
git clone https://github.com/epinay197/trace-spotgamma.git
```

2. Copy master config:
```bash
C:\Users\[username]\AppData\Local\trading_config.env
# (from Computer 1 - contains your credentials)
```

3. Run setup:
```bash
C:\Users\[username]\setup_autostart.bat
```

4. Restart

**That's it** - no manual .env file editing needed!

---

## Troubleshooting

### Apps not starting?
```bash
# Check Task Scheduler
tasklist /svc | find "Trading"

# Or manually start one:
C:\Users\Anwender\Documents\Hiro Engine\launch_hiro_silent.bat
```

### Docker not connecting?
```bash
docker info
# If fails, manually start: C:\Program Files\Docker\Docker\Docker Desktop.exe
```

### Credentials not found?
- Verify master config exists: `C:\Users\Anwender\AppData\Local\trading_config.env`
- Check file has `=` delimiter and no spaces in key names
- Restart application after editing

### Check what's running:
```bash
netstat -ano | find "8050"  # Hiro
netstat -ano | find "8051"  # TRACE
netstat -ano | find "8052"  # SPX
```

---

## Files Modified/Added

### New Configuration Files
- `config.py` - Each app (spx_centroid, Hiro, TRACE) now has `config.py`
- `trading_config.env` - Master config in AppData\Local
- `*.bat` launchers - Silent execution scripts

### GitHub Updates
All repos updated with:
- Automatic credential loading
- Silent execution support
- New launcher scripts

---

## Features

### ✅ Automatic
- Credentials load from central location
- Apps auto-start on login
- Restart on crash

### ✅ Silent
- No console windows ever visible
- Background-only execution
- Browser-only access

### ✅ Portable
- Same config works on all machines
- No machine-specific setup needed
- Add new machine in 3 steps

### ✅ Secure
- Credentials in `.gitignore`
- Never committed to GitHub
- Local file only

---

## Next Steps

1. **Edit** `C:\Users\Anwender\AppData\Local\trading_config.env` with your credentials
2. **Run** `C:\Users\Anwender\setup_autostart.bat` as Administrator
3. **Restart** your computer
4. **Access** applications via browser URLs above

---

**Status:** ✅ All systems ready for deployment
**Last Updated:** 2026-04-06
