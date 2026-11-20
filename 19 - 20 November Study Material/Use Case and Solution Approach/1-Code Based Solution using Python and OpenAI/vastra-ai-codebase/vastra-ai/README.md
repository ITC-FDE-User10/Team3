# Vastra & Co. – AI Core

Reference implementation for the Forward Deployed Engineer case study: a fictional Indian
omnichannel retailer whose AI ambitions are blocked by a slow, fragile 2014 monolith that moves data by batch.

The code is organised by the **four value pillars** and maps every module to the requirement it fixes.

| Pillar | KPI | Requirement | Module |
|---|---|---|---|
| 1 Sales & revenue | AOV | R8 search & recommendations | `app/services/search.py` |
| | | R9 single customer profile | `app/services/customer360.py` |
| | | R4 consistent offers | `app/services/pricing.py` |
| 2 User friction | Conversion, cart abandonment, p95 latency | R1, R10 conversion to order | `app/services/checkout.py` |
| | | R2, R3, R4, R11 cart abandonment | `app/services/checkout.py` (`abandonment_risk`) |
| 3 Logistics & backend | Overselling, stock-outs, reorders, cost/order | R5, R6 sold-out stock on sale | `app/services/inventory.py` |
| | | R7 best sellers gone before reorder | `app/services/forecast.py` |
| | | R18 slow order despatch | `app/services/dispatch.py` |
| 4 Cross-cutting | Data quality, compliance, security, SOPs | R12 product data inconsistency | `app/services/data_quality.py` |
| | | R17 manual GRN & invoicing | `app/services/invoice.py` |
| | | SOPs / security | `app/ai/rag.py`, `app/ai/llm.py`, `app/core/security.py` |
| Agents | – | Ops concierge, MCP | `app/agents/` |

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload          # http://localhost:8000/docs  (header X-API-Key: dev-key)
pytest -q                              # 23 tests
python -m evals.run_evals              # AI eval gates (blocks CI on regression)
python -m app.agents.mcp_server        # MCP server over stdio
docker compose up                      # API + Prometheus + Grafana
```

Runs fully offline with a deterministic LLM stub. For a real model:
`VASTRA_LLM_PROVIDER=anthropic VASTRA_LLM_API_KEY=... pip install anthropic`.

## Try it

```bash
curl -H "X-API-Key: dev-key" "localhost:8000/v1/search?q=red%20sari%20for%20wedding"
curl -H "X-API-Key: dev-key" "localhost:8000/v1/price/SR-101?channel=pos&loyalty_tier=gold"
curl -H "X-API-Key: dev-key" "localhost:8000/v1/reorder/SR-101?festival_uplift=1.4"
curl -H "X-API-Key: dev-key" -H "Idempotency-Key: abc" -H "Content-Type: application/json" \
     -d '{"lines":[{"sku":"KT-201","qty":1}],"payment_token":"tok_ok"}' localhost:8000/v1/checkout
curl -H "X-API-Key: dev-key" -H "Content-Type: application/json" \
     -d '{"message":"SR-101 will run out, order 100","scopes":["ops.read","ops.write"]}' localhost:8000/v1/agent
curl -H "X-API-Key: dev-key" localhost:8000/v1/kpis
```

## Layout

```
app/
  core/        config, event bus (Kafka stand-in), security, observability/KPIs
  services/    pricing, inventory, forecast, dispatch, customer360, search, checkout, data_quality, invoice
  ai/          llm gateway (guardrails, PII redaction, caching), policy RAG
  agents/      tool registry, supervisor + specialist agents, MCP server
  api/         FastAPI routes (v1)
data/seed.py   synthetic catalogue, stock, sales, baskets
evals/         offline eval gates
tests/         pytest suite
docs/          ADRs, data contracts
ops/           Prometheus config, SLOs and rollback policy
```

## What changes in production
In-memory bus → Kafka + Debezium CDC; dicts → Postgres/Redis; hashed embeddings → a multilingual
embedding model + pgvector/OpenSearch; stub OCR → Azure Document Intelligence/Textract; API keys → OIDC;
deploy to AKS/EKS behind an API gateway, with an AI gateway for LLM rate limiting, routing and cost tracking.
