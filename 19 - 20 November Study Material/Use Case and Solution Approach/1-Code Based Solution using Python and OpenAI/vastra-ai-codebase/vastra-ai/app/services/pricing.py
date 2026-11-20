"""Pillar 1 / R4 - Consistent offers across channels.

Today Vastra has TWO price copies (monolith 'Price copy (1)' and store POS 'own price
copy (2)'), refreshed nightly from ERP, so web, app, store and contact centre quote
different prices. This service is the single Price & Offer Authority:

* One price book keyed by SKU, versioned, published as events to every channel.
* Offers are a declarative decision table (DMN-style, 'first-hit' + stacking rules),
  evaluated identically for web, app, POS and contact centre.
* Every quote returns an explanation and the price-book version, so a disputed
  price can be traced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from app.core.events import TOPIC_PRICE, Event, bus


@dataclass
class OfferRule:
    rule_id: str
    description: str
    priority: int                       # lower = evaluated first
    pct_off: float = 0.0
    flat_off: float = 0.0
    category: str | None = None
    min_cart_value: float = 0.0
    loyalty_tiers: tuple[str, ...] = ()
    channels: tuple[str, ...] = ("web", "app", "pos", "contact_centre")
    valid_from: date | None = None
    valid_to: date | None = None
    stackable: bool = False
    max_discount_pct: float = 40.0      # guardrail on margin

    def matches(self, ctx: dict) -> bool:
        today = ctx.get("today", date.today())
        if self.valid_from and today < self.valid_from:
            return False
        if self.valid_to and today > self.valid_to:
            return False
        if self.category and ctx.get("category") != self.category:
            return False
        if self.loyalty_tiers and ctx.get("loyalty_tier") not in self.loyalty_tiers:
            return False
        if ctx.get("channel") not in self.channels:
            return False
        return ctx.get("cart_value", 0.0) >= self.min_cart_value


@dataclass
class PriceBook:
    version: int = 0
    base: dict[str, float] = field(default_factory=dict)
    category: dict[str, str] = field(default_factory=dict)


class PricingService:
    def __init__(self) -> None:
        self.book = PriceBook()
        self.rules: list[OfferRule] = []

    def upsert_price(self, sku: str, price: float, category: str) -> int:
        if price <= 0:
            raise ValueError("price must be positive")
        self.book.base[sku] = round(price, 2)
        self.book.category[sku] = category
        self.book.version += 1
        bus.publish(Event(TOPIC_PRICE, {"sku": sku, "price": price, "version": self.book.version}, key=sku))
        return self.book.version

    def add_rule(self, rule: OfferRule) -> None:
        self.rules.append(rule)
        self.rules.sort(key=lambda r: r.priority)

    def quote(self, sku: str, channel: str, loyalty_tier: str = "none",
              cart_value: float = 0.0, today: date | None = None) -> dict:
        if sku not in self.book.base:
            raise KeyError(sku)
        base = self.book.base[sku]
        ctx = {"channel": channel, "loyalty_tier": loyalty_tier, "cart_value": cart_value,
               "category": self.book.category.get(sku), "today": today or date.today()}
        applied, discount = [], 0.0
        for rule in self.rules:
            if not rule.matches(ctx):
                continue
            d = base * rule.pct_off / 100 + rule.flat_off
            cap = base * rule.max_discount_pct / 100
            d = min(d, cap - discount)
            if d <= 0:
                continue
            discount += d
            applied.append({"rule_id": rule.rule_id, "description": rule.description, "amount": round(d, 2)})
            if not rule.stackable:
                break  # first-hit policy for non-stackable rules
        return {
            "sku": sku, "channel": channel, "base_price": base,
            "discount": round(discount, 2), "final_price": round(base - discount, 2),
            "applied_offers": applied, "price_book_version": self.book.version,
        }


pricing = PricingService()
