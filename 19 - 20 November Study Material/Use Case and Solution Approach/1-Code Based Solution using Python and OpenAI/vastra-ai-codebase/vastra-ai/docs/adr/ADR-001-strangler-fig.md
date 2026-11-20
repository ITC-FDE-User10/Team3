# ADR-001: Strangle the 2014 monolith instead of a big-bang rewrite
Status: Accepted
Context: Single on-prem data centre, serial checkout, batch stock/price/order transfers, shared DB admin logins.
Decision: Put an API facade in front of the monolith. Move capabilities out one at a time (price authority, ATP stock, checkout orchestration, search) behind feature flags; monolith remains system of record for orders until phase 3.
Consequences: Value lands in weeks, not years; dual-running cost for ~2 quarters; needs CDC + reconciliation jobs.
