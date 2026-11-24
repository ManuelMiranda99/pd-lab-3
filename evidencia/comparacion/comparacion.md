# Comparación de alternativas (Fase 10, 2026-11-23)

**Pregunta:** ¿qué modelo de OpenRouter (y qué arquitectura) se entrega en `.env.example`, sabiendo
que el evaluador usa **ese** modelo con **su** clave y mide calidad (estado, líneas, total,
fecha, motivos), que ningún mensaje altere precios y la latencia?

**Respuesta:** `google/gemini-2.5-flash-lite` con la resolución en código (incluido el ajuste
D49). 177/177 casos en dos repeticiones, p95 ≈ 1.1–1.2 s, USD 0.0002 por pedido. Decisión y
criterio en `DECISIONES.md` D49.

## Método

- **Mismo dataset y mismo código** para todas: 177 casos = dev (84) + variantes (24) + control
  (14) + propios (55, de ellos 12 de inyección). Evaluación en proceso
  (`eval/correr.py --modelo M --variante V --repeticion N --mlflow`), `--concurrencia 4`,
  `temperature=0`, una llamada por pedido con salida estructurada `json_schema` estricta.
- **Sondeo** de 12 casos por modelo antes de la corrida completa (runs `f10_sondeo_*`): los 6
  modelos aceptaron el esquema estricto (enum de sku) sin cambios; ninguno necesitó
  `function_calling`.
- **2 repeticiones** por modelo (Haiku: 1, por costo; ver "Lo que falló").
- **Latencia "de producción"**: 40 casos repartidos en los 4 conjuntos, un pedido a la vez
  (`--concurrencia 1`) para los 3 finalistas.
- **Arquitectura**: nano con y sin la resolución determinista posterior al LLM
  (`--sin-resolucion`).
- Todas las corridas están en MLflow, experimento `novapedidos-comparacion` (servidor local
  temporal en 127.0.0.1:5001), con parámetros `modelo`, `variante`, `repeticion` y
  `razonamiento`. Las corridas inválidas quedaron con la etiqueta `valida=false`.
- Exactitud = `caso_exacto` (D33: estado, líneas, total, fecha, sustituciones, pendientes y
  motivos exactos). Costo = `usage.cost` de OpenRouter, por caso con llamada. Precios
  consultados en `GET https://openrouter.ai/api/v1/models` el 2026-11-23.

## Alternativas

| Alternativa | ID exacto | Entrada / salida / caché (USD por M) | Proveedor en OpenRouter | Configuración |
|---|---|---|---|---|
| nano (actual) | `openai/gpt-4.1-nano` | 0.10 / 0.40 / 0.03 | solo Azure | base |
| nano sin resolución | `openai/gpt-4.1-nano` | igual | Azure | `--sin-resolucion` |
| 4.1-mini | `openai/gpt-4.1-mini` | 0.40 / 1.60 / 0.10 | Azure, OpenAI | base |
| 2.5-flash | `google/gemini-2.5-flash` | 0.30 / 2.50 / 0.03 | Google AI Studio | `--razonamiento no` |
| 2.5-flash-lite | `google/gemini-2.5-flash-lite` | 0.10 / 0.40 / 0.01 | Google AI Studio | base (no razona por defecto) |
| mistral-small-3.2 | `mistralai/mistral-small-3.2-24b-instruct` | 0.075 / 0.20 / – | DeepInfra, Parasail, Venice | base |
| haiku-4.5 | `anthropic/claude-haiku-4.5` | 1.00 / 5.00 / 0.10 | Anthropic, Bedrock, Vertex, Azure | base |

Todas anuncian `structured_outputs` en el modelo y en cada endpoint.

## Resultados (177 casos, 4 en paralelo)

Media de las repeticiones; "±" = diferencia entre la mejor y la peor repetición.
Inyección = 12 casos `inyeccion` (propios) + 2 `manipulacion` (variantes/control).

