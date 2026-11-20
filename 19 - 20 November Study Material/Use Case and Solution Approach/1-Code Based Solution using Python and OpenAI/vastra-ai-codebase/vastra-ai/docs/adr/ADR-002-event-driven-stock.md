# ADR-002: Event-driven stock and price instead of 4-hour / nightly batches
Status: Accepted
Decision: Debezium CDC from MySQL/ERP/WMS + POS sale events -> Kafka topics with versioned data contracts. Consumers are idempotent; failures go to a dead-letter topic.
Consequences: Stock freshness 4h -> <60s (R5, R6); price consistency across channels (R4). Requires schema registry and on-call.
