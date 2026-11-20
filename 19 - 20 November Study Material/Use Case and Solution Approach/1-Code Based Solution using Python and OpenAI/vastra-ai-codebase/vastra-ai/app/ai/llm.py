"""LLM gateway: one place for provider routing, timeouts, PII redaction, prompt-
injection screening, structured output validation, caching and cost tracking.

Provider 'stub' is deterministic and offline (used by tests and evals). Set
VASTRA_LLM_PROVIDER=anthropic and VASTRA_LLM_API_KEY to call a real model.
"""
from __future__ import annotations

import hashlib
import json
import re

from app.core.config import settings
from app.core.observability import log, metrics
from app.core.security import redact_pii

INJECTION_PATTERNS = [
    r"ignore (all|any|previous|prior) (instructions|rules)",
    r"you are now", r"system prompt", r"disregard .* (policy|instructions)",
    r"reveal .* (key|password|secret)", r"act as (admin|root)",
]


class GuardrailViolation(Exception):
    pass


def screen_input(text: str) -> str:
    for pat in INJECTION_PATTERNS:
        if re.search(pat, text, re.I):
            metrics.inc("guardrail_blocks")
            raise GuardrailViolation("possible prompt injection")
    return redact_pii(text)


class LLMGateway:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self.calls = 0

    def complete(self, system: str, user: str, json_schema_keys: list[str] | None = None,
                 untrusted: bool = True) -> str | dict:
        user = screen_input(user) if untrusted else redact_pii(user)
        key = hashlib.sha256(f"{system}|{user}".encode()).hexdigest()
        if key in self._cache:
            metrics.inc("llm_cache_hits")
            raw = self._cache[key]
        else:
            self.calls += 1
            with metrics.timer("llm"):
                raw = self._call(system, user)
            self._cache[key] = raw
        if json_schema_keys is None:
            return raw
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned non-JSON") from exc
        missing = [k for k in json_schema_keys if k not in data]
        if missing:
            raise ValueError(f"LLM output missing keys {missing}")
        return data

    def _call(self, system: str, user: str) -> str:
        if settings.llm_provider == "anthropic" and settings.llm_api_key:
            import anthropic  # optional dependency
            client = anthropic.Anthropic(api_key=settings.llm_api_key, timeout=settings.llm_timeout_s)
            msg = client.messages.create(model=settings.llm_model, max_tokens=800, system=system,
                                         messages=[{"role": "user", "content": user}])
            log("llm.call", model=settings.llm_model, in_tokens=msg.usage.input_tokens,
                out_tokens=msg.usage.output_tokens)
            return msg.content[0].text
        return _stub(system, user)


def _stub(system: str, user: str) -> str:
    """Deterministic offline behaviour so pipelines, tests and evals run without keys."""
    s = system.lower()
    if "product attribute" in s:
        txt = user.lower()
        colours = ["red", "maroon", "blue", "green", "yellow", "pink", "black", "white", "gold", "orange"]
        fabrics = ["silk", "cotton", "georgette", "chiffon", "linen", "rayon", "velvet", "banarasi"]
        cats = ["saree", "kurta", "lehenga", "dupatta", "sherwani", "kurti"]
        return json.dumps({
            "colour": next((c for c in colours if c in txt), None),
            "fabric": next((f for f in fabrics if f in txt), None),
            "category": next((c for c in cats if c in txt), None),
        })
    if "invoice" in s:
        m = re.findall(r"([A-Z]{2}-\d{3,})\s+(\d+)\s+x\s+([\d.]+)", user)
        po = re.search(r"PO[-\s#:]*(\d+)", user)
        inv = re.search(r"INV[-\s#:]*([\w-]+)", user)
        gst = re.search(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]\b", user)
        return json.dumps({
            "invoice_no": inv.group(1) if inv else None,
            "po_number": po.group(1) if po else None,
            "supplier_gstin": gst.group(0) if gst else None,
            "lines": [{"sku": a, "qty": int(b), "unit_price": float(c)} for a, b, c in m],
        })
    if "answer using only the context" in s:
        ctx = user.split("CONTEXT:")[-1].strip().split("\n")[0]
        return f"Based on Vastra policy: {ctx[:300]}"
    return "OK"


llm = LLMGateway()
