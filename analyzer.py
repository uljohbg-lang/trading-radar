"""Analysmotor: indikatorer (ren pandas/numpy) och beslutsmatris."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import config as C


# ---------------------------------------------------------------------------
# Indikatorer
# ---------------------------------------------------------------------------
def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def wilder(series: pd.Series, n: int) -> pd.Series:
    """Wilders utjämning (används av RSI, ATR och ADX)."""
    return series.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = wilder(delta.clip(lower=0), n)
    loss = wilder(-delta.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out = out.mask((loss == 0) & (gain > 0), 100.0)
    out = out.mask((loss == 0) & (gain == 0), 50.0)
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift()
    return pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return wilder(true_range(df), n)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_ = wilder(true_range(df), n).replace(0, np.nan)
    plus_di = 100 * wilder(plus_dm, n) / atr_
    minus_di = 100 * wilder(minus_dm, n) / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder(dx, n)


def intraday_vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP som nollställs varje handelsdag (svensk tid)."""
    day = df.index.normalize()
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_pv = (tp * df["Volume"]).groupby(day).cumsum()
    cum_v = df["Volume"].groupby(day).cumsum()
    vwap = cum_pv / cum_v.replace(0, np.nan)
    # Ingen volym ännu (t.ex. första stapeln) -> använd dagens snittpris
    fallback = tp.groupby(day).expanding().mean().reset_index(level=0, drop=True)
    return vwap.fillna(fallback)


def relative_volume(volume: pd.Series, lookback: int = 20) -> pd.Series:
    """Aktuell stapels volym / snittet av de 20 föregående staplarna."""
    avg = volume.rolling(lookback, min_periods=lookback).mean().shift(1)
    return volume / avg.replace(0, np.nan)


def compute_indicators(df5: pd.DataFrame, df1h: pd.DataFrame | None) -> pd.DataFrame:
    f = df5.copy()
    f["vwap"] = intraday_vwap(f)
    f["ema_fast"] = ema(f["Close"], C.EMA_FAST)
    f["ema_slow"] = ema(f["Close"], C.EMA_SLOW)
    f["adx"] = adx(f, C.ADX_PERIOD)
    f["rsi"] = rsi(f["Close"], C.RSI_PERIOD)
    f["atr"] = atr(f, C.ATR_PERIOD)
    f["rvol"] = relative_volume(f["Volume"], C.RVOL_LOOKBACK)

    if df1h is not None and len(df1h) > 0:
        htf = ema(df1h["Close"], C.HTF_EMA).dropna()
        # En timstapel är "känd" först när den stängt -> flytta fram en timme,
        # så att historiska signaler inte tjuvkikar in i framtiden.
        htf.index = htf.index + pd.Timedelta(hours=1)
        htf = htf[~htf.index.duplicated(keep="last")].sort_index()
        f["htf_ema"] = htf.reindex(f.index, method="ffill") if len(htf) else np.nan
    else:
        f["htf_ema"] = np.nan

    minutes = f.index.hour * 60 + f.index.minute
    start = C.SIGNAL_WINDOW_START.hour * 60 + C.SIGNAL_WINDOW_START.minute
    end = C.SIGNAL_WINDOW_END.hour * 60 + C.SIGNAL_WINDOW_END.minute
    f["in_window"] = (minutes >= start) & (minutes <= end)
    return f


def evaluate_signals(f: pd.DataFrame) -> pd.DataFrame:
    """Beslutsmatrisen, stapel för stapel. NaN räknas alltid som 'nej'."""
    c = f["Close"]
    trend_ok = (f["adx"] > C.ADX_MIN) & (f["rvol"] >= C.RVOL_MIN)
    buy = (
        f["in_window"]
        & (c > f["vwap"]) & (c > f["htf_ema"])
        & (f["ema_fast"] > f["ema_slow"])
        & trend_ok
        & f["rsi"].between(C.RSI_BUY_MIN, C.RSI_BUY_MAX)
    )
    sell = (
        f["in_window"]
        & (c < f["vwap"]) & (c < f["htf_ema"])
        & (f["ema_fast"] < f["ema_slow"])
        & trend_ok
    )
    f["buy"] = buy.fillna(False).astype(bool)
    f["sell"] = sell.fillna(False).astype(bool)
    f["signal"] = np.select([f["buy"], f["sell"]], [C.STATUS_BUY, C.STATUS_SELL], default=C.STATUS_WAIT)
    return f


