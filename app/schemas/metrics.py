from pydantic import BaseModel


class MetricsWindow(BaseModel):
    request_count: int
    requests_per_second: float
    avg_latency_ms: float
    error_rate: float
    fallback_rate: float


class TodayMetricsWindow(MetricsWindow):
    cost_usd: float


class MetricsSummary(BaseModel):
    last_60s: MetricsWindow
    today: TodayMetricsWindow
