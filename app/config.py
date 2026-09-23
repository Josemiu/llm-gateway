from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    environment: str = "development"
    valid_api_keys: str = ""
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_per_minute: int = 20
    database_url: str = "postgresql+asyncpg://gateway:gateway_dev_pw@localhost:5432/llm_gateway"

    # Load testing only (see DECISIONS.md "Load testing con MockProvider"):
    # when enabled, routing uses MockProvider instead of the real OpenAI/Gemini
    # SDKs, so k6 runs don't spend real API credits or get bottlenecked by
    # provider-side rate limits. Defaults keep normal dev/prod behavior untouched.
    load_test_mode: bool = False
    mock_provider_latency_ms_min: int = 200
    mock_provider_latency_ms_max: int = 1500
    mock_provider_fail: str = ""

    # Adaptive routing for model="auto" (see DECISIONS.md - "Routing
    # adaptativo"). Explicit model requests (e.g. "gpt-4o") never go through
    # this - only the complexity heuristic's provider pick for "auto" can be
    # overridden, and only with enough real evidence.
    routing_stats_window_minutes: int = 60
    routing_min_samples: int = 20
    routing_error_rate_threshold: float = 0.5
    routing_latency_degradation_multiplier: float = 2.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
