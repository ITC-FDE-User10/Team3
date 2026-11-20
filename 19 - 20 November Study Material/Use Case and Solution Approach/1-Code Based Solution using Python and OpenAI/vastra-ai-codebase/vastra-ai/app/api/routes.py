"""REST API v1 (FastAPI, OpenAPI at /docs). Versioned, idempotent writes, typed I/O."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.agents.agent import supervisor
from app.agents.tools import approval_queue
from app.ai.llm import GuardrailViolation
from app.ai.rag import policy_rag
from app.core.observability import metrics
from app.core.security import audit, require_api_key
from app.services import customer360, data_quality, dispatch, forecast, invoice
from app.services.checkout import abandonment_risk, checkout
from app.services.inventory import inventory
from app.services.pricing import pricing
from app.services.search import recommender, search_index

router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])


# ---------- Pillar 1: Sales & revenue -------------------------------------
@router.get("/search", tags=["P1 Sales"])
def search(q: str, k: int = 10, max_price: float | None = None):
    metrics.inc("sessions")
    with metrics.timer("search"):
        return search_index.search(q, k=k, atp=inventory.atp, max_price=max_price)


@router.get("/recommendations/{sku}", tags=["P1 Sales"])
def recommendations(sku: str, k: int = 4):
    recs = recommender.recommend(sku, k=k * 2)
    return [r for r in recs if inventory.atp(r["sku"]) > 0][:k]


@router.get("/price/{sku}", tags=["P1 Sales"])
def price(sku: str, channel: str = "web", loyalty_tier: str = "none", cart_value: float = 0):
    try:
        return pricing.quote(sku, channel, loyalty_tier, cart_value)
    except KeyError:
        raise HTTPException(404, "unknown sku")


class CustomerRecord(BaseModel):
    source: str
    name: str = ""
    phone: str | None = None
    email: str | None = None
    pincode: str | None = None
    loyalty_points: int = 0
    loyalty_tier: str | None = None
    marketing_consent: bool = False
    updated_at: str = ""


@router.post("/customers/resolve", tags=["P1 Sales"])
def resolve_customers(records: list[CustomerRecord]):
    return customer360.resolve([r.model_dump() for r in records])


# ---------- Pillar 2: User friction ---------------------------------------
class Line(BaseModel):
    sku: str
    qty: int = Field(gt=0, le=10)


class CheckoutRequest(BaseModel):
    customer: dict = {}
    lines: list[Line]
    channel: str = "web"
    payment_token: str
    reco_skus: list[str] = []


@router.post("/checkout", tags=["P2 Friction"])
def place_order(req: CheckoutRequest, idempotency_key: str = Header(...)):
    metrics.inc("carts_created")
    return checkout.place_order(idempotency_key, req.customer, [l.model_dump() for l in req.lines],
                                req.channel, req.payment_token, set(req.reco_skus))


@router.post("/carts/risk", tags=["P2 Friction"])
def cart_risk(cart: dict):
    return abandonment_risk(cart)


# ---------- Pillar 3: Logistics & backend ---------------------------------
@router.get("/stock/{sku}", tags=["P3 Logistics"])
def stock(sku: str):
    return {"sku": sku, "atp": inventory.atp(sku), "locations": inventory.snapshot(sku)}


@router.get("/reorder/{sku}", tags=["P3 Logistics"])
def reorder(sku: str, lead_time_days: int = 7, festival_uplift: float = 1.0):
    hist = inventory.sales_history.get(sku, [])
    daily: dict[int, float] = {}
    for ts, q in hist:
        daily[int(ts // 86400)] = daily.get(int(ts // 86400), 0) + q
    series = [daily[d] for d in sorted(daily)]
    on_hand = sum(p["on_hand"] for p in inventory.snapshot(sku))
    s = forecast.suggest_reorder(sku, series, on_hand, lead_time_days=lead_time_days, festival_uplift=festival_uplift)
    if s.suggested_qty:
        metrics.inc("reorders_suggested")
    return s.__dict__


@router.post("/dispatch/wave", tags=["P3 Logistics"])
def pick_wave(orders: list[dict], wave_size: int = 50):
    return dispatch.build_pick_wave(orders, wave_size)


# ---------- Pillar 4: Cross-cutting ---------------------------------------
@router.post("/catalogue/ingest", tags=["P4 Cross-cutting"])
def ingest(records: list[dict]):
    return data_quality.process_batch(records)


class InvoiceIn(BaseModel):
    ocr_text: str
    po: dict
    grn: dict


@router.post("/invoices/match", tags=["P4 Cross-cutting"])
def match_invoice(body: InvoiceIn):
    inv = invoice.extract_invoice(body.ocr_text)
    return {"extracted": inv, **invoice.three_way_match(body.po, body.grn, inv)}


@router.get("/policy", tags=["P4 Cross-cutting"])
def policy(q: str, role: str = "all"):
    try:
        return policy_rag.answer(q, role)
    except GuardrailViolation as exc:
        raise HTTPException(400, str(exc))


class AgentRequest(BaseModel):
    message: str
    scopes: list[str] = ["ops.read"]


@router.post("/agent", tags=["Agents"])
def agent(req: AgentRequest):
    t = supervisor.handle(req.message, set(req.scopes))
    return {"agent": t.agent, "answer": t.answer, "trajectory": t.steps}


@router.post("/approvals/{approval_id}", tags=["Agents"])
def approve(approval_id: str, decision: str, approver: str):
    item = approval_queue.get(approval_id)
    if not item:
        raise HTTPException(404, "not found")
    item["status"] = "approved" if decision == "approve" else "rejected"
    audit(approver, "po.decision", approval_id=approval_id, decision=item["status"])
    return item


@router.get("/kpis", tags=["Observability"])
def kpis():
    return metrics.kpis()
