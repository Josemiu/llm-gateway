# Load Testing — resultados reales

Resultados de correr los 4 escenarios de `load-tests/` contra el gateway real
(`docker compose up`, sin mocks a nivel de infraestructura) el 2026-09-23.
No son benchmarks universales: dependen del hardware, de Docker Desktop y de
que **el provider real está reemplazado por `MockProvider`** (ver más abajo
por qué). Los números sirven para entender el comportamiento del gateway
bajo carga, no como cifra de marketing.

## Por qué `MockProvider` en vez de OpenAI/Gemini reales

El objetivo del load testing es medir el overhead propio del gateway (auth,
rate limiting, routing, fallback, background task de cost tracking) — no la
latencia o los límites de OpenAI/Gemini, que son variables externas fuera de
control. Usar providers reales además tenía dos problemas concretos
encontrados al planear esta etapa:

- La API key de OpenAI configurada en este proyecto **no tiene créditos**
  (verificado con una llamada real: `insufficient_quota` /
  `credit_balance_exhausted`, HTTP 429). Cualquier escenario que dependiera
  de OpenAI real habría fallado al 100% por un motivo de billing, no de
  comportamiento del gateway.
- Aunque OpenAI tuviera crédito, los escenarios B (hasta 250 VUs) y D
  (fallo forzado y repetido) habrían generado miles de requests de pago o
  chocado con los rate limits propios de los providers, que no son lo que
  se está evaluando acá.

Se agregó `app/providers/mock_provider.py`, activado solo con
`LOAD_TEST_MODE=true` (`app/config.py`, default `false` — no afecta
dev/producción). `MockProvider` simula latencia realista y puede forzar el
fallo de un provider (`MOCK_PROVIDER_FAIL`). La latencia simulada
(200-1500ms) se calibró con 3 llamadas reales a Gemini hechas antes de
correr los tests (1076ms, 1401ms, 1076ms; avg 1245ms) — no es un número
inventado, aunque tampoco es una medición de OpenAI real (esa medición no
fue posible por el problema de créditos arriba). Ver `DECISIONS.md` para el
detalle completo de esta decisión.

**Importante**: los `estimated_cost_usd` que aparecen en `/metrics` y
`/v1/usage` durante estas corridas son artefactos del mock (token counts
sintéticos pasando por la tabla de precios real) — no reflejan gasto real,
porque no se hizo ninguna llamada real a OpenAI/Gemini durante los tests de
carga.

## Entorno

| | |
|---|---|
| CPU | Intel Core i5-1135G7 (11th gen), 4 cores / 8 threads |
| RAM | 11.7 GB físicos |
| OS | Windows 11 Home 64-bit |
| Docker | Docker Desktop 29.0.1, engine con 8 CPUs / ~5.6 GiB asignados |
| Red | localhost (sin red externa real — k6 corre en un contenedor Docker separado, contra el gateway vía `host.docker.internal`) |
| Provider | `MockProvider` (ver arriba), no OpenAI/Gemini reales |
| Modelos | `gpt-4o-mini` (mock) / `gemini-3.5-flash-lite` (mock) |
| Fecha | 2026-09-23 |

Correr esto en otra máquina, con Docker con más/menos recursos asignados, o
en un entorno cloud real, va a dar números distintos.

## Cómo se corrió

```bash
docker compose up -d --build          # stack completo: postgres, redis, migrate, gateway

# .env.docker con LOAD_TEST_MODE=true (+ RATE_LIMIT_PER_MINUTE alto para A/B/D,
# el default 20 para C, y MOCK_PROVIDER_FAIL=openai solo para D) - ver detalle
# de cada escenario abajo. Tras cada cambio: `docker compose up -d gateway`
# para que tome el nuevo env.

docker run --rm -v "$(pwd)/load-tests:/scripts" grafana/k6 run \
  --summary-export=/scripts/_out.json \
  -e BASE_URL=http://host.docker.internal:8000 \
  -e API_KEY=<key> \
  /scripts/scenario-X.js
```

Los JSON crudos de cada corrida están en `results/`.

## Resultados

### Escenario A — tráfico normal

5 VUs, 1 minuto, 1-3s de "think time" entre requests, mezcla de prompts
simples (→ Gemini) y complejos (→ OpenAI) vía `model: "auto"`.

