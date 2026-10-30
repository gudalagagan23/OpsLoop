import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values come from environment variables prefixed OPSLOOP_."""

    model_config = SettingsConfigDict(env_prefix="OPSLOOP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://opsloop:opsloop@localhost:55432/opsloop"
    prometheus_url: str = "http://localhost:9090"
    taskflow_url: str = "http://localhost:8080"
    taskflow_control_url: str = "http://localhost:9100"  # simulation only; never exposed
    # Dashboard origins allowed to call the API: JSON list or comma-separated
    # (e.g. "http://localhost:3000,https://opsloop.vercel.app").
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    # {"<token>": {"name": "alice", "role": "approver" | "viewer"}}; set in .env only.
    api_tokens: Annotated[dict[str, dict[str, str]], NoDecode] = {}

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return ["http://localhost:3000"]
            return (
                json.loads(v)
                if v.startswith("[")
                else [o.strip() for o in v.split(",") if o.strip()]
            )
        return v

    @field_validator("llm_fallback_models", mode="before")
    @classmethod
    def _fallbacks(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            return (
                json.loads(v)
                if v.startswith("[")
                else [m.strip() for m in v.split(",") if m.strip()]
            )
        return v

    @field_validator("api_tokens", mode="before")
    @classmethod
    def _tokens(cls, v: Any) -> Any:
        if isinstance(v, str):
            return json.loads(v) if v.strip() else {}
        return v

    # LLM provider (Claude API). Model names and prices come from the environment only; the
    # API key is read by the SDK from ANTHROPIC_API_KEY and never stored here.
    # Provider keys: read from .env or the environment, never logged (SecretStr).
    anthropic_api_key: SecretStr = Field(
        SecretStr(""),
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "OPSLOOP_ANTHROPIC_API_KEY"),
    )
    groq_api_key: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("GROQ_API_KEY", "OPSLOOP_GROQ_API_KEY")
    )
    gemini_api_key: SecretStr = Field(
        SecretStr(""), validation_alias=AliasChoices("GEMINI_API_KEY", "OPSLOOP_GEMINI_API_KEY")
    )
    llm_provider: str = "anthropic"  # anthropic | groq | gemini
    llm_model: str = ""
    llm_effort: str = "high"  # low | medium | high (Gemini: thinking level)
    # Tried in order when the primary model is overloaded (comma-separated or JSON list).
    llm_fallback_models: Annotated[list[str], NoDecode] = []
    llm_timeout_s: float = 240.0  # per request; a stuck call fails and is retried
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
