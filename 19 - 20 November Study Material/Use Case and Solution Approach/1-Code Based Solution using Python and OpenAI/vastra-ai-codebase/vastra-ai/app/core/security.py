"""API security: API-key auth, per-key rate limiting, PII redaction, audit trail.

Replaces 'shared admin logins' on the monolith DB with per-service identities and
least-privilege scopes. In production: OAuth2/OIDC (Azure AD / Cognito) + Vault/KMS.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException

from app.core.config import settings

_windows: dict[str, deque] = defaultdict(deque)
audit_log: list[dict] = []


def require_api_key(x_api_key: str = Header(default="")) -> str:
    if x_api_key not in settings.api_keys:
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    now = time.time()
    win = _windows[x_api_key]
    while win and now - win[0] > 60:
        win.popleft()
    if len(win) >= settings.rate_limit_rpm:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    win.append(now)
    return x_api_key


_PII = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[EMAIL]"),
    (re.compile(r"(?<!\d)(?:\+91[\s-]?)?[6-9]\d{9}(?!\d)"), "[PHONE]"),
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[AADHAAR]"),
    (re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"), "[PAN]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[CARD]"),
]


def redact_pii(text: str) -> str:
    """DPDP Act 2023: never send raw personal data to an external LLM."""
    for pattern, token in _PII:
        text = pattern.sub(token, text)
    return text


def audit(actor: str, action: str, **details) -> None:
    audit_log.append({"ts": time.time(), "actor": actor, "action": action, **details})
