"""Central configuration (12-factor). All secrets come from env vars, never code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    env: str = os.getenv("VASTRA_ENV", "dev")
    api_keys: tuple[str, ...] = tuple(
        k for k in os.getenv("VASTRA_API_KEYS", "dev-key").split(",") if k
    )
    # LLM gateway: "stub" works offline; "anthropic" / "openai" use real providers.
    llm_provider: str = os.getenv("VASTRA_LLM_PROVIDER", "stub")
    llm_model: str = os.getenv("VASTRA_LLM_MODEL", "claude-sonnet-5-5")
    llm_api_key: str = os.getenv("VASTRA_LLM_API_KEY", "")
    llm_timeout_s: float = float(os.getenv("VASTRA_LLM_TIMEOUT_S", "8"))
    # Rate limiting (requests per minute per API key)
    rate_limit_rpm: int = int(os.getenv("VASTRA_RATE_LIMIT_RPM", "600"))
    # Inventory: units held back from online sale per SKU-location as safety buffer
    online_safety_buffer: int = int(os.getenv("VASTRA_SAFETY_BUFFER", "1"))
    # Reorder policy
    service_level_z: float = float(os.getenv("VASTRA_SERVICE_Z", "1.65"))  # ~95%
    # Cart reservation TTL (seconds)
    reservation_ttl_s: int = int(os.getenv("VASTRA_RESERVATION_TTL", "900"))
    # Agent actions above this value need human approval (INR)
    hitl_threshold_inr: float = float(os.getenv("VASTRA_HITL_THRESHOLD", "50000"))
    feature_flags: dict = field(default_factory=lambda: {
        "ai_search": True,
        "ai_recommendations": True,
        "cart_recovery": True,
        "agent_auto_reorder": False,   # rollout ladder: shadow -> assist -> auto
    })


settings = Settings()
