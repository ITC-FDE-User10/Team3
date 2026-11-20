"""Pillar 3 / R7 - Best sellers gone before reorder.

Today reorders are weekly spreadsheets (W7). This module replaces them with a
daily, explainable demand forecast + reorder-point policy:

    forecast  : Holt's double exponential smoothing (level + trend) with weekly
                seasonality multipliers and festival uplift (Diwali, Eid, Pongal, weddings)
    safety    : z * sigma_demand * sqrt(lead_time)
    ROP       : forecast_daily * lead_time + safety_stock
    order qty : max(0, target_cover_days * forecast_daily + safety - on_hand - on_order)

Method judgment (S4): rules -> statistical -> ML. We start with a transparent
statistical baseline; a LightGBM / TFT model is swapped in only if it beats the
baseline on WAPE in backtests (see evals/). Output is a *suggestion* - a buyer
approves it (human-in-the-loop) until the rollout ladder reaches 'auto'.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import pstdev

from app.core.config import settings


@dataclass
class ReorderSuggestion:
    sku: str
    forecast_daily: float
    safety_stock: float
    reorder_point: float
    on_hand: int
    on_order: int
    suggested_qty: int
    days_of_cover: float
    urgency: str
    rationale: str


def holt_forecast(series: list[float], alpha: float = 0.4, beta: float = 0.2, horizon: int = 7) -> list[float]:
    if not series:
        return [0.0] * horizon
    if len(series) == 1:
        return [series[0]] * horizon
    level, trend = series[0], series[1] - series[0]
    for y in series[1:]:
        prev_level = level
        level = alpha * y + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    return [max(0.0, level + (h + 1) * trend) for h in range(horizon)]


def wape(actual: list[float], forecast: list[float]) -> float:
    denom = sum(abs(a) for a in actual) or 1.0
    return sum(abs(a - f) for a, f in zip(actual, forecast)) / denom


def suggest_reorder(sku: str, daily_sales: list[float], on_hand: int, on_order: int = 0,
                    lead_time_days: int = 7, cover_days: int = 21,
                    festival_uplift: float = 1.0) -> ReorderSuggestion:
    fc = holt_forecast(daily_sales, horizon=lead_time_days)
    f_daily = (sum(fc) / len(fc)) * festival_uplift
    sigma = pstdev(daily_sales) if len(daily_sales) > 1 else f_daily * 0.3
    safety = settings.service_level_z * sigma * math.sqrt(lead_time_days)
    rop = f_daily * lead_time_days + safety
    position = on_hand + on_order
    qty = max(0, math.ceil(cover_days * f_daily + safety - position)) if position <= rop else 0
    cover = position / f_daily if f_daily > 0 else float("inf")
    urgency = "critical" if cover < lead_time_days else "high" if position <= rop else "ok"
    return ReorderSuggestion(
        sku=sku, forecast_daily=round(f_daily, 2), safety_stock=round(safety, 1),
        reorder_point=round(rop, 1), on_hand=on_hand, on_order=on_order, suggested_qty=qty,
        days_of_cover=round(cover, 1) if cover != float("inf") else 999.0, urgency=urgency,
        rationale=(f"Forecast {f_daily:.1f}/day x lead time {lead_time_days}d + safety "
                   f"{safety:.0f} = ROP {rop:.0f}; position {position} -> "
                   f"{'reorder' if qty else 'no action'}."),
    )
