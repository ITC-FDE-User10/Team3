"""Pillar 1 / R8 - Recommendations and search.

Today: monolith 'Keyword search' (SQL LIKE). Shoppers typing 'red silk saree for
wedding' or 'kurta for haldi' get nothing.

Solution: hybrid retrieval
* BM25 lexical index (exact SKUs, brand names, Hindi/English transliterations)
* Dense vectors (production: multilingual embedding model in a vector DB such as
  pgvector/OpenSearch; here a deterministic hashed char-n-gram embedding so it runs
  offline)
* Reciprocal Rank Fusion, then business re-rank: in-stock only (ATP > 0), margin,
  personalisation from the single customer profile (R9)
* LLM query understanding extracts filters (colour, occasion, price) - optional.

Recommendations: item-to-item co-purchase (lift) + 'complete the look' rules, which
lifts AOV (Pillar 1 KPI).
"""
from __future__ import annotations

import math
import re
import zlib
from collections import Counter, defaultdict

SYNONYMS = {
    "saree": ["sari"], "sari": ["saree"], "kurta": ["kurti"], "lehenga": ["lehnga", "ghagra"],
    "wedding": ["bridal", "shaadi", "festive"], "haldi": ["yellow", "festive"],
    "diwali": ["festive"], "dupatta": ["stole"],
}
_tok = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    toks = _tok.findall(text.lower())
    out = list(toks)
    for t in toks:
        out += SYNONYMS.get(t, [])
    return out


def embed(text: str, dim: int = 256) -> list[float]:
    vec = [0.0] * dim
    t = f"  {text.lower()}  "
    for i in range(len(t) - 2):
        vec[zlib.crc32(t[i:i + 3].encode()) % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class HybridSearch:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs: dict[str, dict] = {}
        self.tf: dict[str, Counter] = {}
        self.df: Counter = Counter()
        self.vecs: dict[str, list[float]] = {}
        self.avgdl = 0.0

    def index(self, products: list[dict]) -> None:
        for p in products:
            text = " ".join(str(p.get(f, "")) for f in ("title", "category", "colour", "fabric", "occasion", "description"))
            toks = tokenize(text)
            self.docs[p["sku"]] = p
            self.tf[p["sku"]] = Counter(toks)
            self.vecs[p["sku"]] = embed(text)
        self.df = Counter()
        for c in self.tf.values():
            self.df.update(c.keys())
        self.avgdl = sum(sum(c.values()) for c in self.tf.values()) / max(len(self.tf), 1)

    def _bm25(self, q: list[str]) -> dict[str, float]:
        N, scores = len(self.docs), defaultdict(float)
        for sku, tf in self.tf.items():
            dl = sum(tf.values())
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (N - self.df[t] + 0.5) / (self.df[t] + 0.5))
                scores[sku] += idf * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
        return scores

    def search(self, query: str, k: int = 10, atp=None, max_price: float | None = None,
               preferred_categories: list[str] | None = None) -> list[dict]:
        lex = self._bm25(tokenize(query))
        qv = embed(query)
        dense = {s: cosine(qv, v) for s, v in self.vecs.items()}
        rrf: dict[str, float] = defaultdict(float)
        for ranking in (sorted(lex, key=lex.get, reverse=True), sorted(dense, key=dense.get, reverse=True)):
            for rank, sku in enumerate(ranking[:100]):
                rrf[sku] += 1 / (60 + rank)
        results = []
        for sku, score in sorted(rrf.items(), key=lambda kv: kv[1], reverse=True):
            p = self.docs[sku]
            if max_price and p.get("price", 0) > max_price:
                continue
            if atp is not None and atp(sku) <= 0:
                continue  # never show what we can't sell (R5/R6)
            if preferred_categories and p.get("category") in preferred_categories:
                score *= 1.15
            results.append({"sku": sku, "title": p["title"], "price": p.get("price"), "score": round(score, 5)})
        return sorted(results, key=lambda r: r["score"], reverse=True)[:k]


class CoPurchaseRecommender:
    def __init__(self) -> None:
        self.pair: Counter = Counter()
        self.item: Counter = Counter()
        self.n_baskets = 0

    def fit(self, baskets: list[list[str]]) -> None:
        for b in baskets:
            items = set(b)
            self.n_baskets += 1
            self.item.update(items)
            for a in items:
                for c in items:
                    if a != c:
                        self.pair[(a, c)] += 1

    def recommend(self, sku: str, k: int = 5, exclude: set[str] | None = None) -> list[dict]:
        exclude = exclude or set()
        out = []
        for (a, c), n in self.pair.items():
            if a != sku or c in exclude:
                continue
            lift = (n / self.n_baskets) / ((self.item[a] / self.n_baskets) * (self.item[c] / self.n_baskets))
            out.append({"sku": c, "lift": round(lift, 2), "support": n})
        return sorted(out, key=lambda r: (r["lift"], r["support"]), reverse=True)[:k]


search_index = HybridSearch()
recommender = CoPurchaseRecommender()
