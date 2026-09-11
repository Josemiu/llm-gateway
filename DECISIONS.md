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
