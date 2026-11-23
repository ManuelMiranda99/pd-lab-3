# Evidencia

Resultados de la evaluación, de la comparación de alternativas y capturas de MLflow. Todo con el
modelo entregado (`google/gemini-2.5-flash-lite`) salvo lo marcado como histórico. Cómo
reproducir cada resultado: [`README.md`](../README.md) §5.

## Lo principal

| Ruta | Qué es |
|---|---|
| [`checklist_entrega.md`](checklist_entrega.md) | Revisión requisito por requisito contra el enunciado y el Anexo, con dónde se cumple. |
| [`evaluacion_final/`](evaluacion_final/) | Evaluación final: stack de Docker Compose, **por HTTP**, 177 casos, `--concurrencia 4`, registrada en MLflow. |
| [`comparacion/`](comparacion/comparacion.md) | Comparación de 6 modelos y 2 arquitecturas en calidad, latencia y costo (respalda D49). |
| [`mlflow/`](mlflow/) | Capturas de MLflow (trazas y evaluación) con el modelo entregado. |
| [`resiliencia/`](resiliencia/) | Prueba con MLflow detenido y colgado en Docker Compose, más 422/404. |
| [`modelos_candidatos.md`](modelos_candidatos.md) | Modelos de OpenRouter con salida estructurada considerados (precios, proveedores). |

## `evaluacion_final/` (2026-11-23)

| Archivo | Contenido |
|---|---|
| `reporte.txt` | Reporte de la corrida `final_docker_flashlite` contra la API en Docker: **175/177 (98.9 %)**; dev 84/84, variantes 24/24, control 14/14, propios 53/55; p50 0.83 s, p95 1.08 s, máx 1.36 s. Los 2 fallos (P042, P051) son 503 por **429 de OpenRouter**, no errores de interpretación. |
| `resumen.json` | Mismos resultados en JSON (por tipo, archivo, calificador, latencias). |
| `reintento_429_reporte.txt`, `reintento_429_resumen.json` | P042 y P051 repetidos uno a uno contra la misma API: 2/2 exactos. |
| `simulacion_docente_reporte.txt`, `simulacion_docente_resumen.json` | Corrida hecha siguiendo el README al pie de la letra en una copia limpia del repo (sin `datos/` ni `.env` de trabajo): **177/177 (100 %)**, p50 0.82 s, p95 1.10 s, máx 1.65 s, 0 errores. |

Costo (etiqueta `llm.costo_usd` de las trazas, la API no lo expone): ≈ USD 0.034 la corrida
final (≈ USD 0.00019 por pedido) y ≈ USD 0.040 la de la simulación.

## `mlflow/`

| Archivo | Vista |
|---|---|
| `trazas_api_lista.png` | Experimento `novapedidos-api` → *Traces*: una traza por solicitud (entrada, salida, duración, estado, tokens, etiquetas), incluidas las 2 con 503. |
| `traza_solicitud_spans.png` | Una traza abierta: `interpretar_pedido` → `interprete_llm` (LangChain / `ChatOpenAI` con tokens y costo) → `resolucion_productos` → `motor_armar_pedido`, con entradas y salidas. |
| `evaluacion_runs.png` | Experimento `novapedidos-evaluacion` → *Evaluation runs* con la exactitud por conjunto. |
| `evaluacion_run_parametros_metricas.png` | Run `final_docker_flashlite`: métricas (exactitud por tipo, calificadores) y datos del run. |
| `evaluacion_por_caso_scorers.png` | Vista por caso del run de evaluación con los 11 *scorers* (`caso_exacto`, `estado`, `lineas`…). |
| `historico_nano/` | Las mismas vistas de la Fase 8 con el modelo de desarrollo `openai/gpt-4.1-nano` (históricas). |

## `comparacion/`

`comparacion.md` (método, tablas, lectura y lo que falló), `comparacion.csv` (una fila por
corrida), `alternativas.csv` (media por alternativa), `por_tipo_finalistas.json`,
`corridas/*.json` (resumen de cada corrida), gráficas `exactitud_vs_costo.png`,
`exactitud_vs_p95.png`, `exactitud_por_tipo_finalistas.png` y capturas de MLflow
`mlflow_runs_comparacion.png` y `mlflow_compare_runs.png`.

## `resiliencia/`

`resiliencia_docker_flashlite.txt`: salida de `scripts/probar_resiliencia_docker.py` con el modelo
entregado. Fases: A) MLflow arriba · B) `stop mlflow` · C) `start mlflow` · D) `pause mlflow`
(colgado) · E) `unpause` · F) 422/404. Resultado: todos los POST 200 (p95 ≤ 1.7 s) en todas las
fases; `/health` → `degradado` en 0.01 s (detenido) y 1.0 s (colgado), de vuelta a `ok` en
4.2 s y 1.0 s sin reiniciar la API; las trazas nuevas llegan a MLflow tras la recuperación;
5 solicitudes inválidas → 422 con motivo y cliente inexistente → 404.

## `sesiones/`

Evidencia de trabajo por sesión (`AAAA-MM-DD_FN`), en el orden del plan (`PLAN.md`): ping a
OpenRouter (F0), exploración de los datos (F1), `/docs` y respuestas de la API (F4), primeras
corridas del intérprete (F5), iteraciones de calidad (F6, F7), MLflow y resiliencia con nano
(F8), Docker Compose y clon limpio (F9). Son históricas: los resultados vigentes son los de
arriba.
