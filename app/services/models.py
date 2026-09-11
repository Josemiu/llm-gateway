from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.services.database import Base


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key: Mapped[str] = mapped_column(index=True)
    model_requested: Mapped[str]
    model_used: Mapped[str]
    provider: Mapped[str]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    estimated_cost_usd: Mapped[float]
    latency_ms: Mapped[int]
    status: Mapped[str]
    used_fallback: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
