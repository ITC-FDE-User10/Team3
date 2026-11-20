# Data contracts (topic: owner, schema, SLA)
| Topic | Owner | Key fields | Freshness SLA |
|---|---|---|---|
| inventory.stock.changed.v1 | Warehouse | sku, location, on_hand, ts | 60 s |
| pos.sale.recorded.v1 | Stores | sku, location, qty, ts | 60 s |
| pricing.price.changed.v1 | Pricing | sku, price, version | 60 s |
| commerce.order.placed.v1 | Ecommerce | order_id, lines, total, channel | 5 s |
| warehouse.goods.received.v1 | Warehouse | grn_id, po_number, lines | 15 min |
Personal data (DPDP Act 2023): phone/email only in customer domain, hashed for matching, never sent raw to LLMs. Data RACI in the solution document.
