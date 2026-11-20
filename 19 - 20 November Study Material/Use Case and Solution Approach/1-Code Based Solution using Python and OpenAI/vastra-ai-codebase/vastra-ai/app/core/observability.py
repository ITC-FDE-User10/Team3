"""Structured logging + KPI metrics registry, organised by value pillar.

Exported in Prometheus text format at /metrics so Grafana/Datadog can build the
pillar dashboards (AOV, conversion, cart abandonment, oversell, stock-outs, CPO, DQ).
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from contextlib import contextmanager

logger = logging.getLogger("vastra")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def log(event: str, **fields) -> None:
    logger.info(json.dumps({"ts": round(time.time(), 3), "event": event, **fields}, default=str))


class Metrics:
    def __init__(self) -> None:
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = {}
        self.latencies: dict[str, list[float]] = defaultdict(list)

    def inc(self, name: str, value: float = 1.0) -> None:
        self.counters[name] += value

    def set(self, name: str, value: float) -> None:
        self.gauges[name] = value

    @contextmanager
    def timer(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.latencies[name].append((time.perf_counter() - start) * 1000)

    def p95(self, name: str) -> float:
        xs = sorted(self.latencies.get(name, []))
        return xs[int(0.95 * (len(xs) - 1))] if xs else 0.0

    # ---- Pillar KPIs -------------------------------------------------------
    def kpis(self) -> dict:
        c = self.counters
        orders = c["orders_placed"]
        sessions = max(c["sessions"], 1)
        carts = max(c["carts_created"], 1)
        return {
            "pillar_1_sales_revenue": {
                "aov_inr": round(c["revenue_inr"] / orders, 2) if orders else 0.0,
                "reco_attach_rate": round(c["reco_items_ordered"] / max(c["items_ordered"], 1), 3),
            },
            "pillar_2_user_friction": {
                "conversion_rate": round(orders / sessions, 4),
                "cart_abandonment_rate": round(1 - orders / carts, 4) if c["carts_created"] else 0.0,
                "checkout_p95_ms": round(self.p95("checkout"), 2),
                "search_p95_ms": round(self.p95("search"), 2),
            },
            "pillar_3_logistics_backend": {
                "oversell_incidents": c["oversell_prevented"],
                "stockout_events": c["stockout_events"],
                "reorders_suggested": c["reorders_suggested"],
                "cost_per_order_inr": round(c["fulfilment_cost_inr"] / orders, 2) if orders else 0.0,
            },
            "pillar_4_cross_cutting": {
                "dq_records_rejected": c["dq_rejected"],
                "dq_records_auto_fixed": c["dq_autofixed"],
                "invoices_auto_matched": c["invoice_matched"],
                "invoices_exceptions": c["invoice_exceptions"],
                "guardrail_blocks": c["guardrail_blocks"],
            },
        }

    def prometheus(self) -> str:
        lines = [f"vastra_{k} {v}" for k, v in self.counters.items()]
        lines += [f"vastra_{k} {v}" for k, v in self.gauges.items()]
        lines += [f'vastra_latency_p95_ms{{op="{k}"}} {self.p95(k):.2f}' for k in self.latencies]
        return "\n".join(lines) + "\n"


metrics = Metrics()
