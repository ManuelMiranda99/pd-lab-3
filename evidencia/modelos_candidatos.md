# Modelos candidatos de OpenRouter

Consulta: `GET https://openrouter.ai/api/v1/models` (lista pública, sin clave) el **2026-11-22**.
Filtro: `supported_parameters` incluye `structured_outputs` (o `response_format`/`tools`),
excluyendo variantes `:free` (límites de tasa) y `:batch` (no sirven para una API en línea).
Precios en USD por millón de tokens.

## Baratos (desarrollo y pruebas)

| Modelo | Entrada | Salida | `structured_outputs` | Razonamiento | Notas |
|---|---:|---:|:---:|:---:|---|
| **`openai/gpt-4.1-nano`** (elegido) | 0.10 | 0.40 | sí | no | Rápido, sin razonamiento (baja latencia), JSON Schema estricto, proveedor estable. Contexto 1M. |
| `google/gemini-2.5-flash-lite` | 0.10 | 0.40 | sí | opcional | Muy rápido; alternativa de otro proveedor con el mismo precio. |
| `mistralai/mistral-small-3.2-24b-instruct` | 0.075 | 0.20 | sí | no | El más barato de los estables; buen español. |
| `openai/gpt-4o-mini` | 0.15 | 0.60 | sí | no | Reserva conocida y muy estable. |

## Recomendados (comparar en la Fase 10)

| Modelo | Entrada | Salida | `structured_outputs` | Razonamiento | Notas |
|---|---:|---:|:---:|:---:|---|
| `openai/gpt-4.1-mini` | 0.40 | 1.60 | sí | no | Misma familia que el barato, más capaz; latencia baja. |
| `google/gemini-2.5-flash` | 0.30 | 2.50 | sí | opcional | Buen balance calidad/precio; vigilar latencia si razona. |
| `anthropic/claude-haiku-4.5` | 1.00 | 5.00 | sí | opcional | Más capaz en instrucciones y en resistir manipulación; más caro. |
| `openai/gpt-5.4-mini` | 0.75 | 4.50 | sí | sí | Opción con razonamiento; medir p95 de latencia antes de elegirlo. |

## Criterio

- El evaluador usa **el modelo de `.env.example`** con su propia clave: debe ser estable, barato,
  con salida estructurada nativa y latencia baja (se mide). Por eso se prefieren modelos sin
  razonamiento obligatorio.
- El LLM solo extrae menciones; los cálculos los hace el motor, así que un modelo pequeño
  debería bastar. La Fase 10 lo confirma comparando exactitud, latencia p95 y costo con los
  casos de `ejemplos/mensajes_dev.jsonl` y los casos propios.
- Precios y disponibilidad cambian: re-consultar la lista antes de la Fase 10.