| Métrica | Valor |
|---|---|
| Requests totales | 110 |
| Error rate | 0% |
| p50 | 864ms |
| p90 | 1.39s |
| p95 | 1.46s |
| max | 1.5s |
| Throughput | 1.75 req/s |

Todos los checks pasaron (`status is 200`, `has content`). La latencia está
dominada por el sleep simulado de `MockProvider` (200-1500ms) más el
overhead real del gateway.

### Escenario B — rampa de concurrencia (10 → 50 → 100 → 250 VUs)

Sin think time, 30s por escalón + 15s de bajada.
`RATE_LIMIT_PER_MINUTE` elevado (100000) para este escenario específicamente
— el objetivo es medir la capacidad de manejo de concurrencia del gateway
en sí, no el rate limiter (eso es el escenario C).

| Métrica | Valor |
|---|---|
| Requests totales | 11,773 |
| Error rate | 0% |
| Throughput pico | ~86 req/s |
| p50 | 886ms |
| p90 | 1.41s |
| p95 | 1.48s |
| max | 1.75s |
| VUs máximos alcanzados | 250 |

0% de errores incluso en el pico de 250 VUs concurrentes — con la latencia
dominada igual por el sleep simulado del mock, no se observó degradación de
p95 al escalar de 10 a 250 VUs (1.46s en A con 5 VUs vs 1.48s en B con 250).
Esto dice que, con esta carga, el cuello de botella no es el gateway
(FastAPI + Redis + Postgres async) sino la latencia simulada del provider —
lo cual es exactamente el resultado esperado dado que el mock impone un
piso de 200-1500ms por request independientemente de la concurrencia.

### Escenario C — rate limiting

1 VU, 40 requests consecutivas, misma API key (`devkey2`, sin uso previo),
`RATE_LIMIT_PER_MINUTE=20` (default real).

| Métrica | Valor |
|---|---|
| Requests aceptadas (200) | 20 |
| Requests rechazadas (429) | 20 |
| Rate-limit rejection rate | 50.00% (20/40) |

Resultado exacto: las primeras 20 pasan, las siguientes 20 se rechazan con
429 — confirma que el fixed window counter (`INCR`+`EXPIRE` en Redis)
funciona como está documentado en `DECISIONS.md`. (Un primer intento con
`devkey1` dio 100% de rechazo porque esa key ya había acumulado miles de
requests durante el Escenario B, corrido minutos antes con el límite
elevado — el contador de Redis seguía vivo dentro de su ventana. No es un
bug: es el fixed window counter comportándose como se documentó
[limitación conocida de ráfagas en el borde de ventana]. Se repitió con una
key sin uso previo para aislar la medición.)

### Escenario D — fallo de provider / fallback

5 VUs, 20s, `model: "gpt-4o-mini"` explícito, `MOCK_PROVIDER_FAIL=openai`
(el mock de OpenAI falla con 503 en cada llamada).

| Métrica | Valor |
|---|---|
| Requests totales | 119 |
| Fallback exitoso | 100% (119/119) |
| Error rate | 0% |

Cada request forzada a fallar en OpenAI recibió una respuesta 200 exitosa
del fallback (Gemini) — confirmado revisando que `response.model ==
"gemini-3.5-flash-lite"` en cada caso. Cruzado con `GET /metrics`
inmediatamente después de la corrida: `last_60s.fallback_rate = 0.9225`
(92.25%, refleja el tráfico reciente dominado por este escenario;
`today.fallback_rate` da mucho más bajo porque promedia con los ~47k
requests acumulados de los escenarios A/B corridos antes sobre la misma
key) — ver `results/metrics_after_all_scenarios.json` y
`results/usage_devkey1_after_scenario_d.json`.

## Qué NO dicen estos resultados

- No miden la latencia real de OpenAI/Gemini (ver sección de arriba).
- No miden comportamiento bajo rate limiting real de los providers.
- No son representativos de un despliegue en un servidor cloud con recursos
  fijos garantizados (acá compiten con el resto de procesos de una laptop
  de desarrollo).
- El escenario B no encontró un techo de capacidad del gateway — con esta
  carga (piso de latencia del mock ~200-1500ms) nunca se llegó a saturar
  Postgres/Redis/FastAPI. Encontrar el techo real requeriría remover el
  sleep simulado o escalar VUs mucho más allá de 250, lo cual queda fuera
  del alcance de esta etapa.
