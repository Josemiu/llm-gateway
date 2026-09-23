from datetime import datetime

from sqlalchemy import Boolean, DateTime, func
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
    # True for the attempt whose outcome was actually returned to the client
    # (what /v1/usage and /metrics count - unchanged meaning). False for a
    # primary provider's failed attempt that was superseded by a fallback:
    # those used to go unrecorded entirely under the primary's own provider
    # name (see DECISIONS.md - "usage_records no registraba el fallo del
    # provider primario"), which made per-provider error rates computed
    # straight from this table wrong for whichever provider tends to be
    # tried first. Provider-level stats (app/services/provider_stats_service.py)
    # read both; request-facing aggregates filter to True only.
    is_final_attempt: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
