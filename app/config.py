from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    environment: str = "development"
    valid_api_keys: str = ""
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_per_minute: int = 20
    database_url: str = "postgresql+asyncpg://gateway:gateway_dev_pw@localhost:5432/llm_gateway"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
