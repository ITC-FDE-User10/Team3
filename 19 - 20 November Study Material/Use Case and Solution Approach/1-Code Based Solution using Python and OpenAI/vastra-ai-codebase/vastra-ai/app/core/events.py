"""Event bus abstraction.

Replaces Vastra's batch/file transfers (stock every 4h, prices nightly, sales every 4h,
orders next day) with near-real-time events. In production this is Kafka / Azure Event
Hubs / AWS Kinesis fed by CDC (Debezium) from the monolith MySQL and the ERP/WMS,
using the transactional-outbox pattern. Locally it is an in-memory pub/sub so the
whole solution runs and is testable on a laptop.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

Handler = Callable[["Event"], None]


@dataclass
class Event:
    topic: str
    payload: dict[str, Any]
    key: str = ""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: float = field(default_factory=time.time)
    schema_version: int = 1


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._seen: set[str] = set()          # idempotent consumers
        self.dead_letter: list[tuple[Event, str]] = []
        self.log: list[Event] = []            # replayable log (like a Kafka topic)

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    def publish(self, event: Event, max_retries: int = 3) -> None:
        if event.event_id in self._seen:
            return  # duplicate delivery -> ignore (at-least-once safe)
        self._seen.add(event.event_id)
        self.log.append(event)
        for handler in self._subs.get(event.topic, []):
            for attempt in range(max_retries):
                try:
                    handler(event)
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == max_retries - 1:
                        self.dead_letter.append((event, repr(exc)))


bus = EventBus()

# Topic catalogue (data contracts live in docs/data-contracts.md)
TOPIC_STOCK = "inventory.stock.changed.v1"
TOPIC_SALE = "pos.sale.recorded.v1"
TOPIC_ORDER = "commerce.order.placed.v1"
TOPIC_PRICE = "pricing.price.changed.v1"
TOPIC_CART = "commerce.cart.updated.v1"
TOPIC_GRN = "warehouse.goods.received.v1"
