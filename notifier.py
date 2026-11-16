"""Larm när en aktie byter status till KÖP/SÄLJ (30 min cooldown per aktie).

Molnversionen skickar push-notiser via ntfy.sh. De går att ta emot utan
installation på https://ntfy.sh/app i webbläsaren (eller i ntfy-appen i
mobilen). Dessutom visas larmet som en ruta med ljud i Trading Radar.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import datetime, timedelta

import config as C

log = logging.getLogger("tradingradar.notifier")
NTFY_SERVER = "https://ntfy.sh"


def send_ntfy(topic: str, title: str, message: str, tags: list[str] | None = None, priority: int = 4) -> bool:
    if not topic:
        return False
    body = json.dumps({"topic": topic, "title": title, "message": message,
                       "tags": tags or [], "priority": priority}).encode("utf-8")
    req = urllib.request.Request(NTFY_SERVER, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        log.warning("Kunde inte skicka ntfy-notis: %s", exc)
        return False


class Notifier:
    def __init__(self, cooldown_minutes: int = C.NOTIFY_COOLDOWN_MINUTES):
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.topic: str = ""
        self._lock = threading.Lock()
        self._last_status: dict[str, str] = {}
        self._last_sent: dict[str, datetime] = {}
        self.events: list[dict] = []  # visas som rutor i appen
        self._next_id = 1

    def process(self, ticker: str, status: str, price: float, detail: str = "") -> bool:
        with self._lock:
            previous = self._last_status.get(ticker)
            self._last_status[ticker] = status
            if status not in (C.STATUS_BUY, C.STATUS_SELL) or status == previous:
                return False
            now = datetime.now().astimezone()
            last = self._last_sent.get(ticker)
            if last is not None and now - last < self.cooldown:
                return False
            self._last_sent[ticker] = now
            self.events.append({"id": self._next_id, "time": now, "ticker": ticker,
                                "status": status, "price": price, "detail": detail})
            self.events = self.events[-50:]
            self._next_id += 1
            topic = self.topic
        is_buy = status == C.STATUS_BUY
        send_ntfy(topic, f"{'KÖP' if is_buy else 'SÄLJ'}: {ticker} @ {price:.2f} SEK",
                  detail or "Öppna Trading Radar för order-nivåer.",
                  tags=["green_circle" if is_buy else "red_circle"])
        return True

    def events_after(self, event_id: int) -> list[dict]:
        with self._lock:
            return [e for e in self.events if e["id"] > event_id]

    def last_event_id(self) -> int:
        with self._lock:
            return self._next_id - 1
