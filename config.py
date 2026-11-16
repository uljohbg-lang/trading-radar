"""Trading Radar (molnversion) – central konfiguration.

Alla siffror som styr analysen finns samlade här, så att du kan justera
strategin på ett ställe utan att röra resten av koden.
"""
from __future__ import annotations

import json
import logging
from datetime import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Sökvägar (i molnet nollställs filerna när appen startas om)
# ---------------------------------------------------------------------------
APP_NAME = "Trading Radar"
APP_DIR = Path(__file__).resolve().parent
WATCHLIST_FILE = APP_DIR / "watchlist.json"
SETTINGS_FILE = APP_DIR / "settings.json"
CACHE_DIR = APP_DIR / "cache"
LOG_DIR = APP_DIR / "logs"

# ---------------------------------------------------------------------------
# Grundinställningar
# ---------------------------------------------------------------------------
DEFAULT_TICKERS = ["SAAB-B.ST", "VOLV-B.ST", "INVE-B.ST", "EVO.ST", "ATCO-A.ST"]
REFRESH_SECONDS = 60
DEFAULT_ORDER_SEK = 10_000
ORDER_STEP_SEK = 1_000
SERVER_PORT = 8501

# Datakälla: "yfinance" (standard) eller "demo" (simulerad data för test).
# Kan även styras med miljövariabeln TRADINGRADAR_PROVIDER.
DATA_PROVIDER = "yfinance"

# ---------------------------------------------------------------------------
# Tid
# ---------------------------------------------------------------------------
TIMEZONE = "Europe/Stockholm"
SIGNAL_WINDOW_START = time(9, 25)   # undvik öppningskaoset
SIGNAL_WINDOW_END = time(16, 30)    # undvik stängningsauktionen
# Om senaste stapeln är äldre än så här räknas datan som inaktuell (=> AVVAKTA).
# Yahoo är ca 15 min fördröjt, så marginalen är generös.
MAX_DATA_AGE_MINUTES = 45

# ---------------------------------------------------------------------------
# Datahämtning
# ---------------------------------------------------------------------------
INTRADAY_INTERVAL = "5m"
INTRADAY_PERIOD = "5d"
HTF_INTERVAL = "1h"
HTF_PERIOD = "1mo"
MIN_BARS = 30  # minsta antal 5-min staplar för att kunna analysera

# ---------------------------------------------------------------------------
# Indikatorer
# ---------------------------------------------------------------------------
EMA_FAST = 9
EMA_SLOW = 21
HTF_EMA = 21
ADX_PERIOD = 14
RSI_PERIOD = 14
ATR_PERIOD = 14
RVOL_LOOKBACK = 20

# ---------------------------------------------------------------------------
# Signalregler
# ---------------------------------------------------------------------------
ADX_MIN = 22.0
RSI_BUY_MIN = 50.0
RSI_BUY_MAX = 70.0
RVOL_MIN = 1.5

# ---------------------------------------------------------------------------
# Risk & position (ATR-baserad)
# ---------------------------------------------------------------------------
STOP_ATR_MULT = 1.5
TARGET_ATR_MULT = 3.0
STOP_ORDER_MARGIN = 0.004  # 0,4 % säkerhetsmarginal mellan trigger och orderkurs

# ---------------------------------------------------------------------------
# Notiser
# ---------------------------------------------------------------------------
NOTIFY_COOLDOWN_MINUTES = 30
NOTIFY_SOUND = "Glass"

# ---------------------------------------------------------------------------
# Statusetiketter
# ---------------------------------------------------------------------------
STATUS_BUY = "🟢 KÖP"
STATUS_SELL = "🔴 SÄLJ"
STATUS_WAIT = "🟡 AVVAKTA"
STATUS_ERROR = "⚠️ INGEN DATA"
STATUS_LOADING = "⏳ LADDAR"


log = logging.getLogger("tradingradar")


# ---------------------------------------------------------------------------
# Bevakningslista & inställningar (JSON-filer)
# ---------------------------------------------------------------------------
def normalize_ticker(raw: str) -> str:
    """Gör om 't.ex. hm-b' till 'HM-B.ST'. Index (^OMX) och utländska lämnas."""
    t = (raw or "").strip().upper().replace(" ", "-")
    if t and "." not in t and not t.startswith("^"):
        t += ".ST"
    return t


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:  # trasig fil -> börja om snyggt
        log.warning("Kunde inte läsa %s: %s", path, exc)
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_watchlist() -> list[str]:
    data = _read_json(WATCHLIST_FILE, None)
    tickers = data.get("tickers") if isinstance(data, dict) else data
    if not isinstance(tickers, list):
        tickers = list(DEFAULT_TICKERS)
        save_watchlist(tickers)
    seen, clean = set(), []
    for t in tickers:
        n = normalize_ticker(str(t))
        if n and n not in seen:
            seen.add(n)
            clean.append(n)
    return clean


def save_watchlist(tickers: list[str]) -> None:
    _write_json(WATCHLIST_FILE, {"tickers": list(tickers)})


def load_settings() -> dict:
    data = _read_json(SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_settings(**updates) -> None:
    data = load_settings()
    data.update(updates)
    _write_json(SETTINGS_FILE, data)
