"""Pillar 2 / R1, R10 (conversion to order) and R2, R3, R4, R11 (cart abandonment).

Today 'Checkout (serial)' calls stock, price, payment, courier and SMS one after
another on a 2014 monolith; a slow courier or SMS API stalls the whole order.

New checkout flow (strangler-fig, in front of the monolith):
* Idempotency-Key on every POST - safe retries, no double charges.
* Price from the single Price Authority (R4) - no surprise price change at payment.
* Stock reservation with TTL (R5/R6) - no 'item no longer available' after paying.
* Only payment is on the synchronous path; courier booking, SMS/email, ERP posting
  happen asynchronously via events (outbox) with retries + dead-letter queue.
* Circuit breaker + timeout on the payment gateway.
* Abandonment-risk model scores live carts and triggers a recovery nudge.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.core.events import TOPIC_ORDER, Event, bus
from app.core.observability import log, metrics
from app.services.inventory import OversellError, inventory
from app.services.pricing import pricing


class CircuitBreaker:
    def __init__(self, fail_threshold: int = 5, reset_after_s: float = 30) -> None:
        self.fail_threshold, self.reset_after_s = fail_threshold, reset_after_s
        self.failures, self.opened_at = 0, 0.0

    def allow(self) -> bool:
        if self.failures < self.fail_threshold:
            return True
        return time.time() - self.opened_at > self.reset_after_s  # half-open

    def record(self, ok: bool) -> None:
        if ok:
            self.failures = 0
        else:
            self.failures += 1
            if self.failures >= self.fail_threshold:
                self.opened_at = time.time()


payment_breaker = CircuitBreaker()


def fake_payment_gateway(amount: float, token: str) -> dict:
    """Stand-in for Razorpay/PayU/etc. 'tok_fail' simulates a decline."""
    if token == "tok_fail":
        return {"status": "declined"}
    return {"status": "captured", "payment_id": "pay_" + uuid.uuid4().hex[:10]}


@dataclass
class CheckoutService:
    _idem: dict[str, dict] = field(default_factory=dict)
    orders: dict[str, dict] = field(default_factory=dict)

    def place_order(self, idem_key: str, customer: dict, lines: list[dict], channel: str,
                    payment_token: str, reco_skus: set[str] | None = None) -> dict:
        if idem_key in self._idem:
            return self._idem[idem_key]
        with metrics.timer("checkout"):
            result = self._place(customer, lines, channel, payment_token, reco_skus or set())
        self._idem[idem_key] = result
        return result

    def _place(self, customer, lines, channel, payment_token, reco_skus) -> dict:
        order_id = "VO-" + uuid.uuid4().hex[:8].upper()
        cart_value = sum(pricing.book.base.get(line["sku"], 0) * line["qty"] for line in lines)
        priced, reservations = [], []
        try:
            for i, line in enumerate(lines):
                q = pricing.quote(line["sku"], channel, customer.get("loyalty_tier", "none"), cart_value)
                r = inventory.reserve(f"{order_id}-{i}", line["sku"], line["qty"])
                reservations.append(r.res_id)
                priced.append({**line, "unit_price": q["final_price"], "offers": q["applied_offers"],
                               "location": r.location})
        except OversellError as exc:
            for rid in reservations:
                inventory.release(rid)
            return {"status": "rejected", "reason": str(exc), "order_id": None}

        total = round(sum(p["unit_price"] * p["qty"] for p in priced), 2)
        if not payment_breaker.allow():
            for rid in reservations:
                inventory.release(rid)
            return {"status": "retry_later", "reason": "payment provider degraded", "order_id": None}
        pay = fake_payment_gateway(total, payment_token)
        payment_breaker.record(pay["status"] == "captured")
        if pay["status"] != "captured":
            for rid in reservations:
                inventory.release(rid)
            return {"status": "payment_failed", "order_id": None}

        for rid in reservations:
            inventory.confirm(rid)
        order = {"order_id": order_id, "status": "confirmed", "total_inr": total, "lines": priced,
                 "customer_id": customer.get("customer_id"), "channel": channel,
                 "payment_id": pay["payment_id"], "price_book_version": pricing.book.version}
        self.orders[order_id] = order
        # async side-effects: courier, SMS/email, ERP, WMS pick
        bus.publish(Event(TOPIC_ORDER, order, key=order_id))
        metrics.inc("orders_placed")
        metrics.inc("revenue_inr", total)
        metrics.inc("items_ordered", sum(p["qty"] for p in priced))
        metrics.inc("reco_items_ordered", sum(p["qty"] for p in priced if p["sku"] in reco_skus))
        metrics.inc("fulfilment_cost_inr", 85.0)
        log("order.confirmed", order_id=order_id, total=total)
        return order


def abandonment_risk(cart: dict) -> dict:
    """Logistic scoring of a live cart (weights learned offline; shown here as constants).

    Features chosen from Vastra's friction list: slow pages (Core Web Vitals),
    API errors, price changed in cart, item went out of stock, guest checkout,
    shipping cost shock.
    """
    import math
    w = {"bias": -2.0, "lcp_s": 0.35, "api_errors": 0.6, "price_changed": 1.2,
         "item_oos": 1.5, "guest": 0.4, "shipping_shock": 0.9, "minutes_idle": 0.05}
    z = w["bias"] + sum(w[k] * float(cart.get(k, 0)) for k in w if k != "bias")
    p = 1 / (1 + math.exp(-z))
    action = "none"
    if p > 0.6:
        action = "whatsapp_nudge_with_saved_cart" if cart.get("marketing_consent") else "onsite_banner"
    elif p > 0.35:
        action = "onsite_free_shipping_threshold_hint"
    return {"risk": round(p, 3), "action": action}


checkout = CheckoutService()