# ---------------------------------------------------------------------------
# Resultat
# ---------------------------------------------------------------------------
@dataclass
class Check:
    label: str
    detail: str
    ok: bool


@dataclass
class TradePlan:
    direction: str  # "long" eller "short"
    shares: int
    entry: float
    order_value: float
    stop_trigger: float
    stop_order: float
    take_profit: float
    max_loss_sek: float
    max_loss_pct: float
    expected_profit_sek: float
    expected_profit_pct: float

    @property
    def risk_reward(self) -> float:
        return self.expected_profit_sek / self.max_loss_sek if self.max_loss_sek else float("nan")


@dataclass
class AnalysisResult:
    ticker: str
    status: str = C.STATUS_WAIT
    status_reason: str = ""
    price: float = float("nan")
    bar_time: pd.Timestamp | None = None
    vwap: float = float("nan")
    vwap_dist_pct: float = float("nan")
    rvol: float = float("nan")
    adx: float = float("nan")
    rsi: float = float("nan")
    atr: float = float("nan")
    ema_fast: float = float("nan")
    ema_slow: float = float("nan")
    htf_ema: float = float("nan")
    in_window: bool = False
    data_fresh: bool = False
    last_signal_status: str | None = None
    last_signal_time: pd.Timestamp | None = None
    last_signal_price: float = float("nan")
    buy_checks: list = field(default_factory=list)
    sell_checks: list = field(default_factory=list)
    frame: pd.DataFrame | None = field(default=None, repr=False)
    error: str | None = None

    @property
    def is_signal(self) -> bool:
        return self.status in (C.STATUS_BUY, C.STATUS_SELL)


def _f(x) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return v


def _ok(cond) -> bool:
    try:
        return bool(cond)
    except Exception:
        return False


def _n(x: float, d: int = 2) -> str:
    return "–" if x is None or math.isnan(x) else f"{x:.{d}f}"


