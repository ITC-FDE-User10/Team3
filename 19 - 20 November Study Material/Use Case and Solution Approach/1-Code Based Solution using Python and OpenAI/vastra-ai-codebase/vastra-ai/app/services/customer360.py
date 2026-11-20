"""Pillar 1 / R9 - Single customer profile.

CRM & loyalty keep 'separate customer records'; web, app, POS and contact centre each
create their own. This service performs identity resolution:

1. Deterministic match on normalised phone / email (hashed - DPDP-friendly).
2. Probabilistic match on name + pincode similarity (Jaro-Winkler-like ratio).
3. Union-find to merge into a golden record with survivorship rules
   (most recent non-null wins; loyalty points summed; consent = most restrictive).
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher


def _norm_phone(p: str | None) -> str | None:
    if not p:
        return None
    d = re.sub(r"\D", "", p)
    return d[-10:] if len(d) >= 10 else None


def _norm_email(e: str | None) -> str | None:
    return e.strip().lower() if e and "@" in e else None


def _h(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:16]


class _UF:
    def __init__(self, n: int) -> None:
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def resolve(records: list[dict], fuzzy_threshold: float = 0.88) -> list[dict]:
    n = len(records)
    uf = _UF(n)
    keys: dict[str, int] = {}
    for i, r in enumerate(records):
        for k in (_norm_phone(r.get("phone")), _norm_email(r.get("email"))):
            if k:
                hk = _h(k)
                if hk in keys:
                    uf.union(i, keys[hk])
                else:
                    keys[hk] = i
    for i in range(n):
        for j in range(i + 1, n):
            a, b = records[i], records[j]
            if a.get("pincode") and a.get("pincode") == b.get("pincode"):
                sim = SequenceMatcher(None, a.get("name", "").lower(), b.get("name", "").lower()).ratio()
                if sim >= fuzzy_threshold:
                    uf.union(i, j)
    groups: dict[int, list[dict]] = {}
    for i, r in enumerate(records):
        groups.setdefault(uf.find(i), []).append(r)
    return [_golden(g) for g in groups.values()]


def _golden(group: list[dict]) -> dict:
    g = sorted(group, key=lambda r: r.get("updated_at", ""), reverse=True)
    pick = lambda f: next((r[f] for r in g if r.get(f)), None)  # noqa: E731
    seed = _norm_phone(pick("phone")) or _norm_email(pick("email")) or pick("name") or "anon"
    return {
        "customer_id": "C-" + _h(seed),
        "name": pick("name"), "phone": pick("phone"), "email": pick("email"),
        "pincode": pick("pincode"),
        "loyalty_points": sum(int(r.get("loyalty_points", 0)) for r in group),
        "loyalty_tier": pick("loyalty_tier") or "none",
        "marketing_consent": all(r.get("marketing_consent", False) for r in group),
        "source_systems": sorted({r.get("source", "?") for r in group}),
        "merged_records": len(group),
    }
