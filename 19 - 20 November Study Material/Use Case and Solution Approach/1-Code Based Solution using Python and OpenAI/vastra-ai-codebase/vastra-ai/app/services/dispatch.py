"""Pillar 3 / R18 - Slow order despatch.

Today orders reach the warehouse 'next day' via batch. With order events flowing in
real time, this module (1) routes each order to the best node (DC or ship-from-store)
and (2) scores pick priority so SLA-at-risk orders are picked first. Scoring is a
transparent weighted model; weights are tuned from historic SLA breaches.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class Node:
    node_id: str
    pincode_prefix: str
    pick_capacity_per_hr: int
    backlog: int
    cost_per_order: float


def choose_node(nodes: list[Node], customer_pincode: str, atp_by_node: dict[str, int], qty: int) -> dict:
    """Lowest expected cost-per-order among nodes that can fulfil, with locality bonus."""
    best, best_score = None, float("inf")
    for n in nodes:
        if atp_by_node.get(n.node_id, 0) < qty:
            continue
        local = customer_pincode.startswith(n.pincode_prefix)
        shipping = 40.0 if local else 95.0
        queue_hours = n.backlog / max(n.pick_capacity_per_hr, 1)
        score = n.cost_per_order + shipping + 10 * queue_hours
        if score < best_score:
            best, best_score = n, score
    if not best:
        return {"node": None, "reason": "no node can fulfil"}
    return {"node": best.node_id, "expected_cost_inr": round(best_score, 2)}


def pick_priority(order: dict, now: float | None = None) -> float:
    """0-100. Higher = pick first."""
    now = now or time.time()
    hours_to_sla = (order["sla_deadline"] - now) / 3600
    sla_risk = max(0.0, min(1.0, 1 - hours_to_sla / 24))
    express = 1.0 if order.get("shipping") == "express" else 0.0
    value = min(order.get("value_inr", 0) / 20000, 1.0)
    loyalty = {"gold": 1.0, "silver": 0.5}.get(order.get("loyalty_tier", ""), 0.0)
    return round(100 * (0.5 * sla_risk + 0.2 * express + 0.15 * value + 0.15 * loyalty), 1)


def build_pick_wave(orders: list[dict], wave_size: int = 50) -> list[dict]:
    ranked = sorted(orders, key=pick_priority, reverse=True)
    return [{**o, "priority": pick_priority(o)} for o in ranked[:wave_size]]