| Alternativa | Reps | Exactos | Exactitud global | Propios | Inyección | Integridad de precios | p50 (s) | p95 (s) | máx (s) | USD por caso | USD por 1000 pedidos | Errores / 503 |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| nano | 2 | 175 / 175 | 98.9 % ± 0.0 | 98.2 % | 100 % | 100 % | 1.57 | 2.32 ± 0.28 | 4.2 / 15.3 | 0.000243 | 0.24 | 0 |
| nano sin resolución | 1 | 131 | **74.0 %** | 72.7 % | 78.6 % | 100 % | 1.59 | 3.25 | 4.5 | 0.000242 | 0.24 | 0 |
| 4.1-mini | 2 | 175 / 176 | 99.2 % ± 0.6 | 98.2 % | 100 % | 100 % | 2.45 | 3.74 ± 0.29 | 6.7 / 5.2 | 0.000923 | 0.92 | 0 |
| 2.5-flash (sin razonar) | 2 | 175 / 175 | 98.9 % ± 0.0 | 96.4 % | 100 % | 100 % | 1.13 | 1.58 ± 0.01 | 2.1 | 0.000689 | 0.69 | 0 |
| 2.5-flash-lite | 2 | 176 / 176 | 99.4 % ± 0.0 | 98.2 % | 100 % | 100 % | 0.79 | 1.07 ± 0.12 | 2.6 | 0.000202 | 0.20 | 0 |
| mistral-small-3.2 | 2 | 176 / 176 | 99.4 % ± 0.0 | 98.2 % | 100 % | 100 % | 2.78 | 5.16 ± 0.85 | 14.7 / 12.1 | 0.000499 | 0.50 | 0 |
| haiku-4.5 | 1 | 176 | 99.4 % | 98.2 % | 100 % | 100 % | 1.31 | 1.92 | 5.1 | 0.008668 | 8.67 | 0 |
| **2.5-flash-lite + D49** | 2 | **177 / 177** | **100 % ± 0.0** | **100 %** | 100 % | 100 % | **0.78** | **1.16 ± 0.05** | 4.0 / 2.0 | **0.000214** | **0.21** | 0 |
| nano + D49 | 1 | 176 | 99.4 % | 98.2 % | 100 % | 100 % | 1.53 | 2.31 | 4.4 | 0.000242 | 0.24 | 0 |

Fallos por alternativa (ids, por repetición): nano `D073 P028 | D073 P028`; 4.1-mini
`P029 V11 | P029`; 2.5-flash `P022 P029 | P022 P029`; 2.5-flash-lite, mistral y haiku `P029`;
nano + D49 `P028`; 2.5-flash-lite + D49 ninguno. Nano sin resolución: 46 fallos (lista en
`alternativas.csv`).

**Latencia "de producción"** (40 casos, un pedido a la vez):

| Finalista | Exactos | p50 (s) | p95 (s) | máx (s) | USD por caso |
|---|---|---:|---:|---:|---:|
| 2.5-flash-lite + D49 | 40/40 | 0.84 | 1.13 | 1.34 | 0.000181 |
| nano + D49 | 40/40 | 1.53 | 1.87 | 2.15 | 0.000243 |
| 2.5-flash (sin razonar) + D49 | 40/40 | 1.18 | 1.66 | 2.02 | 0.000973 |

### Gráficas

- `exactitud_vs_costo.png`: exactitud global contra costo por 1000 pedidos (escala log).
- `exactitud_vs_p95.png`: exactitud global contra p95 de latencia.
- `exactitud_por_tipo_finalistas.png`: exactitud por tipo de caso de los 3 finalistas.
- `mlflow_runs_comparacion.png`: lista de runs del experimento `novapedidos-comparacion`.
- `mlflow_compare_runs.png`: vista **Compare runs** de MLflow (8 runs, coordenadas paralelas
  con `marca`, `exactitud`, `latencia_p95_s` y `costo_por_caso_usd`). URL:
  `http://127.0.0.1:5001/#/compare-runs?runs=[<ids>]&experiments=["1"]`.

## Lectura

1. **La capa determinista es lo que más aporta.** Con el mismo modelo (nano), quitar la
   resolución en código baja la exactitud de 98.9 % a 74.0 % (inyección de 100 % a 78.6 %).
   Con ella, los seis modelos quedan entre 98.9 % y 99.4 %: el modelo importa menos que la
   arquitectura.
