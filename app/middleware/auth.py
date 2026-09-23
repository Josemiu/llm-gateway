from fastapi import Header, HTTPException

from app.config import settings
from app.telemetry import tracer


def _parse_valid_keys(raw: str) -> set[str]:
    return {key.strip() for key in raw.split(",") if key.strip()}


async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key")
) -> str:
    with tracer.start_as_current_span("auth.verify_api_key"):
        if not x_api_key or x_api_key not in _parse_valid_keys(settings.valid_api_keys):
            raise HTTPException(status_code=401, detail="Missing or invalid API key")
        return x_api_key
