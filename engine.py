"""Bakgrundsmotor: skannar bevakningslistan var 60:e sekund (så länge appen
är vaken i molnet) och larmar vid nya signaler."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import config as C
from analyzer import AnalysisResult, analyze
from data_provider import DataProvider, DataProviderError, get_provider
from notifier import Notifier

log = logging.getLogger("tradingradar.engine")


class ScannerEngine:
    def __init__(self, provider: DataProvider | None = None, notifier: Notifier | None = None):
        self.provider = provider or get_provider()
        self.notifier = notifier or Notifier()
        self._results: dict[str, AnalysisResult] = {}
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._first_done = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_scan: datetime | None = None
        self._hourly_cache: dict[str, tuple[float, object]] = {}

    # -- livscykel ---------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="TradingRadarScanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_now()
            except Exception:
                log.exception("Skanningen misslyckades")
            self._wake.wait(C.REFRESH_SECONDS)
            self._wake.clear()

    # -- skanning ----------------------------------------------------------
    def scan_ticker(self, ticker: str) -> AnalysisResult:
        try:
            df5 = self.provider.get_intraday(ticker)
            if df5.empty:
                return AnalysisResult(ticker, status=C.STATUS_ERROR,
                                      error="Datakällan returnerade ingen data för tickern.")
            df1h = self._hourly(ticker)
            return analyze(ticker, df5, df1h)
        except DataProviderError as exc:
            return AnalysisResult(ticker, status=C.STATUS_ERROR, error=str(exc))
        except Exception as exc:
            log.exception("Fel vid analys av %s", ticker)
            return AnalysisResult(ticker, status=C.STATUS_ERROR, error=f"Oväntat fel: {exc}")

    def _hourly(self, ticker: str):
        """1h-data ändras långsamt – hämta högst var 5:e minut (snällare mot Yahoo)."""
        cached = self._hourly_cache.get(ticker)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        df = self.provider.get_hourly(ticker)
        self._hourly_cache[ticker] = (time.monotonic(), df)
        return df

    def scan_now(self) -> None:
        with self._scan_lock:
            tickers = C.load_watchlist()
            results = {t: self.scan_ticker(t) for t in tickers}
            with self._lock:
                self._results = results
                self.last_scan = datetime.now().astimezone()
            self._first_done.set()
        for r in results.values():
            if r.error is None:
                detail = f"RVOL {r.rvol:.1f} · ADX {r.adx:.0f} · RSI {r.rsi:.0f}"
                self.notifier.process(r.ticker, r.status, r.price, detail)

    def refresh_soon(self) -> None:
        self._wake.set()

    def wait_for_first_scan(self, timeout: float = 90) -> bool:
        return self._first_done.wait(timeout)

    def snapshot(self) -> tuple[dict[str, AnalysisResult], datetime | None]:
        with self._lock:
            return dict(self._results), self.last_scan
