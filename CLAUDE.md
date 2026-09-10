# LLM Gateway — Contexto del proyecto

## Qué es esto
Gateway de LLMs para portfolio: routing inteligente, fallback entre providers,
rate limiting, cost tracking y observabilidad. Ver DECISIONS.md para el porqué
de cada decisión técnica.

## Stack
- Python 3.11+, FastAPI, Pydantic v2
- PostgreSQL (cost tracking), Redis (rate limiting, fases posteriores)
- Docker + docker-compose

## Fase actual
Fase 1: endpoint base + integración directa con OpenAI (sin abstracción de provider aún).

## Reglas de trabajo
- Async/await en todo el I/O (llamadas a providers, DB, Redis).
- Type hints obligatorios en funciones públicas.
- Cada feature nueva necesita al menos un test antes de darla por cerrada.
- No adelantar funcionalidad de fases futuras (ej: no meter circuit breaker
  en fase 1-3, no meter Prometheus antes de fase 6).
- Documentar decisiones no triviales en DECISIONS.md, no solo en comentarios.
- Nombres de variables/funciones en inglés; comentarios y docs pueden ir en español.

## Git
- Nunca agregar "Co-Authored-By: Claude" ni menciones de Claude/AI en los mensajes de commit.
- El autor de todos los commits debe ser el configurado en git config (user.name/user.email), sin modificar.

## Estructura
app/routes/      → endpoints FastAPI
app/providers/    → integraciones con OpenAI, Anthropic, Ollama
app/routing/      → lógica de decisión de qué modelo/provider usar
app/middleware/   → auth, rate limiting
app/services/     → lógica de negocio (cost tracking, etc.)
app/schemas/      → modelos Pydantic
