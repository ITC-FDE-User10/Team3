"""Pillar 4 / R17 - Manual goods receipt and invoicing.

Today the warehouse keys in goods receipts (GRN) and accounts re-key supplier
invoices into ERP; phone and email transfers (W6) fill the gaps.

Solution: document AI + three-way match.
1. OCR (production: Azure Document Intelligence / AWS Textract) -> text.
2. LLM extracts a structured invoice (JSON schema, validated).
3. Deterministic three-way match: Purchase Order <-> GRN <-> Invoice with tolerances.
4. Clean matches auto-post to ERP; exceptions go to an AP clerk with the reason.
"""
from __future__ import annotations

import re

from app.ai.llm import llm
from app.core.observability import metrics

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]$")
PRICE_TOL = 0.02   # 2%
QTY_TOL = 0        # exact


def extract_invoice(ocr_text: str) -> dict:
    data = llm.complete(
        "You extract supplier invoice fields. Return JSON with keys invoice_no, po_number, "
        "supplier_gstin, lines[{sku, qty, unit_price}].",
        ocr_text, json_schema_keys=["invoice_no", "po_number", "supplier_gstin", "lines"], untrusted=False)
    return data


def three_way_match(po: dict, grn: dict, invoice: dict) -> dict:
    issues = []
    if invoice.get("po_number") != po["po_number"]:
        issues.append("po_number_mismatch")
    if not invoice.get("supplier_gstin") or not GSTIN_RE.match(invoice["supplier_gstin"]):
        issues.append("invalid_gstin")
    po_lines = {l["sku"]: l for l in po["lines"]}
    grn_qty = {l["sku"]: l["qty"] for l in grn["lines"]}
    for line in invoice.get("lines", []):
        sku = line["sku"]
        if sku not in po_lines:
            issues.append(f"{sku}:not_on_po")
            continue
        if abs(line["qty"] - grn_qty.get(sku, 0)) > QTY_TOL:
            issues.append(f"{sku}:qty_invoice_{line['qty']}_vs_received_{grn_qty.get(sku, 0)}")
        po_price = po_lines[sku]["unit_price"]
        if abs(line["unit_price"] - po_price) / po_price > PRICE_TOL:
            issues.append(f"{sku}:price_{line['unit_price']}_vs_po_{po_price}")
    status = "auto_post_to_erp" if not issues else "exception_queue"
    metrics.inc("invoice_matched" if not issues else "invoice_exceptions")
    total = round(sum(l["qty"] * l["unit_price"] for l in invoice.get("lines", [])), 2)
    return {"status": status, "issues": issues, "invoice_total": total}
