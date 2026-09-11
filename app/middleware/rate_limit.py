import logging

from fastapi import Depends, HTTPException
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.middleware.auth import verify_api_key

logger = logging.getLogger(__name__)

RATE_LIMIT_WINDOW_SECONDS = 60

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


async def enforce_rate_limit(api_key: str = Depends(verify_api_key)) -> str:
    key = f"ratelimit:{api_key}"
    try:
        count = await redis_client.incr(key)
        if count == 1:
            await redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    except RedisConnectionError as exc:
        logger.warning(
            "Redis unavailable, allowing request without rate limiting: %s", exc
        )
        return api_key

    if count > settings.rate_limit_per_minute:
        ttl = await redis_client.ttl(key)
        retry_after = ttl if ttl > 0 else RATE_LIMIT_WINDOW_SECONDS
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {settings.rate_limit_per_minute} requests per minute",
            headers={"Retry-After": str(retry_after)},
        )
    return api_key
