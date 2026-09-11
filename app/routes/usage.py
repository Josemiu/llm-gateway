from fastapi import APIRouter, Depends

from app.middleware.auth import verify_api_key
from app.schemas.usage import UsageSummary
from app.services.usage_service import get_usage_summary

router = APIRouter()


@router.get("/v1/usage", response_model=UsageSummary)
async def get_usage(api_key: str = Depends(verify_api_key)) -> UsageSummary:
    return await get_usage_summary(api_key)
