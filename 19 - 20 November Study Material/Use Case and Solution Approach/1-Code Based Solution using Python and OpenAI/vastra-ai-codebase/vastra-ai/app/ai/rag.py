"""Policy RAG for contact centre and store staff (SOPs - Pillar 4).

Agents in the contact centre today read POS reports and call stores. This RAG answers
'what is the return window for sale items?' from approved SOP documents only, with
citations, and refuses when the answer is not in the corpus (groundedness).

Permission-aware: each chunk has an audience tag; a store associate never retrieves
finance-only SOPs.
"""
from __future__ import annotations

from app.ai.llm import llm
from app.services.search import HybridSearch

SOPS = [
    {"sku": "SOP-RET-01", "title": "Returns policy", "audience": "all",
     "description": "Unworn items with tags can be returned within 15 days online or in any store. "
                    "Sale items are exchange only within 7 days. Customised blouses are non-returnable."},
    {"sku": "SOP-PRC-02", "title": "Price match", "audience": "all",
     "description": "All channels honour the single Vastra price book. If a customer sees a lower Vastra "
                    "price on another channel the same day, staff must match it and log the case."},
    {"sku": "SOP-DSP-03", "title": "Dispatch SLA", "audience": "all",
     "description": "Standard orders dispatch within 24 hours; express within 4 hours if placed before 2 pm."},
    {"sku": "SOP-FIN-04", "title": "Supplier payment terms", "audience": "finance",
     "description": "Suppliers are paid 45 days after a clean three-way match. Exceptions need AP manager approval."},
]


class PolicyRAG:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = {d["sku"]: d for d in docs}
        self.index = HybridSearch()
        self.index.index([{**d, "category": d["audience"]} for d in docs])

    def answer(self, question: str, role: str = "all", k: int = 2) -> dict:
        hits = self.index.search(question, k=len(self.docs))
        allowed = [h for h in hits if self.docs[h["sku"]]["audience"] in ("all", role)][:k]
        if not allowed or allowed[0]["score"] < 0.02:
            return {"answer": "I can't find this in approved Vastra SOPs; escalating to a supervisor.",
                    "citations": [], "grounded": False}
        context = "\n".join(self.docs[h["sku"]]["description"] for h in allowed)
        text = llm.complete(
            "You are Vastra's policy assistant. Answer using only the context. If not in context say you don't know.",
            f"QUESTION: {question}\nCONTEXT:\n{context}")
        return {"answer": text, "citations": [h["sku"] for h in allowed], "grounded": True}


policy_rag = PolicyRAG(SOPS)
