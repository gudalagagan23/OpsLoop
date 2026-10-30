from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from environment variables prefixed OPSLOOP_."""

    model_config = SettingsConfigDict(env_prefix="OPSLOOP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://opsloop:opsloop@localhost:55432/opsloop"
    prometheus_url: str = "http://localhost:9090"
    cors_origins: list[str] = ["http://localhost:3000"]
    # {"<token>": {"name": "alice", "role": "approver" | "viewer"}}; set in .env only.
    api_tokens: dict[str, dict[str, str]] = {}

    # LLM provider (Claude API). Model names and prices come from the environment only; the
    # API key is read by the SDK from ANTHROPIC_API_KEY and never stored here.
    llm_model: str = ""
    llm_effort: str = "high"
    llm_input_usd_per_mtok: float = 0.0
    llm_output_usd_per_mtok: float = 0.0
    llm_cache_read_usd_per_mtok: float = 0.0
    llm_cache_write_usd_per_mtok: float = 0.0

    # Embeddings: "fastembed" (local, needs OPSLOOP_EMBEDDING_MODEL) or "hash" (tests only).
    embedding_provider: str = "fastembed"
    embedding_model: str = ""
    embedding_dim: int = 384

    # Budgets per incident (spec section 10).
    max_attempts: int = 4
    max_specialist_switches: int = 2
    incident_time_budget_s: float = 1200
    incident_cost_cap_usd: float = 5.0

    escalation_confidence: float = 0.6
    approval_ttl_s: float = 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
