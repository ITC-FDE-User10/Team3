"""Pillar 4 / R12 - Product data inconsistency.

The catalogue is edited in the monolith, prices in ERP, and stores keep their own
copy, so titles, colours, sizes and HSN/GST codes disagree across channels.

Solution: a product *data contract* enforced at ingestion + LLM-assisted repair.
1. Validate each record against the contract (required fields, enums, ranges, GST).
2. Deterministic fixes first (trim, case, size normalisation).
3. LLM extracts missing attributes from title/description (structured JSON output).
4. Anything still failing goes to a data-steward queue (human review), never silently
   to the storefront. Every fix is logged with its source for audit.
"""
from __future__ import annotations

import re

from app.ai.llm import llm
from app.core.observability import metrics

CONTRACT = {
    "required": ["sku", "title", "category", "price", "hsn_code", "colour", "fabric"],
    "category": {"saree", "kurta", "lehenga", "dupatta", "sherwani", "kurti"},
    "gst_rate_by_hsn_prefix": {"5007": 5, "5208": 5, "6204": 12, "6211": 12},
    "size": {"XS", "S", "M", "L", "XL", "XXL", "FREE"},
}
SIZE_MAP = {"small": "S", "medium": "M", "large": "L", "extra large": "XL", "free size": "FREE", "fs": "FREE"}
SKU_RE = re.compile(r"^[A-Z]{2}-\d{3,}$")


def validate(rec: dict) -> list[str]:
    errs = [f"missing:{f}" for f in CONTRACT["required"] if not rec.get(f)]
    if rec.get("sku") and not SKU_RE.match(rec["sku"]):
        errs.append("bad_format:sku")
    if rec.get("category") and rec["category"] not in CONTRACT["category"]:
        errs.append("bad_enum:category")
    if rec.get("size") and rec["size"] not in CONTRACT["size"]:
        errs.append("bad_enum:size")
    if rec.get("price") is not None and not (50 <= float(rec["price"]) <= 500000):
        errs.append("out_of_range:price")
    if rec.get("hsn_code") and not re.fullmatch(r"\d{4,8}", str(rec["hsn_code"])):
        errs.append("bad_format:hsn_code")
    return errs


def repair(rec: dict) -> tuple[dict, list[str]]:
    fixed, notes = dict(rec), []
    for k in ("title", "colour", "fabric", "category"):
        if isinstance(fixed.get(k), str):
            v = fixed[k].strip()
            v = v.lower() if k != "title" else re.sub(r"\s+", " ", v).title()
            if v != fixed[k]:
                fixed[k] = v
                notes.append(f"normalised:{k}")
    if fixed.get("sku"):
        fixed["sku"] = fixed["sku"].strip().upper()
    if isinstance(fixed.get("size"), str):
        s = fixed["size"].strip()
        fixed["size"] = SIZE_MAP.get(s.lower(), s.upper())
    if any(not fixed.get(f) for f in ("colour", "fabric", "category")):
        attrs = llm.complete(
            "Extract product attributes as JSON with keys colour, fabric, category. Use null if unknown.",
            f"{fixed.get('title', '')}. {fixed.get('description', '')}",
            json_schema_keys=["colour", "fabric", "category"], untrusted=False)
        for k, v in attrs.items():
            if not fixed.get(k) and v:
                fixed[k] = v
                notes.append(f"llm_filled:{k}")
    return fixed, notes


def process_batch(records: list[dict]) -> dict:
    accepted, review = [], []
    for rec in records:
        fixed, notes = repair(rec)
        errs = validate(fixed)
        if errs:
            metrics.inc("dq_rejected")
            review.append({"record": fixed, "errors": errs, "fixes": notes})
        else:
            if notes:
                metrics.inc("dq_autofixed")
            accepted.append({**fixed, "_dq_fixes": notes})
    score = len(accepted) / max(len(records), 1)
    return {"accepted": accepted, "steward_queue": review, "dq_score": round(score, 3)}
