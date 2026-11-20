"""Synthetic Vastra data so the platform runs end-to-end on a laptop."""
from __future__ import annotations

import random
import time
from datetime import date

from app.core.events import TOPIC_SALE, TOPIC_STOCK, Event, bus
from app.services.inventory import inventory
from app.services.pricing import OfferRule, pricing
from app.services.search import recommender, search_index

PRODUCTS = [
    {"sku": "SR-101", "title": "Red Banarasi Silk Saree", "category": "saree", "colour": "red", "fabric": "silk",
     "occasion": "wedding bridal", "price": 12999, "description": "Handwoven zari border"},
    {"sku": "SR-102", "title": "Yellow Cotton Saree", "category": "saree", "colour": "yellow", "fabric": "cotton",
     "occasion": "haldi daily", "price": 2499, "description": "Light block print"},
    {"sku": "SR-103", "title": "Maroon Georgette Party Saree", "category": "saree", "colour": "maroon",
     "fabric": "georgette", "occasion": "party festive", "price": 5499, "description": "Sequin work"},
    {"sku": "KT-201", "title": "Men's Off-white Linen Kurta", "category": "kurta", "colour": "white", "fabric": "linen",
     "occasion": "festive diwali", "price": 1899, "description": "Mandarin collar"},
    {"sku": "KT-202", "title": "Women's Pink Rayon Kurti", "category": "kurti", "colour": "pink", "fabric": "rayon",
     "occasion": "office daily", "price": 999, "description": "A-line"},
    {"sku": "LH-301", "title": "Bridal Red Velvet Lehenga", "category": "lehenga", "colour": "red", "fabric": "velvet",
     "occasion": "wedding bridal", "price": 45999, "description": "Heavy embroidery with dupatta"},
    {"sku": "DP-401", "title": "Gold Zari Dupatta", "category": "dupatta", "colour": "gold", "fabric": "silk",
     "occasion": "wedding festive", "price": 1499, "description": "Pairs with lehenga or kurta"},
    {"sku": "SH-501", "title": "Ivory Wedding Sherwani", "category": "sherwani", "colour": "white", "fabric": "silk",
     "occasion": "wedding groom", "price": 24999, "description": "Brocade"},
]
LOCATIONS = ["DC-BLR", "STORE-CHN-01", "STORE-MUM-07"]


def seed(n_days: int = 28, rng_seed: int = 7) -> None:
    rng = random.Random(rng_seed)
    for p in PRODUCTS:
        pricing.upsert_price(p["sku"], p["price"], p["category"])
        for loc in LOCATIONS:
            bus.publish(Event(TOPIC_STOCK, {"sku": p["sku"], "location": loc, "on_hand": rng.randint(5, 40)}))
    pricing.rules.clear()
    pricing.add_rule(OfferRule("FEST10", "Festive 10% on sarees", 10, pct_off=10, category="saree",
                               valid_from=date(2026, 10, 1), valid_to=date(2026, 12, 31)))
    pricing.add_rule(OfferRule("GOLD5", "Gold loyalty extra 5%", 20, pct_off=5, loyalty_tiers=("gold",), stackable=True))
    pricing.add_rule(OfferRule("BIG500", "INR 500 off above INR 10,000", 30, flat_off=500, min_cart_value=10000))
    search_index.index(PRODUCTS)
    # sales history -> forecasting
    now = time.time()
    for d in range(n_days, 0, -1):
        for p in PRODUCTS:
            base = 3 if p["price"] < 5000 else 1
            qty = max(0, int(rng.gauss(base + (n_days - d) * 0.05, 1)))
            if qty:
                inventory.sales_history.setdefault(p["sku"], []).append((now - d * 86400, qty))
    baskets = [["SR-101", "DP-401"], ["LH-301", "DP-401"], ["SH-501", "KT-201"], ["SR-101", "DP-401", "LH-301"],
               ["KT-202", "SR-102"], ["SR-103", "DP-401"], ["LH-301", "DP-401", "SH-501"], ["KT-201", "DP-401"]]
    recommender.fit(baskets * 5)


def simulate_store_sale(sku: str, location: str, qty: int) -> None:
    bus.publish(Event(TOPIC_SALE, {"sku": sku, "location": location, "qty": qty}))
