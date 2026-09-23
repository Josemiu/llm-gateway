## Load testing con `MockProvider`, no contra OpenAI/Gemini reales

Para los 4 escenarios de k6 (`load-tests/`) se necesitaba generar cientos o
miles de requests de chat completions sin depender de los providers reales.
Se evaluó y descartó llamar a las APIs reales: al intentar medir latencia
real de referencia para calibrar el mock, una llamada real a OpenAI
(`gpt-4o-mini`) devolvió `429 insufficient_quota` /
`credit_balance_exhausted` — la API key configurada en este proyecto no
tiene créditos cargados. Aunque tuviera créditos, el escenario de
concurrencia (hasta 250 VUs) y el de fallo forzado repetido habrían
generado cientos de requests de pago y chocado con los rate limits propios
de los providers, que no son lo que estos tests miden (el objetivo es medir
el overhead propio del gateway: auth, rate limiting, routing, fallback,
background task de cost tracking).

Se agregó `app/providers/mock_provider.py` (`MockProvider`), activado
únicamente con `settings.load_test_mode` (default `false`, sin efecto en
dev/producción). El branch vive en `app/routing/selector.py::_build_decision`
y es el único punto de la app que lo conoce. `MockProvider` simula latencia
con `asyncio.sleep` en un rango configurable
(`MOCK_PROVIDER_LATENCY_MS_MIN/MAX`, default 200-1500ms — calibrado con 3
llamadas reales a Gemini que sí funcionaron: 1076/1401/1076ms, avg 1245ms;
no se pudo calibrar con OpenAI real por el problema de créditos de arriba,
así que el mismo rango se aplica a ambos providers mockeados) y puede forzar
el fallo de un provider específico vía `MOCK_PROVIDER_FAIL` (nombre de
provider, o varios separados por coma) para el escenario D. Los tokens que
devuelve son una proxy por conteo de palabras, solo para que el cost
tracking downstream tenga números no-cero con los que trabajar — no son una
medición real, y los `estimated_cost_usd` que salen de una corrida de load
testing no reflejan gasto real (ver `benchmarks/README.md`).

## Escenario B (concurrencia) sube el rate limit; Escenario C lo deja en default

El Escenario B de k6 mide capacidad de manejo de concurrencia del gateway
(FastAPI + Redis + Postgres), no el rate limiter — por eso, solo para esa
corrida (y para D, que tampoco testea el limiter), se subió
`RATE_LIMIT_PER_MINUTE` a un valor alto (100000) en `.env.docker` antes de
levantar el gateway. El Escenario C, que sí testea el rate limiter
específicamente, corre con el default real (20). Mezclar ambos objetivos en
un solo escenario habría hecho que el resultado de B fuera "cuántos 429
tira Redis" en vez de "cuánta concurrencia aguanta el gateway".