def analyze(ticker: str, df5: pd.DataFrame, df1h: pd.DataFrame | None,
            now: pd.Timestamp | None = None) -> AnalysisResult:
    if df5 is None or len(df5) < C.MIN_BARS:
        return AnalysisResult(
            ticker, status=C.STATUS_ERROR,
            error="För lite 5-minutersdata. Kontrollera tickern (svenska aktier slutar på .ST).",
        )

    f = evaluate_signals(compute_indicators(df5, df1h))
    last = f.iloc[-1]
    bar_time = f.index[-1]
    now = now if now is not None else pd.Timestamp.now(tz=C.TIMEZONE)
    age_min = (now - bar_time).total_seconds() / 60

    r = AnalysisResult(ticker=ticker, frame=f, bar_time=bar_time)
    r.price, r.vwap, r.rvol = _f(last["Close"]), _f(last["vwap"]), _f(last["rvol"])
    r.adx, r.rsi, r.atr = _f(last["adx"]), _f(last["rsi"]), _f(last["atr"])
    r.ema_fast, r.ema_slow, r.htf_ema = _f(last["ema_fast"]), _f(last["ema_slow"]), _f(last["htf_ema"])
    r.vwap_dist_pct = (r.price / r.vwap - 1) * 100 if r.vwap else float("nan")
    r.in_window = bool(last["in_window"])
    r.data_fresh = age_min <= C.MAX_DATA_AGE_MINUTES

    win = f"{C.SIGNAL_WINDOW_START:%H:%M}–{C.SIGNAL_WINDOW_END:%H:%M}"
    if not r.data_fresh:
        r.status = C.STATUS_WAIT
        r.status_reason = (f"Senaste data är från {bar_time:%Y-%m-%d %H:%M} – börsen är stängd "
                           "eller datan försenad. Inga larm.")
    elif not r.in_window:
        r.status = C.STATUS_WAIT
        r.status_reason = f"Utanför signalfönstret {win} (öppnings-/stängningsbrus). Inga larm."
    else:
        r.status = str(last["signal"])
        r.status_reason = {
            C.STATUS_BUY: "Alla köpvillkor uppfyllda – stark upptrend med hög volym.",
            C.STATUS_SELL: "Alla säljvillkor uppfyllda – stark nedtrend med hög volym.",
        }.get(r.status, "Inte alla villkor uppfyllda – vänta på ett tydligare läge.")

    sig = f[f["signal"] != C.STATUS_WAIT]
    if len(sig):
        r.last_signal_status = str(sig["signal"].iloc[-1])
        r.last_signal_time = sig.index[-1]
        r.last_signal_price = _f(sig["Close"].iloc[-1])

    p = r.price
    r.buy_checks = [
        Check(f"Inom tidsfönstret {win}", f"stapel {bar_time:%H:%M}", r.in_window),
        Check("Pris över VWAP", f"{_n(p)} vs {_n(r.vwap)}", _ok(p > r.vwap)),
        Check("Pris över 1h EMA(21)", f"{_n(p)} vs {_n(r.htf_ema)}", _ok(p > r.htf_ema)),
        Check("EMA(9) över EMA(21) på 5 min", f"{_n(r.ema_fast)} vs {_n(r.ema_slow)}", _ok(r.ema_fast > r.ema_slow)),
        Check(f"Stark trend: ADX > {C.ADX_MIN:g}", f"ADX {_n(r.adx, 1)}", _ok(r.adx > C.ADX_MIN)),
        Check(f"RSI mellan {C.RSI_BUY_MIN:g} och {C.RSI_BUY_MAX:g}", f"RSI {_n(r.rsi, 1)}",
              _ok(C.RSI_BUY_MIN <= r.rsi <= C.RSI_BUY_MAX)),
        Check(f"Hög volym: RVOL ≥ {C.RVOL_MIN:g}", f"RVOL {_n(r.rvol)}", _ok(r.rvol >= C.RVOL_MIN)),
    ]
    r.sell_checks = [
        Check(f"Inom tidsfönstret {win}", f"stapel {bar_time:%H:%M}", r.in_window),
        Check("Pris under VWAP", f"{_n(p)} vs {_n(r.vwap)}", _ok(p < r.vwap)),
        Check("Pris under 1h EMA(21)", f"{_n(p)} vs {_n(r.htf_ema)}", _ok(p < r.htf_ema)),
        Check("EMA(9) under EMA(21) på 5 min", f"{_n(r.ema_fast)} vs {_n(r.ema_slow)}", _ok(r.ema_fast < r.ema_slow)),
        Check(f"Stark trend: ADX > {C.ADX_MIN:g}", f"ADX {_n(r.adx, 1)}", _ok(r.adx > C.ADX_MIN)),
        Check(f"Hög volym: RVOL ≥ {C.RVOL_MIN:g}", f"RVOL {_n(r.rvol)}", _ok(r.rvol >= C.RVOL_MIN)),
    ]
    return r


def trade_plan(price: float, atr_value: float, direction: str, order_sek: float) -> TradePlan | None:
    """ATR-baserad position, stop loss och målkurs."""
    if not (price and atr_value) or math.isnan(price) or math.isnan(atr_value) or price <= 0 or atr_value <= 0:
        return None
    shares = max(1, int(order_sek // price))
    if direction == "short":
        trigger = price + C.STOP_ATR_MULT * atr_value
        order = trigger * (1 + C.STOP_ORDER_MARGIN)
        target = price - C.TARGET_ATR_MULT * atr_value
        loss = (trigger - price) * shares
        profit = (price - target) * shares
    else:
        direction = "long"
        trigger = price - C.STOP_ATR_MULT * atr_value
        order = trigger * (1 - C.STOP_ORDER_MARGIN)
        target = price + C.TARGET_ATR_MULT * atr_value
        loss = (price - trigger) * shares
        profit = (target - price) * shares
    value = shares * price
    return TradePlan(direction, shares, price, value, trigger, order, target,
                     loss, loss / value * 100, profit, profit / value * 100)
