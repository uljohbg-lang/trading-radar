"""Datakälla för Trading Radar.

Resten av appen pratar ENDAST med gränssnittet `DataProvider`:

    provider.get_intraday(ticker) -> DataFrame (5-min staplar)
    provider.get_hourly(ticker)   -> DataFrame (1-timmes staplar)

Båda returnerar kolumnerna Open, High, Low, Close, Volume med ett
tidsindex i Europe/Stockholm, sorterat och utan dubbletter.

Byta till realtidsdata (t.ex. EODHD eller Twelve Data):
  1. Skapa en ny klass som ärver `DataProvider` och implementerar
     `fetch_bars(ticker, interval, period)` (returnera rå OHLCV-data).
  2. Lägg till den i `PROVIDERS` längst ner.
  3. Ändra `DATA_PROVIDER` i config.py.
Analys- och UI-koden behöver inte röras – `normalize_bars` tar hand om
kolumnnamn, tidszoner och tomma svar.
"""
from __future__ import annotations

import logging
import os
import zlib
from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

import config

log = logging.getLogger("tradingradar.data")
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


class DataProviderError(RuntimeError):
    """Fel vid datahämtning, med ett meddelande som går att visa för användaren."""


def empty_frame() -> pd.DataFrame:
    idx = pd.DatetimeIndex([], tz=config.TIMEZONE)
    return pd.DataFrame({c: pd.Series(dtype=float) for c in OHLCV}, index=idx)


def normalize_bars(raw: pd.DataFrame | None) -> pd.DataFrame:
    """Gör om valfri OHLCV-tabell till appens standardformat."""
    if raw is None or len(raw) == 0:
        return empty_frame()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):  # yf.download-format
        df.columns = df.columns.get_level_values(0)
    df.columns = [str(c).strip().title() for c in df.columns]
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        raise DataProviderError(f"Datakällan saknar kolumner: {', '.join(missing)}")
    df = df[OHLCV].apply(pd.to_numeric, errors="coerce")

    idx = pd.DatetimeIndex(pd.to_datetime(df.index))
    if idx.tz is None:  # naiva tider antas vara UTC
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(config.TIMEZONE)

    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df.dropna(subset=["Open", "High", "Low", "Close"])
    df = df[df["Close"] > 0]
    df["Volume"] = df["Volume"].fillna(0.0).clip(lower=0.0)
    return df


class DataProvider(ABC):
    """Gemensamt gränssnitt för alla datakällor."""

    name: str = "Okänd"
    delay_note: str = ""

    @abstractmethod
    def fetch_bars(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        """Hämta rå OHLCV-data. Får returnera tom tabell eller kasta fel."""

    def get_bars(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        try:
            raw = self.fetch_bars(ticker, interval, period)
        except DataProviderError:
            raise
        except Exception as exc:
            raise DataProviderError(f"Kunde inte hämta {interval}-data för {ticker}: {exc}") from exc
        return normalize_bars(raw)

    def get_intraday(self, ticker: str) -> pd.DataFrame:
        return self.get_bars(ticker, config.INTRADAY_INTERVAL, config.INTRADAY_PERIOD)

    def get_hourly(self, ticker: str) -> pd.DataFrame:
        return self.get_bars(ticker, config.HTF_INTERVAL, config.HTF_PERIOD)


class YFinanceProvider(DataProvider):
    """Gratis men ca 15 min fördröjd data från Yahoo Finance."""

    name = "Yahoo Finance"
    delay_note = "ca 15 min fördröjd"

    def __init__(self) -> None:
        import yfinance as yf

        self._yf = yf
        try:  # håll yfinance-cachen inne i appmappen
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            yf.set_tz_cache_location(str(config.CACHE_DIR))
        except Exception:
            pass

    def fetch_bars(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        return self._yf.Ticker(ticker).history(
            period=period, interval=interval, auto_adjust=False, prepost=False, actions=False
        )


class DemoProvider(DataProvider):
    """Simulerad börsdag – används för test när marknaden är stängd."""

    name = "Demodata (simulerad)"
    delay_note = "simulerad – endast för test"

    def fetch_bars(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        five = self._five_minute(ticker)
        if interval == "5m":
            return five
        if interval == "1h":
            agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
            return five.resample("1h", label="left", closed="left").agg(agg).dropna()
        raise DataProviderError(f"Demodata stöder inte intervallet {interval}")

    @staticmethod
    def _five_minute(ticker: str) -> pd.DataFrame:
        tz = config.TIMEZONE
        now = pd.Timestamp.now(tz=tz)
        days = pd.bdate_range(end=now.tz_localize(None).normalize(), periods=5)
        parts = [
            pd.date_range(d + pd.Timedelta(hours=9), d + pd.Timedelta(hours=17, minutes=25), freq="5min", tz=tz)
            for d in days
        ]
        idx = parts[0].append(parts[1:])
        seed = zlib.crc32(f"{ticker}-{days[-1].date()}".encode())
        rng = np.random.default_rng(seed)
        n = len(idx)
        regime = np.repeat(rng.normal(0, 0.0018, n // 18 + 1), 18)[:n]
        ret = regime + rng.normal(0, 0.0022, n)
        close = (50 + seed % 400) * np.exp(np.cumsum(ret))
        open_ = np.concatenate([[close[0]], close[:-1]])
        wiggle = np.abs(rng.normal(0, 0.0015, n))
        high = np.maximum(open_, close) * (1 + wiggle)
        low = np.minimum(open_, close) * (1 - wiggle)
        volume = rng.lognormal(10, 0.4, n) * (1 + 2.5 * (np.abs(regime) > 0.0018))
        df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)
        return df[df.index <= now]


PROVIDERS = {
    "yfinance": YFinanceProvider,
    "demo": DemoProvider,
    # "eodhd": EodhdProvider,       # framtida realtidskälla
    # "twelvedata": TwelveDataProvider,
}


def get_provider(name: str | None = None) -> DataProvider:
    key = (name or os.environ.get("TRADINGRADAR_PROVIDER") or config.DATA_PROVIDER).lower()
    if key not in PROVIDERS:
        log.warning("Okänd datakälla '%s' – använder yfinance", key)
        key = "yfinance"
    return PROVIDERS[key]()
