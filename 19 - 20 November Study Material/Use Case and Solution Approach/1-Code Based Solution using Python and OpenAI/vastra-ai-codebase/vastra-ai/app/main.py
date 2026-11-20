"""Vastra AI Core - entrypoint.

    uvicorn app.main:app --reload     ->  http://localhost:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.api.routes import router
from app.core.observability import metrics
from data.seed import seed

@asynccontextmanager
async def lifespan(_: FastAPI):
    seed()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Vastra & Co. AI Core",
    version="1.0.0",
    description="Event-driven, AI-enabled services that strangle the 2014 ecommerce monolith, "
                "organised by the four value pillars.",
)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
def prom():
    return metrics.prometheus()
