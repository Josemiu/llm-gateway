# LLM Gateway — Contexto del proyecto

## Qué es esto
Gateway de LLMs para portfolio: routing inteligente, fallback entre providers,
rate limiting, cost tracking y observabilidad. Ver DECISIONS.md para el porqué
de cada decisión técnica.

## Stack
- Python 3.13, FastAPI, Pydantic v2
- PostgreSQL (cost tracking), Redis (rate limiting)
- Docker + docker-compose (Postgres, Redis, Jaeger, y el gateway mismo)
- OpenTelemetry + Jaeger (tracing), k6 (load testing), GitHub Actions (CI)

## Estado
Proyecto cerrado como pieza de portfolio: routing (heurístico + adaptativo),
fallback, auth, rate limiting, cost tracking, métricas, CI/CD, load testing
y tracing distribuido están implementados y documentados. No se agregan
features nuevas salvo que aparezca un bug real — ver "Estado del proyecto"
en README.md y DECISIONS.md para el detalle completo.

## Reglas de trabajo
- Async/await en todo el I/O (llamadas a providers, DB, Redis).
- Type hints obligatorios en funciones públicas.
- Cada feature nueva necesita al menos un test antes de darla por cerrada.
- Documentar decisiones no triviales en DECISIONS.md, no solo en comentarios.
- Nombres de variables/funciones en inglés; comentarios y docs pueden ir en español.

## Git
- Nunca agregar "Co-Authored-By: Claude" ni menciones de Claude/AI en los mensajes de commit.
- El autor de todos los commits debe ser el configurado en git config (user.name/user.email), sin modificar.

## Estructura
app/routes/        → endpoints FastAPI
app/providers/      → integraciones con OpenAI, Gemini, y MockProvider (solo load testing)
app/routing/        → heurística de "auto" + ajuste adaptativo por stats reales
app/middleware/     → auth, rate limiting
app/services/       → lógica de negocio (cost tracking, métricas, provider stats)
app/schemas/        → modelos Pydantic
app/telemetry.py    → setup de OpenTelemetry (spans por capa, exportador a Jaeger)
load-tests/         → escenarios de k6
benchmarks/         → resultados reales de load testing, con entorno documentado
