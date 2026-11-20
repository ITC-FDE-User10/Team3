"""Multi-agent Ops Concierge (supervisor + specialists).

* Supervisor routes a request to a specialist (rules first; LLM classification when a
  real provider is configured).
* InventoryAgent: stock, stock-outs, reorder (R5, R6, R7).
* CommerceAgent: prices and offers for contact centre (R4, R9).
* PolicyAgent: SOP questions via permission-aware RAG.

Safeguards: step budget, tool allow-list per agent, scope check, HITL on high-risk
tools, full trajectory recorded for trajectory evaluation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agents.tools import TOOLS, call_tool
from app.ai.llm import GuardrailViolation, screen_input

SKU_RE = re.compile(r"\b[A-Z]{2}-\d{3,}\b")


@dataclass
class Trajectory:
    request: str
    agent: str = ""
    steps: list[dict] = field(default_factory=list)
    answer: str = ""


class Specialist:
    name = "base"
    allowed: tuple[str, ...] = ()
    max_steps = 4

    def plan(self, request: str) -> list[tuple[str, dict]]:
        raise NotImplementedError

    def run(self, request: str, scopes: set[str], traj: Trajectory) -> None:
        for i, (tool, args) in enumerate(self.plan(request)):
            if i >= self.max_steps:
                traj.steps.append({"halt": "step budget exceeded"})
                break
            if tool not in self.allowed:
                traj.steps.append({"blocked": tool})
                continue
            try:
                out = call_tool(tool, args, scopes, actor=self.name)
            except (PermissionError, ValueError, KeyError) as exc:
                out = {"error": str(exc)}
            traj.steps.append({"tool": tool, "args": args, "result": out})


class InventoryAgent(Specialist):
    name = "inventory_agent"
    allowed = ("check_stock", "reorder_suggestion", "raise_purchase_order")

    def plan(self, request):
        skus = SKU_RE.findall(request)
        steps = []
        for sku in skus:
            steps.append(("check_stock", {"sku": sku}))
            if re.search(r"reorder|run out|stock-?out|replenish", request, re.I):
                steps.append(("reorder_suggestion", {"sku": sku}))
            m = re.search(r"order (\d+)", request, re.I)
            if m:
                steps.append(("raise_purchase_order", {"sku": sku, "qty": int(m.group(1)), "unit_cost": 1200.0}))
        return steps


class CommerceAgent(Specialist):
    name = "commerce_agent"
    allowed = ("quote_price", "check_stock")

    def plan(self, request):
        ch = "pos" if "store" in request.lower() else "contact_centre"
        tier = "gold" if "gold" in request.lower() else "none"
        return [("quote_price", {"sku": s, "channel": ch, "loyalty_tier": tier}) for s in SKU_RE.findall(request)]


class PolicyAgent(Specialist):
    name = "policy_agent"
    allowed = ("policy_lookup",)

    def plan(self, request):
        return [("policy_lookup", {"question": request})]


class Supervisor:
    def __init__(self) -> None:
        self.agents = {a.name: a for a in (InventoryAgent(), CommerceAgent(), PolicyAgent())}

    def route(self, request: str) -> str:
        r = request.lower()
        if re.search(r"stock|reorder|replenish|run out|purchase order|\border \d+", r):
            return "inventory_agent"
        if re.search(r"price|offer|discount|cost", r) and SKU_RE.search(request):
            return "commerce_agent"
        return "policy_agent"

    def handle(self, request: str, scopes: set[str]) -> Trajectory:
        traj = Trajectory(request=request)
        try:
            screen_input(request)
        except GuardrailViolation as exc:
            traj.answer = f"Request blocked by guardrail: {exc}"
            return traj
        agent = self.agents[self.route(request)]
        traj.agent = agent.name
        agent.run(request, scopes, traj)
        traj.answer = summarise(traj)
        return traj


def summarise(traj: Trajectory) -> str:
    parts = []
    for s in traj.steps:
        r = s.get("result", {})
        if "error" in r:
            parts.append(f"{s['tool']} failed: {r['error']}")
        elif s.get("tool") == "check_stock":
            parts.append(f"{r['sku']}: {r['atp']} units available to promise")
        elif s.get("tool") == "reorder_suggestion":
            parts.append(f"{r['sku']}: {r['urgency']} - suggest ordering {r['suggested_qty']} ({r['rationale']})")
        elif s.get("tool") == "raise_purchase_order":
            parts.append(f"PO: {r['status']}" + (f" ({r['approval_id']})" if "approval_id" in r else ""))
        elif s.get("tool") == "quote_price":
            parts.append(f"{r['sku']} on {r['channel']}: INR {r['final_price']} (base {r['base_price']})")
        elif s.get("tool") == "policy_lookup":
            parts.append(r["answer"] + (f" [sources: {', '.join(r['citations'])}]" if r["citations"] else ""))
    return " | ".join(parts) or "No action taken."


supervisor = Supervisor()
__all__ = ["supervisor", "TOOLS"]
