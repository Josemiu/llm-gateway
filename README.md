# LLM Gateway

[![CI](https://github.com/Josemiu/llm-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/Josemiu/llm-gateway/actions/workflows/ci.yml)

Gateway de LLMs con routing inteligente, fallback automático entre providers, auth, rate limiting, cost tracking y métricas — no un wrapper de ChatGPT.

## Arquitectura

```
Client
  │
  ▼
Auth (X-API-Key header) ──────── 401 si falta o es inválida
  │
  ▼
Rate Limit (Redis, fixed window) ─ 429 si se supera el límite
  │
  ▼
Routing (heurística "auto" + ajuste adaptativo con stats reales, o prefijo de modelo explícito)
  │
  ▼
Provider (Gemini u OpenAI) ──┐
  │                          │ falla (ProviderError / timeout)
  │ éxito                    ▼
  │                    Fallback → el otro provider (un solo reintento)
  │                          │
  └──────────┬───────────────┘
             ▼
     Cost Tracking (background task, no bloquea la respuesta) → Postgres
             ▼
         Response
```

Cada capa (auth, rate limit, routing, provider, DB) emite su propio span de OpenTelemetry, exportado a Jaeger cuando `OTEL_ENABLED=true` — ver sección de Observability más abajo.

`GET /v1/usage` y `GET /metrics` leen de la misma tabla de Postgres (`usage_records`) para exponer agregados por API key y globales, respectivamente.

## Features

- **Routing con heurística explicable**: longitud del prompt + keywords simples (`code`, `analyze`, `explain in detail`) deciden entre Gemini (rápido/barato) y OpenAI (más potente) cuando `model: "auto"`.
- **Routing adaptativo sobre esa heurística**: la elección por complejidad puede ser corregida con datos reales de `usage_records` (ventana configurable, default 1h) — si el provider elegido tiene un error rate reciente demasiado alto, o es notablemente más lento que la alternativa, se cambia al otro. Solo actúa con evidencia suficiente (mínimo de muestras configurable); con pocos datos, se respeta la heurística original. Ver [`DECISIONS.md`](./DECISIONS.md).
- **Fallback automático entre providers**: si el provider elegido falla (error o timeout), se reintenta una vez con el otro antes de devolver un error al cliente.
- **Auth por API key**: header `X-API-Key` validado contra una lista en `.env`, implementado como dependency explícita de FastAPI.
- **Rate limiting con Redis**: fixed window counter (`INCR`+`EXPIRE`) por API key, con fail-open si Redis no responde.
- **Cost tracking en Postgres**: cada request queda registrada (tokens, costo estimado, latencia, éxito/error, si usó fallback) sin bloquear la respuesta.
- **Endpoint de métricas**: agregados globales con ventana de los últimos 60s y del día corrido (UTC).
- **Tracing distribuido con OpenTelemetry**: un span por capa (auth, rate limiting, routing, llamada al provider, escritura en Postgres) exportado a Jaeger vía OTLP — permite ver dónde se va el tiempo dentro de un request individual, algo que `/metrics` (agregado) no muestra. Desactivado por default fuera de Docker (`OTEL_ENABLED=false`); UI en `http://localhost:16686` al levantar el stack completo. Ver [`DECISIONS.md`](./DECISIONS.md).

## Stack

- Python 3.13, FastAPI, Pydantic v2
- PostgreSQL 16 (cost tracking), Redis 7 (rate limiting)
- SQLAlchemy 2.0 (async, driver `asyncpg`) + Alembic para migraciones
- Docker / docker-compose (Redis, Postgres y Jaeger en desarrollo local; el gateway también corre dockerizado — ver `docker-compose.yml`)
- OpenTelemetry + Jaeger (tracing distribuido, opcional)
- Providers: OpenAI SDK y Google GenAI SDK (Gemini)

## Cómo levantarlo localmente

```bash
git clone <este-repo>
cd llm-gateway

python -m venv venv
venv\Scripts\activate          # Windows (o `source venv/bin/activate` en Linux/Mac)
pip install -r requirements.txt

docker compose up -d           # levanta Redis (6379), Postgres (5432) y Jaeger (16686)

cp .env.example .env
# completar en .env: OPENAI_API_KEY, GEMINI_API_KEY, VALID_API_KEYS
# (REDIS_URL, RATE_LIMIT_PER_MINUTE y DATABASE_URL ya traen defaults que
# matchean el docker-compose.yml, no hace falta tocarlos en local)
# OTEL_ENABLED=false por default - poner en true para ver traces en
# http://localhost:16686 (Jaeger ya está arriba del `docker compose up -d`)

alembic upgrade head            # crea la tabla usage_records en Postgres

uvicorn app.main:app --reload
```

Para correr los tests (no dependen de Redis/Postgres reales — usan `fakeredis` y SQLite en memoria):
```bash
pytest tests/ -v
```

## CI

GitHub Actions (`.github/workflows/ci.yml`) corre en cada push/PR a `main`: instala `requirements.txt` + `requirements-dev.txt`, ejecuta `ruff check .` y despues `pytest tests/ -v`. No requiere Redis/Postgres reales (mismo motivo que los tests locales), así que no hay servicios levantados en el workflow.

## Performance / Load Testing

El gateway fue probado con [k6](https://k6.io) bajo 4 escenarios (tráfico normal, rampa de concurrencia hasta 250 VUs, rate limiting, y fallo forzado de provider con fallback) contra el stack real en Docker. Para no depender de créditos ni de los rate limits de OpenAI/Gemini reales, estas corridas usan `app/providers/mock_provider.py` (activado solo con `LOAD_TEST_MODE=true`, default `false` — no afecta al gateway en uso normal). Resultados reales, entorno de prueba documentado y metodología completa en [`benchmarks/README.md`](./benchmarks/README.md); scripts en [`load-tests/`](./load-tests/).

Resumen de lo medido (ver el detalle y las salvedades en `benchmarks/README.md`):
- 0% de errores en los 4 escenarios, incluyendo el pico de 250 VUs concurrentes (p95 = 1.48s).
- Rate limiting confirmado exacto: 20/40 requests aceptadas, 20/40 rechazadas con 429, con `RATE_LIMIT_PER_MINUTE=20`.
- Fallback confirmado al 100% (119/119) ante un fallo forzado del provider primario.

## Ejemplos de uso

**Chat completion** (routing automático):
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "X-API-Key: devkey1" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain what a load balancer does"}]
  }'
```
```json
{
  "content": "A load balancer distributes incoming network traffic...",
  "model": "gemini-3.5-flash-lite",
  "usage": {"input_tokens": 8, "output_tokens": 142, "total_tokens": 150}
}
```

**Uso acumulado de una API key**:
```bash
curl http://localhost:8000/v1/usage -H "X-API-Key: devkey1"
```
```json
{
  "api_key": "devkey1",
  "total_requests": 12,
  "total_input_tokens": 340,
  "total_output_tokens": 2150,
  "total_estimated_cost_usd": 0.00412,
  "avg_latency_ms": 780.5,
  "fallback_rate": 0.0833
}
```

**Métricas globales** (sin auth, endpoint operacional):
```bash
curl http://localhost:8000/metrics
```
```json
{
  "last_60s": {
    "request_count": 3,
    "requests_per_second": 0.05,
    "avg_latency_ms": 612.0,
    "error_rate": 0.0,
    "fallback_rate": 0.0
  },
  "today": {
    "request_count": 45,
    "requests_per_second": 0.0021,
    "avg_latency_ms": 701.4,
    "error_rate": 0.022,
    "fallback_rate": 0.11,
    "cost_usd": 0.0187
  }
}
```

## Decisiones técnicas destacadas

- **`BackgroundTasks` se pierde si el endpoint hace `raise HTTPException`**: Starlette solo ejecuta las background tasks adjuntas a la `Response` que efectivamente se envía. Por eso el camino de "ambos providers fallaron" devuelve `JSONResponse(..., background=background_tasks)` en vez de levantar la excepción — si no, el registro de cost tracking del caso de error se perdía silenciosamente.
- **Rate limiting fail-open**: si Redis no responde, la request pasa sin aplicar el límite (con un `WARNING` en logs) en vez de tirar el gateway completo — Redis todavía no es infraestructura de alta disponibilidad en este proyecto.
- **Tests aislados con SQLite + `StaticPool`**: toda la suite corre sin depender de Postgres/Redis reales (`fakeredis` + SQLite en memoria), verificando explícitamente que las queries agregadas dan el mismo resultado en ambos dialectos antes de confiar en el atajo.
- **Un solo punto de patch para la sesión de DB**: los módulos de servicio acceden vía `database.async_session_factory()` (no importan el nombre directo), así los tests parchean un único lugar canónico y ningún módulo nuevo puede "olvidarse" de quedar aislado de la base real — esto costó un bug real (tests escribiendo en Postgres de verdad) que quedó documentado.
- **Bug real: el fallo del provider primario no quedaba registrado**: antes de construir el routing adaptativo se descubrió que `usage_records` solo grababa una fila por request, atribuida siempre al provider que dio el resultado final — el fallo del primario, cuando disparaba un fallback (exitoso o no), nunca quedaba bajo su propio nombre. Eso hacía imposible calcular un error rate real por provider. Se agregó `is_final_attempt` (migración aditiva) para distinguir "intento" de "respuesta al cliente" sin cambiar el significado de `/v1/usage` ni `/metrics`.
- **Routing adaptativo sin pesos mágicos**: la heurística por complejidad de Fase 3 se mantiene intacta; encima se agregó una capa que solo actúa con evidencia real (mínimo de muestras configurable) y en orden fijo — reliability primero (error rate), después latencia (con un multiplicador relativo, no un umbral en ms inventado), costo ya implícito en la heurística. No es un circuit breaker: no guarda estado propio, lee Postgres en cada decisión.
- **Load testing sin gastar créditos reales**: los 4 escenarios de k6 corren contra un `MockProvider` (activado solo con `LOAD_TEST_MODE=true`) en vez de OpenAI/Gemini reales — se descartó explícitamente usar las APIs reales porque la key de OpenAI configurada no tiene créditos (confirmado con una llamada real) y porque el escenario de concurrencia (250 VUs) habría generado costo real y chocado con los rate limits de los providers en vez de medir el overhead propio del gateway.
- **Bug real: `docker compose up` desde cero fallaba**: al cerrar el proyecto se probó por primera vez un `docker compose up` contra un volumen de Postgres realmente vacío (no el de desarrollo, ya usado desde la Fase 5). Una migración de la Fase 5 tenía un `DROP TABLE t` arrastrado por error de un `autogenerate` sobre una base sucia — nunca se había notado porque esa tabla `t` ya existía por casualidad en el Postgres de desarrollo. Corregido y reverificado desde cero.

Ver [`DECISIONS.md`](./DECISIONS.md) para el detalle completo de estas y otras ~25 decisiones documentadas a lo largo del desarrollo.

## Estado del proyecto

Cerrado como proyecto de portfolio. Todas las etapas planeadas están completas:

- **CI/CD**: GitHub Actions con Ruff + pytest en cada push/PR a `main`.
- **Load testing con k6**: 4 escenarios (tráfico normal, concurrencia hasta 250 VUs, rate limiting, fallback ante fallo de provider), resultados reales en [`benchmarks/README.md`](./benchmarks/README.md).
- **Routing adaptativo**: `model: "auto"` considera error rate y latencia reales por provider (ventana configurable, mínimo de muestras antes de actuar) además de la heurística por complejidad.
- **Observability**: tracing distribuido por capa (auth, rate limit, routing, provider, DB) exportado a Jaeger vía OTLP, verificado con un trace real end-to-end.

Ver [`DECISIONS.md`](./DECISIONS.md) para el detalle de cada decisión.

**Fuera de alcance, por decisión explícita** (no por falta de tiempo):

- **OllamaProvider**: no implementado por limitaciones de hardware disponible durante el desarrollo, pero la interfaz `LLMProvider` ya lo soporta como una extensión trivial (solo implementar `generate()`, sin tocar el resto del gateway).
- Semantic caching, Kubernetes y microservicios adicionales quedaron fuera de alcance desde el planteo del proyecto — no había una razón técnica concreta para este gateway que los justificara. Tampoco se agregó un circuit breaker con estado propio: el routing adaptativo cubre ese caso de uso leyendo Postgres en cada decisión, sin necesitar estado compartido entre workers (ver `DECISIONS.md`).
