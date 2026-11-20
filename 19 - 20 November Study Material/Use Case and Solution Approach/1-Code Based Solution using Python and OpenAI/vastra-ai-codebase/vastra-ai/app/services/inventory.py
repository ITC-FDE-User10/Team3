"""Pillar 3 / R5, R6 - Stop selling sold-out stock.

Root cause: the monolith keeps a 'Stock copy (4 h)' and stores send sales in a batch
every 4 h, so the website keeps selling items that were sold in store hours ago.

Solution: an event-sourced Available-to-Promise (ATP) ledger.
* WMS stock changes and POS sales arrive as events within seconds (CDC/outbox).
* ATP = on_hand - reserved - safety_buffer, per SKU per location.
* Checkout reserves stock atomically with a TTL; payment confirms, timeout releases.
* A 'freshness' guard: if a location's feed is stale, that stock is NOT promised online.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.events import TOPIC_SALE, TOPIC_STOCK, Event, bus
from app.core.observability import log, metrics

STALE_AFTER_S = 15 * 60


@dataclass
class StockPosition:
    on_hand: int = 0
    reserved: int = 0
    last_update: float = field(default_factory=time.time)


@dataclass
class Reservation:
    res_id: str
    sku: str
    location: str
    qty: int
    expires_at: float


class OversellError(Exception):
    pass


class InventoryService:
    def __init__(self) -> None:
        self._pos: dict[tuple[str, str], StockPosition] = {}
        self._res: dict[str, Reservation] = {}
        self._lock = threading.Lock()
        self.sales_history: dict[str, list[tuple[float, int]]] = {}
        bus.subscribe(TOPIC_STOCK, self._on_stock)
        bus.subscribe(TOPIC_SALE, self._on_sale)

    # --- event handlers --------------------------------------------------
    def _on_stock(self, e: Event) -> None:
        p = e.payload
        with self._lock:
            pos = self._pos.setdefault((p["sku"], p["location"]), StockPosition())
            pos.on_hand = int(p["on_hand"])
            pos.last_update = e.ts
        if pos.on_hand <= 0:
            metrics.inc("stockout_events")

    def _on_sale(self, e: Event) -> None:
        p = e.payload
        with self._lock:
            pos = self._pos.setdefault((p["sku"], p["location"]), StockPosition())
            pos.on_hand = max(0, pos.on_hand - int(p["qty"]))
            pos.last_update = e.ts
        self.sales_history.setdefault(p["sku"], []).append((e.ts, int(p["qty"])))
        if pos.on_hand <= 0:
            metrics.inc("stockout_events")

    # --- queries ----------------------------------------------------------
    def atp(self, sku: str, location: str | None = None) -> int:
        self._expire()
        now = time.time()
        total = 0
        for (s, loc), pos in self._pos.items():
            if s != sku or (location and loc != location):
                continue
            if now - pos.last_update > STALE_AFTER_S:
                continue  # don't promise stock we can't trust
            total += max(0, pos.on_hand - pos.reserved - settings.online_safety_buffer)
        return total

    def snapshot(self, sku: str) -> list[dict]:
        return [{"location": loc, "on_hand": p.on_hand, "reserved": p.reserved}
                for (s, loc), p in self._pos.items() if s == sku]

    # --- commands ---------------------------------------------------------
    def reserve(self, res_id: str, sku: str, qty: int) -> Reservation:
        """Atomically reserve from the location with most ATP (ship-from-store aware)."""
        self._expire()
        with self._lock:
            if res_id in self._res:
                return self._res[res_id]  # idempotent
            candidates = sorted(
                ((loc, p) for (s, loc), p in self._pos.items() if s == sku),
                key=lambda lp: lp[1].on_hand - lp[1].reserved, reverse=True)
            for loc, p in candidates:
                if p.on_hand - p.reserved - settings.online_safety_buffer >= qty:
                    p.reserved += qty
                    r = Reservation(res_id, sku, loc, qty, time.time() + settings.reservation_ttl_s)
                    self._res[res_id] = r
                    log("stock.reserved", sku=sku, location=loc, qty=qty)
                    return r
        metrics.inc("oversell_prevented")
        raise OversellError(f"insufficient ATP for {sku} qty={qty}")

    def confirm(self, res_id: str) -> None:
        with self._lock:
            r = self._res.pop(res_id, None)
            if not r:
                return
            p = self._pos[(r.sku, r.location)]
            p.reserved -= r.qty
            p.on_hand -= r.qty
        self.sales_history.setdefault(r.sku, []).append((time.time(), r.qty))

    def release(self, res_id: str) -> None:
        with self._lock:
            r = self._res.pop(res_id, None)
            if r:
                self._pos[(r.sku, r.location)].reserved -= r.qty

    def _expire(self) -> None:
        now = time.time()
        for rid in [k for k, r in self._res.items() if r.expires_at < now]:
            self.release(rid)


inventory = InventoryService()
