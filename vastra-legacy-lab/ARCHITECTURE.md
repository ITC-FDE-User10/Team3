# Vastra legacy lab architecture

This diagram describes the runnable legacy lab. The NGINX routes are shown separately because they are configured in `../gateway/nginx.conf` for S2; the local run instructions start the monolith and partner stubs directly. The `/v1/` upstream is a configured integration point, not a service implemented in this lab.

```mermaid
flowchart LR
    web[Web customer]
    mobile[Mobile app]
    pos[Store POS]
    erp[ERP]
    wms[Warehouse management]

    subgraph entry["Request entry"]
        direct["Direct lab access<br/>:8000"]
        nginx["NGINX gateway<br/>:8080<br/>request ID and access log"]
        services["New services upstream<br/>:8200<br/>configured integration point"]
    end

    subgraph lab["Vastra legacy lab"]
        monolith["2014 ecommerce monolith<br/>FastAPI :8000<br/>product page · checkout · private app API"]
        webprice["Website pricing rules"]
        appprice["Private app API pricing rules"]
        posprice["POS pricing rules<br/>(standalone copy)"]
        db[("SQLite database<br/>products · stock_copy · orders · payments")]
        partners["Partner API stubs<br/>:8100<br/>courier · payment · SMS/email notify"]

        stockjob["Stock sync<br/>every 4 hours"]
        pricejob["Nightly price import"]
        orderjob["Nightly order export"]
    end

    web -->|"local :8000"| direct
    mobile -->|"local /app-api/v0/price :8000"| direct
    direct --> monolith

    web -. "optional /legacy/*" .-> nginx
    mobile -. "optional /legacy/*" .-> nginx
    nginx -->|"/legacy/* to :8000"| monolith
    nginx -->|"/v1/* to :8200"| services

    monolith --> webprice
    monolith --> appprice
    monolith -->|"read/write products, stock, orders, payments"| db
    monolith -->|"HTTP: courier promise, payment charge"| partners

    pos --> posprice
    posprice -. "duplicated rules; separate runtime" .-> webprice

    pos -->|"stock export CSV"| stockfile["data/drop/stock_from_pos_and_wms.csv"]
    wms -->|"stock export CSV"| stockfile
    stockfile --> stockjob
    stockjob -->|"refresh stock_copy"| db

    erp -->|"price list CSV"| pricefile["data/drop/erp_price_list.csv"]
    pricefile --> pricejob
    pricejob -->|"update products"| db

    db -->|"paid orders"| orderjob
    orderjob -->|"orders_to_erp.csv"| erp
```

## Runtime and data notes

- The local database is SQLite at `data/vastra_legacy.db`; it stands in for the legacy MySQL database.
- The monolith calls the courier and payment stub APIs during checkout. The stubs also expose a notification endpoint.
- POS pricing is a separate implementation; it is not served by the monolith.
- The stock and price jobs import CSV files into the database. The order job exports paid orders to a CSV for the ERP.
- The local startup instructions bypass NGINX. When used, the gateway sends `/legacy/*` traffic to the monolith and `/v1/*` traffic to the configured new-services upstream.
