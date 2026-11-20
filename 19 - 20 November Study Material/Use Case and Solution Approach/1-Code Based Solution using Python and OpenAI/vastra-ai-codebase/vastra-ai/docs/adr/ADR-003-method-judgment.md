# ADR-003: Rules -> statistics -> ML -> LLM, in that order
Status: Accepted
Decision: Offers = rules (decision table). Reorder = statistical forecast first, ML only if it beats baseline WAPE. Search = BM25 + vectors. LLMs only for unstructured text (product attributes, invoices, SOP Q&A, agent intent), always behind the gateway with PII redaction and schema validation.
Consequences: Cheaper, explainable, auditable; LLM spend focused where it adds unique value.
