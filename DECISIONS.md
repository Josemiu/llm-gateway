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