**Hallazgo real durante la calibración de C**: la primera corrida de este
escenario usó `devkey1` y dio 100% de requests rechazadas (esperábamos una
mezcla ~50/50). Causa: `devkey1` ya había acumulado miles de requests
durante el Escenario B (corrido minutos antes con el límite elevado) sobre
la misma key, y el contador fixed-window de Redis (`ratelimit:devkey1`)
seguía vivo dentro de su ventana de 60s cuando arrancó C con el límite bajo
de nuevo. No es un bug — es el fixed window counter comportándose tal como
está documentado (ver entrada "Rate limiting: fixed window counter, no
token bucket real" más abajo) — pero sí confirma en la práctica que cambiar
`RATE_LIMIT_PER_MINUTE` en caliente no resetea el estado ya acumulado en
Redis. Se repitió el escenario con `devkey2` (sin uso previo) para aislar
la medición; el resultado fue el esperado (20/40 aceptadas, 20/40
rechazadas, exacto).

## CI: `requirements-dev.txt` separado, no todo en `requirements.txt`

Ruff se agregó como dependencia de lint en `requirements-dev.txt` en vez de
sumarlo a `requirements.txt`. El `Dockerfile` (`docker/Dockerfile`) instala
únicamente `requirements.txt` para construir la imagen de producción del
gateway; una herramienta de desarrollo (linter) no tiene motivo para viajar
en esa imagen. El workflow de CI (`.github/workflows/ci.yml`) instala ambos
archivos. Reglas de Ruff elegidas: `E`, `F`, `I` (pycodestyle, pyflakes,
import sorting) — el set por defecto, sin reglas adicionales agresivas,
porque el objetivo de esta etapa es detectar errores reales y mantener
imports ordenados, no imponer un estilo extenso sin evidencia de que haga
falta. Se excluye `alembic/versions/` del lint porque son migraciones
autogeneradas por Alembic, no código escrito a mano.

## Thinking de Gemini: no se fuerza a apagado (revertido)

Se había intentado deshabilitar el thinking de Gemini en el MVP pasando
`thinking_config=ThinkingConfig(thinking_budget=0)`, para evitar que
`thoughts_token_count` complicara la relación `total_tokens = input_tokens +
output_tokens`. Se revirtió: para `gemini-3.5-flash-lite` (y en general para la
familia Gemini 3.x, que reemplazó a `thinking_budget` por `thinking_level`) ese
parámetro es inválido y la API devuelve `400 INVALID_ARGUMENT`. Por ahora el
provider no manda `thinking_config` y queda el comportamiento default del
modelo, incluyendo la posibilidad de que sume tokens de thinking no reflejados
en `output_tokens`. El manejo correcto de tokens de razonamiento (thinking/
reasoning tokens) across providers se abordará en la Fase 6 (Cost Tracking),
cuando se decida una política uniforme para todos los providers que los
soporten.

## Ollama provider no implementado

La interfaz `LLMProvider` está diseñada para soportar cualquier provider,
incluyendo modelos locales via Ollama, pero no se implementó por limitaciones
de hardware disponible durante el desarrollo. Agregar un `OllamaProvider()`
sería una extensión trivial dado el diseño actual: solo requiere implementar
el método `generate()` de la interfaz, sin tocar el endpoint ni el resto del
gateway.

## Heurística de routing "auto": simple y explicable, no ML

Para resolver `model: "auto"` se eligió una heurística simple y 100%
explicable en vez de embeddings/clasificadores ML: se inspecciona sólo el
contenido de los mensajes con `role == "user"` (concatenados) y se elige
OpenAI ("gpt-4o-mini") si el texto supera los 200 caracteres o contiene
alguna de las keywords "code", "analyze", "explain in detail"; en caso
contrario se usa Gemini ("gemini-3.5-flash-lite") por ser el provider
rápido/económico. Umbral y keywords son valores iniciales elegidos a mano,
sin tuning con datos reales. Queda documentado porque es una decisión de
diseño no trivial con margen de mejora futuro (más keywords, o un
clasificador liviano) una vez haya datos de uso reales que lo justifiquen.

## Fallback simple, sin circuit breaker

Para la Fase 3 se implementó fallback "intentar una vez con el otro
provider" sin circuit breaker ni estado persistente. Con sólo 2 providers,
un breaker con estados (open/half-open/closed) agrega complejidad sin
beneficio claro: cada request ya prueba el provider primario y, si falla,
prueba el único fallback posible una sola vez. Un breaker en memoria
tampoco sería confiable con múltiples workers de uvicorn (estado no
compartido entre procesos), y Redis no existe todavía en el proyecto. Si en
el futuro un provider falla de forma sostenida y el fallback constante a
OpenAI (de pago) se vuelve costoso, se puede reconsiderar un breaker con
estado compartido (Redis) — no antes.

## `openai` actualizado a 1.55.3 (bug real, no cosmético)

Probando el fallback real contra OpenAI apareció un 500 genérico sin
`detail`: `AsyncClient.__init__() got an unexpected keyword argument
'proxies'`. Causa: `openai==1.54.0` (pineado desde Fase 1) construye su
cliente HTTP interno pasando `proxies` a `httpx.AsyncClient`, parámetro que
`httpx` eliminó en la versión 0.28 — versión a la que tuvimos que subir en
Fase 2 porque `google-genai` la requiere. El fix conocido (confirmado en la
comunidad de OpenAI) es actualizar `openai` a partir de 1.55.3. Se pineó
`openai==1.55.3` (no la última 3.x disponible, que implicaría una migración
de SDK fuera de alcance de esta fase) y se confirmó que sigue satisfaciendo
`httpx<1,>=0.23.0` sin generar nuevos conflictos (`pip check` limpio).

## Identidad de API key = la key misma, sin mapeo a team/nombre

Para Fase 4 (auth) no se implementó un mapeo `key -> nombre/team` en `.env`.
La propia API key ya es un identificador único y suficiente para todo lo que
hace falta ahora (rate limiting por key). Agregar un mapeo requeriría un
mini-parser adicional en `.env` sin ningún comportamiento distinto todavía
(no hay quotas por team ni billing por team — eso es Fase 5+). Se puede
agregar cuando haga falta diferenciar comportamiento por team, no antes.

## Rate limiting: fixed window counter, no token bucket real

Se implementó con `INCR`+`EXPIRE` sobre una key `ratelimit:{api_key}` en
Redis: esto es técnicamente un **fixed window counter**, no un token bucket
real (que permitiría ráfagas suavizadas con refill continuo en vez de un
reseteo abrupto por ventana). Es lo que alcanza para el requisito actual
("N requests por minuto, resetea cada minuto") y es mucho más simple de
razonar y testear. Limitación conocida y aceptada: permite ráfagas en el
borde de la ventana (ej. N requests en el último segundo de una ventana +
N más en el primer segundo de la siguiente). Si esto se vuelve un problema
real, se puede migrar a un sliding window log o a un token bucket con Lua
script para atomicidad — no antes de tener evidencia de que hace falta.

## Rate limiting: fail-open si Redis no responde

Si Redis no responde (`redis.exceptions.ConnectionError`), la request pasa
sin aplicar rate limiting (con un `WARNING` en logs) en vez de devolver un
error. Redis es infra de dev local todavía, sin alta disponibilidad — un
fail-closed tumbaría todo el gateway por una dependencia que no es crítica
todavía. El auth por API key no se ve afectado (no depende de Redis). Se
reevalúa esta decisión cuando Redis pase a ser infraestructura productiva
con garantías de disponibilidad.

## Tests de rate limiting sin Redis real: `fakeredis` + `freezegun`

Los tests de `tests/test_rate_limit.py` reemplazan `redis_client` por un
`fakeredis.FakeAsyncRedis()` (compatible como drop-in de
`redis.asyncio.Redis` — confirmado con `isinstance`), para no depender de
un Redis corriendo en CI. Para probar que la ventana resetea sin dormir 60s
reales, se usa `freezegun` con `tick()` para avanzar el reloj y expirar la
key. Importante: `freeze_time` por defecto también congela `time.monotonic`,
que es lo que usa el loop de `asyncio` para sus timeouts (como el
`asyncio.wait_for` en `app/routes/chat.py`) — sin `real_asyncio=True` esto
puede colgar o romper el test. Se usa siempre `freeze_time(..., real_asyncio=True)`
en estos tests por esa razón.

## Cost tracking: SQLAlchemy async + Alembic, no `asyncpg` directo

Para Fase 5 se usó SQLAlchemy 2.0 (async, driver `asyncpg`) en vez de
`asyncpg` puro. Trade-off: `asyncpg` directo sería más liviano y algo más
rápido, pero las "migraciones simples" pedidas necesitarían SQL a mano
versionado manualmente. SQLAlchemy + Alembic da autogeneración de
migraciones a partir de los modelos (`alembic revision --autogenerate`), es
el estándar de facto en proyectos FastAPI, y no se pierde nada de async
porque `asyncpg` sigue siendo el driver por debajo. Para tests se usa
`aiosqlite` (SQLite en memoria) en vez de Postgres real — ver entrada
siguiente.

## Tabla de precios: valores de referencia, no tiempo real

`app/services/pricing.py` tiene un diccionario hardcodeado de USD por 1M
tokens. Precios consultados el 2026-09-11 en fuentes oficiales:
`gpt-4o-mini` $0.15/$0.60 y `gpt-4o` $2.50/$10.00 (input/output,
developers.openai.com/api/docs/pricing); `gemini-3.5-flash-lite`
$0.30/$2.50 y `gemini-3.5-flash` $1.50/$9.00 (ai.google.dev/gemini-api/docs/pricing).
Estos precios cambian con el tiempo y no hay ningún mecanismo que los
mantenga sincronizados — `estimated_cost_usd` es una estimación de
referencia para tener una noción de costo relativo entre providers, no una
cifra de facturación exacta. Un modelo sin precio cargado calcula costo 0
y loguea un `WARNING`, en vez de fallar.

## `BackgroundTasks` se pierden si el endpoint hace `raise HTTPException`

Verificado con código: si un endpoint de FastAPI agrega una tarea con
`background_tasks.add_task(...)` y después hace `raise HTTPException(...)`,
esa tarea **nunca se ejecuta** — Starlette solo corre las background tasks
adjuntas a la `Response` que efectivamente se envía, y una excepción no
lleva esas tasks consigo. Por eso, en `app/routes/chat.py`, el camino de
"ambos providers fallaron" ya no hace `raise HTTPException(...)`: devuelve
`JSONResponse(status_code=..., content={"detail": ...}, background=background_tasks)`
explícitamente, que sí ejecuta la tarea de `record_usage` adjunta. El
camino de éxito no necesitó este cambio: devolver el modelo Pydantic normal
(con `response_model` en el decorador) sí preserva las background tasks
acumuladas, se confirmó con código antes de asumirlo.

## No se trackean los `RoutingError` (modelo no reconocido)

Un request con un modelo que no matchea ningún prefijo conocido (`gpt-`,
`gemini-`) nunca llega a intentar ningún provider — no hay costo, tokens,
ni latencia de provider que registrar. Se decidió no crear una fila en
`usage_records` para este caso: es un error de input del cliente, no un
evento operacional de negocio. Lo mismo aplica (sin necesidad de
documentarlo aparte) a los 401/429 de auth/rate-limit, que ocurren en
dependencies antes de llegar al cuerpo del endpoint.

## Tests de cost tracking con SQLite en memoria, no Postgres real

Igual que con `fakeredis` en Fase 4, los tests de `test_usage_tracking.py`
y `test_usage_endpoint.py` reemplazan `async_session_factory` por un engine
`sqlite+aiosqlite:///:memory:` con `poolclass=StaticPool` (necesario:
sin esto, distintas sesiones del mismo engine pueden ver bases en memoria
separadas — es un problema documentado de SQLAlchemy+SQLite en memoria bajo
pooling). Se probó que el esquema y las queries agregadas (`COUNT`/`SUM`/
`AVG`/`COALESCE`/`CASE WHEN`) dan el mismo resultado en SQLite que en el
Postgres real antes de asumir que alcanzaba para testear. Esto evita
depender de Postgres corriendo en CI; la fidelidad con Postgres real se
verifica manualmente (ver sección de Verificación del plan de Fase 5), no
en el test suite automatizado.

**Bug real encontrado al verificar manualmente**: el redirect a SQLite se
había implementado solo en los dos archivos de test nuevos de esta fase.
Los tests preexistentes (`test_auth.py`, `test_chat_endpoint.py`,
`test_chat_fallback.py`, `test_rate_limit.py`) siguen mockeando el provider
y llegando a una respuesta exitosa, así que la background task de
`record_usage` corría igual — y como esos archivos no redirigían
`async_session_factory`, estaban escribiendo filas de test de verdad en el
Postgres real del desarrollador en cada corrida de `pytest`. Se detectó
inspeccionando la tabla real después de correr la suite. Fix: el fixture
de redirect a SQLite se movió a `tests/conftest.py` como `autouse=True`,
así aplica a **todos** los tests del proyecto sin que cada archivo nuevo
tenga que acordarse de configurarlo.

## `GET /v1/usage` autenticado por header, no `?api_key=`

Se reutiliza `verify_api_key` (Fase 4, sin modificarlo) vía el mismo header
`X-API-Key`, en vez de un query param como sugería el pedido original. Un
query param pondría una API key (un secreto) en logs de acceso, historial
del navegador y proxies intermedios — mal patrón para credenciales. La key
autenticada ya determina de quién es el usage a devolver.

## `/metrics`: dos ventanas (`last_60s` + `today`), sin `all_time`

Se expone `last_60s` (foto del minuto más reciente, puede dar todo en cero
sin tráfico) y `today` (agregados desde medianoche UTC — misma ventana que
"costo acumulado del día", una sola query reutilizada). Se descartó una
ventana `all_time`: en una tabla que crece para siempre, agregar sin límite
de fecha se vuelve cada vez más caro y cada vez menos representativo de
"cómo está el sistema hoy" — `today` ya cubre el caso de tráfico esporádico
sin ese costo creciente.

## `/metrics` sin auth (limitación temporal)

`GET /metrics` no está detrás de `verify_api_key`: es un endpoint
operacional/global, no asociado a un cliente particular, y Fase 4 solo
construyó auth de API key de cliente, no un tier de "admin"/operador.
Exponer costo agregado y tasas de error sin auth no es ideal para un
despliegue real — se documenta como limitación temporal, a resolver si
alguna vez se construye un tier de auth de operador (no está pedido todavía).

## Índice en `usage_records.created_at`

Se agregó `index=True` a esa columna (antes solo `api_key` estaba
indexada) porque `/metrics` hace `WHERE created_at >= :cutoff` en cada
llamada — sin índice, cada consulta escanea toda la tabla a medida que
crece. Migración aditiva (`CREATE INDEX`), no toca datos existentes.

## Comparaciones de fecha: siempre en UTC, verificado entre SQLite y Postgres

`func.now()` en SQLite devuelve un datetime naive (sin tzinfo); en Postgres
devuelve un datetime tz-aware en UTC. Se verificó con código que comparar
ambos casos contra un cutoff calculado con `datetime.now(timezone.utc)`
funciona correctamente en los dos dialectos, porque el `CURRENT_TIMESTAMP`
de SQLite ya es UTC internamente (solo le falta la etiqueta de tz). Regla
seguida en todo el código de `/metrics`: los cutoffs de fecha siempre se
calculan con `datetime.now(timezone.utc)`, nunca con hora local naive.

## `async_session_factory`: un solo punto de patch, no uno por módulo

Al agregar `metrics_service.py`, sus queries corrían contra el Postgres
real en los tests (y de paso rompían un segundo test por una conexión de
asyncpg atada a un event loop ya cerrado), porque `tests/conftest.py` solo
parcheaba `app.services.usage_service.async_session_factory` — el nombre
importado directamente en ese módulo, no en el nuevo. Fix: `usage_service.py`
y `metrics_service.py` ahora acceden vía `database.async_session_factory()`
(importando el módulo, no el nombre), y `conftest.py` parchea un solo lugar
canónico: `app.services.database.async_session_factory`. Cualquier módulo
nuevo que consulte la DB con este mismo patrón queda cubierto automáticamente,
sin tener que acordarse de tocar `conftest.py` de nuevo.
