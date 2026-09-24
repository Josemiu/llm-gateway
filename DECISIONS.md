## Tool/function calling en `/v1/chat/completions`

`llm-gateway` se declaró "cerrado" en su Etapa 5, pero un consumidor real
nuevo (`agent-platform`, Fase 3: un agente que decide cuándo usar una tool)
necesitaba tool-calling real, que el gateway no soportaba - `ChatMessage`
solo tenía `content: str`, sin manera de representar una decisión de
"llamar a esta función" ni su resultado. Se evaluó resolver esto del lado
de `agent-platform` con un truco de prompting (pedirle al modelo que
responda en JSON estructurado y parsearlo a mano), pero se descartó: no es
el patrón estándar, es menos confiable que tool-calling nativo, y muy
probablemente habría que tirarlo al llegar a la Fase 4 de `agent-platform`
(LangGraph), que sí espera tool-calling real. Se decidió extender el
gateway en su lugar - es exactamente el tipo de evolución que la
arquitectura del portfolio anticipaba ("el gateway sirve a consumidores
reales", no una excusa para reabrir el proyecto sin motivo).

**Schema** (`app/schemas/chat.py`): `ChatMessage` gana `role: "tool"` (antes
solo system/user/assistant), más `tool_calls` (en un mensaje de assistant
que decide llamar una función) y `tool_call_id` (en el mensaje "tool" que
trae el resultado, correlacionado por id). `ChatCompletionRequest` gana
`tools: list[Tool] | None`. `ChatCompletionResponse.content` pasa a ser
opcional (`str | None`) porque una respuesta que solo llama a una tool no
tiene texto. El formato de `Tool`/`ToolCall` sigue la convención de OpenAI
(`arguments` como string JSON, no dict parseado) por ser el más extendido -
`GeminiProvider` traduce hacia/desde el formato nativo de Gemini
internamente; el consumidor (`agent-platform`) nunca ve esa diferencia.

**`ToolCall.provider_data: dict | None`**: campo opaco para datos que un
provider necesita hacer viajar de ida y vuelta en la conversación sin que
signifiquen nada para el schema común. Existe por un hallazgo real
verificado en vivo: los modelos Gemini 3.x ("thinking") devuelven un
`thought_signature` binario en cada parte de `function_call`, y **rechazan
con 400 INVALID_ARGUMENT** la siguiente vuelta de la conversación si ese
`thought_signature` no se reenvía intacto en esa misma parte (confirmado
haciendo una llamada real de dos turnos antes de asumir que el diseño
funcionaba - la primera versión sin este campo fallaba de verdad en el
segundo turno). OpenAI no tiene este concepto y nunca setea ni lee
`provider_data` - el campo es puramente aditivo para quien no lo necesita.

**Verificado en vivo con Gemini real** (no solo con SDK mockeado): un
diálogo completo de dos turnos (pregunta → el modelo pide `get_weather` →
se le devuelve un resultado inventado → responde en lenguaje natural
usándolo) funcionó de punta a punta contra la API real, incluyendo a
través del endpoint HTTP completo (`/v1/chat/completions`), no solo
llamando al provider directamente. No se pudo hacer la misma verificación
en vivo contra OpenAI real por el problema ya documentado de créditos - esa
implementación se hizo contra la documentación oficial (API de tool calling
extremadamente estable y bien documentada) y se cubrió con tests
mockeados equivalentes a los de Gemini.

**Compatibilidad hacia atrás**: los 48 tests existentes antes de este
cambio siguen pasando sin modificarlos - `tools` es opcional en el request,
`tool_calls`/`provider_data` son opcionales en el schema, y
`LLMProvider.generate()` gana `tools: list[Tool] | None = None` con default,
no rompe ningún call site existente.

## Bug real: migración `8d2dcb772e99` rompía `docker compose up` desde cero

Durante el cierre del proyecto (Etapa 5) se verificó `docker compose up`
desde un estado realmente limpio (`docker compose down -v`, volumen de
Postgres borrado) por primera vez desde la Fase 5. El servicio `migrate`
falló: `alembic.exceptions... asyncpg.exceptions.UndefinedTableError: table
"t" does not exist`. Causa: la migración `8d2dcb772e99_index_usage_records_created_at.py`
(autogenerada en la Fase 5 al agregar el índice en `created_at`) incluía
`op.drop_table('t')` - una tabla `t` que nunca fue parte de ningún modelo ni
de la migración inicial (`88c2522ef01f`). Era ruido de `alembic revision
--autogenerate` comparando contra una base de desarrollo que en ese momento
tenía una tabla `t` suelta (probablemente un experimento manual del
desarrollador, nunca versionado) - Alembic la interpretó como "esto no
debería existir" y generó un `DROP TABLE` para ella. En el Postgres de
desarrollo usado hasta ahora, esa tabla `t` efectivamente existía por
casualidad, así que `alembic upgrade head` "funcionaba" - pero nunca se
había probado contra una base realmente nueva (como la vería cualquiera
clonando el repo), donde el `DROP TABLE t` falla porque la tabla no existe.

Fix: se removieron `op.drop_table('t')` del `upgrade()` y su contraparte
`op.create_table('t', ...)` del `downgrade()` en esa migración - lo único
que esa migración debe hacer es crear el índice (y en `downgrade`,
borrarlo). Se optó por editar la migración histórica directamente, en vez
de agregar una migración nueva que corrija la anterior: este es un proyecto
de portfolio con una sola instancia de Postgres de desarrollo (nunca
desplegado en ningún otro entorno), así que no hay una base de datos real
ya migrada con el bug que se rompería al reescribir el historial - el costo
de una migración "parche" extra para corregir un artefacto de autogenerate
que nunca debió existir no se justificaba. Verificado corriendo
`docker compose up -d --build` desde cero después del fix: las 3
migraciones (`88c2522ef01f` → `8d2dcb772e99` → `7ead1fbc7c58`) corren limpio,
exit code 0.

## OpenTelemetry: spans manuales por capa + auto-instrumentación de FastAPI, Jaeger vía OTLP

Antes de esta etapa, `/metrics` solo daba latencia end-to-end agregada - no
había forma de ver, para un request lento en particular, si el tiempo se iba
en rate limiting, en la query de stats del routing adaptativo, en la llamada
al provider o en el write de cost tracking. Ese es el problema concreto que
resuelve el tracing, no una tecnología agregada porque sí.

Diseño: `FastAPIInstrumentor.instrument_app(app)` (paquete
`opentelemetry-instrumentation-fastapi`) da el span raíz por request
automáticamente - no tiene sentido reimplementar eso a mano. Sobre eso, se
agregaron spans manuales (`tracer.start_as_current_span(...)`) en cada capa
pedida: `auth.verify_api_key`, `rate_limit.enforce`, `routing.select_provider`
(con `db.get_provider_stats` anidado adentro, porque el routing adaptativo
consulta la DB), `provider.generate` (en `_generate_with_timeout` de
`chat.py` - un solo punto cubre OpenAI/Gemini/Mock sin duplicar el span en
cada provider), y `db.record_usage`.

**Exportador**: OTLP sobre HTTP (`opentelemetry-exporter-otlp-proto-http`) a
Jaeger, en vez del exportador Jaeger nativo (deprecado en favor de OTLP
desde hace tiempo - Jaeger acepta OTLP directamente desde la v1.35). Un solo
backend, como pedía el objetivo original (no Jaeger + Tempo a la vez).
Imagen fijada a `jaegertracing/all-in-one:1.76.0` (confirmada real contra
Docker Hub, no adivinada) - nuevo servicio en `docker-compose.yml`, UI en
`localhost:16686`.

**`settings.otel_enabled` (default `false`)**: `app/telemetry.py` expone un
`tracer` global que cualquier módulo puede importar y usar sin condicionales
propios - sin un `TracerProvider` configurado, la API de OpenTelemetry cae
sola a un tracer no-op (sin overhead real, sin intentar exportar nada). Solo
`setup_tracing()` en `app/main.py` es condicional: configura el exportador
real y instrumenta FastAPI únicamente si `OTEL_ENABLED=true`. Esto evita que
los 48 tests o un `uvicorn --reload` local sin Jaeger corriendo intenten
conectarse a un exportador inexistente - cero cambios de comportamiento por
default. En `.env.docker.example` queda `true` (Jaeger ya es parte del stack
completo de Docker); en `.env.example` (dev local sin Docker) queda `false`.

**Verificado en vivo, no solo "debería andar"**: con el stack completo en
Docker (`OTEL_ENABLED=true`), un request real a `/v1/chat/completions`
generó un trace consultado directo contra la API de Jaeger
(`GET /api/traces?service=llm-gateway`) con la jerarquía exacta esperada:
`POST /v1/chat/completions` (raíz, auto-instrumentado) → `auth.verify_api_key`,
`rate_limit.enforce`, `routing.select_provider` (con `db.get_provider_stats`
anidado), `provider.generate`, y - el caso que generaba más dudas, por
tratarse de una `BackgroundTask` que corre después de enviada la respuesta -
`db.record_usage`, que efectivamente aparece anidado bajo el span
`BackgroundTask record_usage` que `FastAPIInstrumentor` genera automáticamente,
sin quedar como un trace huérfano.

## Routing adaptativo: reliability → latency → costo (default heurístico)

Para que `model: "auto"` reaccione a la salud real de cada provider (no solo
al contenido del mensaje), se agregó una capa sobre la heurística existente
de `app/routing/selector.py` (`_is_complex`, sin tocar) en vez de
reemplazarla: la heurística sigue eligiendo un candidato por complejidad del
prompt exactamente como antes; `_apply_adaptive_policy` puede *overridear*
ese candidato con datos reales de `provider_stats_service.get_provider_stats()`
(ventana configurable, default `ROUTING_STATS_WINDOW_MINUTES=60`, tomado del
ejemplo "last 1 hour" del pedido original). Solo aplica a `model: "auto"`
- un modelo explícito (`model: "gpt-4o-mini"`) nunca pasa por esta lógica,
igual que antes.

Política, en orden (sin pesos combinados en un solo score - cada capa decide
independientemente si hay evidencia suficiente para actuar):

1. **Reliability**: si el candidato de la heurística tiene `error_rate` real
   por encima de `ROUTING_ERROR_RATE_THRESHOLD` (default 0.5 - un provider
   que falla más de la mitad de sus intentos recientes es más probable que
   falle de nuevo que no) y el otro provider no está igual de mal, se
   cambia al otro. Si ambos están mal, se mantiene el candidato original -
   no hay a dónde más ir, y el fallback reactivo por request (Fase 3, sin
   tocar) sigue cubriendo ese caso igual que siempre.
2. **Latency**: si ambos providers están sanos (ver `MIN_SAMPLES` abajo) pero
   el candidato es más de `ROUTING_LATENCY_DEGRADATION_MULTIPLIER` veces
   (default 2.0x) más lento que el otro en promedio, se cambia al otro.
   Se usa un multiplicador relativo, no un umbral fijo en ms: un número
   absoluto (ej. "500ms") no tiene forma de justificarse sin datos de
   producción reales, mientras que "2x más lento que la alternativa" es
   auto-calibrado al entorno real y no depende de qué tan rápido sea el
   proveedor en general.
3. **Costo**: si ninguna de las dos capas anteriores actúa, se mantiene el
   candidato de la heurística original - que ya encima la decisión de costo
   (prompts simples → Gemini, más barato; prompts complejos → OpenAI, más
   capaz). No hace falta una tercera regla explícita de costo porque ese
   trade-off ya está calculado en la heurística existente.

**`ROUTING_MIN_SAMPLES` (default 20)**: por debajo de este número de intentos
en la ventana, las stats de un provider se tratan como `UNKNOWN` (ni sano ni
no confiable) y nunca activan un override. Con pocas muestras el error rate
es ruido puro - 1 fallo sobre 2 intentos ya "parece" 50% de error rate sin
significar nada. 20 es un punto de partida conservador sin tuning con datos
reales todavía (mismo espíritu que el umbral de la heurística de
complejidad), documentado para poder ajustarlo cuando haya tráfico real.

**No es un circuit breaker**: a diferencia de un breaker clásico
(open/half-open/closed, con cooldown y estado en memoria por proceso), esta
capa no guarda ningún estado propio - en cada decisión de routing hace una
query fresca a Postgres (que ya es compartido entre los workers de uvicorn,
a diferencia de un estado en memoria). Esto es justo lo que la entrada
"Fallback simple, sin circuit breaker" de más abajo señalaba como el motivo
para no meter un breaker con estado en Fase 3 (Redis no existía todavía, y
un breaker en memoria no sería confiable con múltiples workers) - acá el
problema de estado compartido entre workers directamente no existe, porque
no hay estado: es solo lectura de datos ya persistidos.

`select_provider()` pasó a ser `async` (antes era sync) porque necesita
consultar la DB para el caso `"auto"`. Único call site: `app/routes/chat.py`.

**Verificado en vivo** (no solo con tests unitarios): con `LOAD_TEST_MODE=true`
y `MOCK_PROVIDER_FAIL=gemini` contra el stack real en Docker, tras ~24
requests fallidas de Gemini en la ventana, el log mostró
`Adaptive routing: 'gemini' error rate too high recently, preferring 'openai'`
y la siguiente request fue directo a OpenAI sin intentar Gemini primero (sin
el WARNING de "Primary provider failed" que aparece en el fallback
reactivo). Nota de la verificación: el primer intento, con la ventana
default de 60 minutos, no disparó el override porque el dev DB tenía
~23,800 filas de Gemini exitosas de los load tests de la Etapa 2 corridos
minutos antes dentro de esa misma ventana, diluyendo el error rate real
(0.11% en vez de ~100%) - no es un bug del routing, es la ventana haciendo
exactamente lo que tiene que hacer (promediar sobre datos reales); se repitió
con `ROUTING_STATS_WINDOW_MINUTES=2` para aislar la verificación de ese
ruido de datos de desarrollo.

## Bug real: `usage_records` no registraba el fallo del provider primario

Al diseñar el routing adaptativo (necesita error rate real por provider) se
encontró que `app/routes/chat.py` solo grababa **una** fila de
`usage_records` por request HTTP, atribuida siempre al provider que produjo
el resultado final (el fallback, si hubo uno). El fallo del provider
*primario* en un request que terminó en fallback - exitoso o no - nunca
quedaba registrado bajo el nombre del primario. Consecuencia real: una query
del tipo `WHERE provider = 'openai' AND status = 'error'` solo capturaba los
casos en que OpenAI fallaba actuando como *fallback*, no como primario - que
es exactamente el escenario que el routing adaptativo necesita detectar
("¿debería intentar este provider primero?"). Esto también afectaba (de
forma más sutil) la interpretación de `/v1/usage` y `/metrics`, aunque sus
totales de requests seguían siendo correctos porque siempre hubo exactamente
una fila por request.

Fix: se agregó `usage_records.is_final_attempt: bool` (default `true`,
migración `7ead1fbc7c58`, aditiva). `chat.py` ahora graba una fila adicional
con `is_final_attempt=False` para el intento fallido del primario, además de
la fila de siempre para el resultado final. `usage_service.get_usage_summary`
y `metrics_service._aggregate_since` agregan `WHERE is_final_attempt = true`
para seguir significando exactamente lo mismo que antes ("1 fila = 1
respuesta al cliente"); solo `provider_stats_service` (nuevo, para routing
adaptativo) lee todas las filas, sin ese filtro.

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