2. **Ningún modelo alteró precios.** Integridad de precios 100 % en todas las corridas,
   también en los 14 casos de inyección y manipulación (los precios los calcula el motor).
3. **Los fallos restantes eran casi todos el mismo caso** (P029, "dos quintales de arroz y un
   fardo de arroz ceiba de libra"): cinco de los seis modelos dejaron "arroz" como ambiguo
   porque la palabra aparece dos veces. Era un límite de la resolución, no del modelo: el
   ajuste D49 (ignorar la aparición que pertenece a otra mención más larga y resolver una
   corrección ambigua contra lo ya pedido, P022) es general y beneficia a todos los modelos.
   Tras D49: flash-lite 177/177 en dos repeticiones; nano 176/177 (falla P028, que nano falla a
   veces desde la Fase 8).
4. **Latencia:** 2.5-flash-lite es el más rápido (p50 0.8 s, p95 1.1–1.2 s, máx ≤ 4 s) con 4 en
   paralelo y uno a uno; nano duplica el p50 y tiene picos de Azure (15.3 s en una repetición).
   Mistral (proveedores de terceros, sin caché de prompt) tiene la peor cola (p95 5 s, máx
   12–15 s). 4.1-mini es 2× más lento que nano.
5. **Costo:** flash-lite y nano cuestan lo mismo (≈ USD 0.20–0.24 por 1000 pedidos, gracias a
   la caché de prompt); Haiku cuesta 40× más (Anthropic no reutiliza el prefijo sin
   `cache_control`) sin mejorar la exactitud.
6. **Variabilidad:** entre repeticiones la exactitud cambió como mucho un caso (mini V11). La
   latencia p95 varió 0.01–0.3 s, salvo mistral (0.85 s).

## Lo que falló durante la fase

- **Seis modelos a la vez (24 solicitudes en paralelo) → errores 429/`APIStatusError` de
  OpenRouter** en 14–64 casos de cada corrida (respuesta en 0.1–0.3 s, sin llegar al
  proveedor). Esas 6 corridas se marcaron `valida=false` (nombre `invalida_…` en MLflow,
  carpetas `eval/resultados/f10_invalida_*`) y se repitieron de a dos modelos (8 en
  paralelo), sin ningún error. Costaron USD 1.44 (Haiku 1.10). Para no repetir Haiku
  completo, su repetición 1 = los 127 casos sin error de esa corrida + los 50 restantes
  (`f10_haiku_complemento_r1`); la latencia de esos 127 se midió con más carga que el resto.
  Desde ahora el error de la llamada guarda el código HTTP (`APIStatusError(429)`).
- **Haiku con una sola repetición** por presupuesto (USD 1.53 por corrida completa).

## Gasto de la fase

Suma de `costo_total_usd` de los 30 runs: **USD 3.12** (límite USD 5.00): corridas válidas
USD 1.54, sondeos USD 0.14 y corridas inválidas USD 1.44. Saldo de la cuenta de OpenRouter al
cerrar: ≈ USD 1.3 de 5.

## Reproducir

```bash
uv run mlflow server --host 127.0.0.1 --port 5001 --backend-store-uri sqlite:///<tmp>/mlflow.db \
    --default-artifact-root <tmp>/artefactos
uv run python eval/correr.py ejemplos/mensajes_dev.jsonl eval/casos_variantes.jsonl \
    eval/casos_control.jsonl eval/casos_propios.jsonl --modelo google/gemini-2.5-flash-lite \
    --marca f10_flashlite_d49_r1 --variante resolucion_d49 --repeticion 1 --mlflow \
    --mlflow-uri http://127.0.0.1:5001 --mlflow-experimento novapedidos-comparacion
# (igual para cada modelo; 2.5-flash con --razonamiento no; nano con --sin-resolucion)
uv run --with matplotlib python scripts/comparar_alternativas.py   # CSV, gráficas y tabla
```

Archivos: `comparacion.csv` (una fila por corrida), `alternativas.csv` (media y diferencia por
alternativa), `por_tipo_finalistas.json`, `corridas/*.json` (resumen de cada corrida).
