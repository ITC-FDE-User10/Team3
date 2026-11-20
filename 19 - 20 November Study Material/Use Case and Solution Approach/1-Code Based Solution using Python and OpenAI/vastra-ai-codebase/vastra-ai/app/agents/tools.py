"""Tool registry shared by the in-process agents and the MCP server.

Each tool declares: JSON schema, risk tier, and required scope. High-risk tools
(writes that move money or stock) go through human-in-the-loop approval.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.ai.rag import policy_rag
from app.core.config import settings
from app.core.security import audit
from app.services import forecast
from app.services.inventory import inventory
from app.services.pricing import pricing


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    fn: Callable[..., Any]
    risk: str = "read"          # read | write | high
    scope: str = "ops.read"


approval_queue: dict[str, dict] = {}


def _check_stock(sku: str) -> dict:
    return {"sku": sku, "atp": inventory.atp(sku), "locations": inventory.snapshot(sku)}


def _quote_price(sku: str, channel: str = "web", loyalty_tier: str = "none") -> dict:
    return pricing.quote(sku, channel, loyalty_tier)


def _reorder_suggestion(sku: str, lead_time_days: int = 7) -> dict:
    hist = inventory.sales_history.get(sku, [])
    daily: dict[int, float] = {}
    for ts, q in hist:
        daily[int(ts // 86400)] = daily.get(int(ts // 86400), 0) + q
    series = [daily[d] for d in sorted(daily)] or [0.0]
    on_hand = sum(p["on_hand"] for p in inventory.snapshot(sku))
    return forecast.suggest_reorder(sku, series, on_hand, lead_time_days=lead_time_days).__dict__


def _raise_purchase_order(sku: str, qty: int, unit_cost: float, requested_by: str = "agent") -> dict:
    value = qty * unit_cost
    if value > settings.hitl_threshold_inr or not settings.feature_flags["agent_auto_reorder"]:
        req_id = "APR-" + uuid.uuid4().hex[:6].upper()
        approval_queue[req_id] = {"sku": sku, "qty": qty, "unit_cost": unit_cost, "value": value,
                                  "requested_by": requested_by, "status": "pending"}
        audit(requested_by, "po.requested", req_id=req_id, value=value)
        return {"status": "pending_human_approval", "approval_id": req_id, "value_inr": value}
    audit(requested_by, "po.raised", sku=sku, qty=qty)
    return {"status": "po_raised", "po_number": "PO-" + uuid.uuid4().hex[:6].upper(), "value_inr": value}


def _policy_lookup(question: str, role: str = "all") -> dict:
    return policy_rag.answer(question, role)


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("check_stock", "Real-time available-to-promise stock for a SKU across DC and stores",
         {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}, _check_stock),
    Tool("quote_price", "Price and applied offers for a SKU on a channel from the single price book",
         {"type": "object", "properties": {"sku": {"type": "string"}, "channel": {"type": "string"},
                                           "loyalty_tier": {"type": "string"}}, "required": ["sku"]}, _quote_price),
    Tool("reorder_suggestion", "Forecast-based reorder suggestion for a SKU",
         {"type": "object", "properties": {"sku": {"type": "string"}, "lead_time_days": {"type": "integer"}},
          "required": ["sku"]}, _reorder_suggestion),
    Tool("raise_purchase_order", "Raise a purchase order (requires approval above threshold)",
         {"type": "object", "properties": {"sku": {"type": "string"}, "qty": {"type": "integer"},
                                           "unit_cost": {"type": "number"}}, "required": ["sku", "qty", "unit_cost"]},
         _raise_purchase_order, risk="high", scope="ops.write"),
    Tool("policy_lookup", "Answer a question from approved Vastra SOPs with citations",
         {"type": "object", "properties": {"question": {"type": "string"}, "role": {"type": "string"}},
          "required": ["question"]}, _policy_lookup),
]}


def call_tool(name: str, args: dict, scopes: set[str], actor: str = "agent") -> Any:
    tool = TOOLS.get(name)
    if not tool:
        raise KeyError(f"unknown tool {name}")
    if tool.scope not in scopes:
        raise PermissionError(f"scope {tool.scope} required for {name}")
    missing = [r for r in tool.schema.get("required", []) if r not in args]
    if missing:
        raise ValueError(f"missing args {missing}")
    audit(actor, "tool.call", tool=name, args=args)
    return tool.fn(**args)
