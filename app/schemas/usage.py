from pydantic import BaseModel


class UsageSummary(BaseModel):
    api_key: str
    total_requests: int
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_usd: float
    avg_latency_ms: float
    fallback_rate: float
