# Fase 6 · Evaluación local y mejora de calidad (2026-11-23)

Modelo: `openai/gpt-4.1-nano` (OpenRouter), en proceso, `--concurrencia 4`.
"Antes" = prompt y resolución de la Fase 5; "después" = con los cambios de `DECISIONES.md` D37.
Exactitud = `caso_exacto` (estado, líneas sku+cantidad, total ± 0.005, fecha, motivos,
pendientes con `texto` laxo y sustituciones; D33).

## Resultados

| Conjunto | Casos | Antes | Después | Latencia p50 / p95 (después) | Costo por caso |
|---|---:|---:|---:|---|---:|
| dev (`ejemplos/mensajes_dev.jsonl`) | 84 | 84/84 = 100 % | **84/84 = 100 %** | 1.47 s / 2.14 s | USD 0.000236 |
| variantes (`eval/casos_variantes.jsonl`, usadas para mejorar) | 24 | 20/24 = 83.3 % | **23/24 = 95.8 %** | 1.59 s / 2.10 s | USD 0.000228 |
| control (`eval/casos_control.jsonl`, corrido una vez por versión) | 14 | 14/14 = 100 % | **14/14 = 100 %** | 1.98 s / 4.99 s | USD 0.000231 |
| muestra por HTTP (`127.0.0.1:8001`, ambiguo+agotado+crédito de dev) | 18 | – | 18/18 = 100 % | 1.54 s / 2.24 s | – |

`integridad_precios` y `mensaje_cliente`: 100 % en todas las corridas (ningún precio,
descuento o subtotal distinto del catálogo; ningún mensaje promete agotados).

### Variantes por tipo (antes → después)

| Tipo | Antes | Después |
|---|---:|---:|
| agotado | 1/2 | 1/2 |
| ambiguo | 5/5 | 5/5 |
| audio | 1/1 | 1/1 |
| correccion | 2/2 | 2/2 |
| fuera_de_alcance | 2/2 | 2/2 |
| habitual | 1/3 | 3/3 |
| manipulacion | 1/1 | 1/1 |
| no_existe | 1/1 | 1/1 |
| simple | 2/2 | 2/2 |
| sin_cantidad | 1/1 | 1/1 |
| unidades | 2/3 | 3/3 |
| varios_mensajes | 1/1 | 1/1 |

### Iteraciones (4 completas)

| Iteración | Cambio | dev | variantes |
|---|---|---:|---:|
| 0 (antes) | configuración de la Fase 5 | 84/84 | 20/24 |
| 1 | prompt general + `anclar_cantidades` | 78/84 | 23/24 |
| 2 | números con punto, sin cruzar líneas, guarda de unidad suelta local, empaque en el nombre, desambiguación por contexto | 83/84 | 23/24 |
| 3 | no desambiguar si el texto aparece varias veces | 84/84 | 23/24 |
| 4 (después) | producto inventado → `no_existe` | 84/84 | 23/24 |

Reportes completos: `dev_antes.txt`, `dev_despues.txt`, `variantes_antes.txt`,
`variantes_despues.txt`, `control_antes.txt`, `control_despues.txt`, `dev_http_muestra.txt` e
`iteraciones/`.

## Fallos que siguen sin resolver

- **V10** (`agotado`): "3 fardos de harina de maíz, una libra de queso y 2 de café
  instantáneo". El modelo pierde "queso" (lo omite, lo reemplaza por "pasta" o inventa "pasta
  dental") y parte "café instantáneo". Estado, líneas, total y fecha salen bien; falla el
  pendiente `producto_no_existe`. La guarda de producto inventado evita entregar algo que el
  cliente no pidió, pero no recupera lo omitido. Candidato a resolverse con un modelo más capaz
  (Fase 10).
- Variabilidad: con `temperature=0` el modelo no es del todo determinista; en la iteración 1
  cambiaron salidas de dev que antes pasaban (D072, D074). Las guardas nuevas cubren esos
  patrones, pero conviene repetir la corrida final en la Fase 10.
- Latencia: con 4 casos en paralelo la cola sube (p95 hasta 4.6–5.0 s en algunas corridas; 1–2
  casos por encima del umbral de 5 s). Para medir latencia de producción usar `--concurrencia 1`.

## Lectura del sobreajuste

- Dev ya estaba en 100 %; mantenerlo no dice nada nuevo.
- En paráfrasis nuevas el sistema de la Fase 5 sacaba 34/38 (variantes + control = 89 %); los
  fallos eran de "lo de siempre" dicho de otra forma, docenas multiplicadas, cantidades
  perdidas y productos inventados u omitidos.
- Después: 37/38 (97 %), pero las variantes se usaron para mejorar. El control (14 casos
  escritos después) dio 14/14 con las dos versiones: confirma que no hubo regresión, pero es
  pequeño y fácil. La medición sin sesgo queda para los casos propios de la Fase 7.

## Gasto

Costo reportado por OpenRouter en las corridas en proceso: **USD 0.134** (12 corridas, 568
llamadas) + muestra HTTP (~18 llamadas, ≈ USD 0.004, la API no expone el costo) ≈ **USD 0.14**
de USD 0.75 del límite de la fase.

## Cómo reproducir

```bash
uv run python eval/variantes.py                      # regenera variantes y control (con datos/)
uv run python eval/correr.py --marca dev             # dev en proceso (4 hilos)
uv run python eval/correr.py eval/casos_variantes.jsonl eval/casos_control.jsonl --marca gen
uv run uvicorn novapedidos.api.main:app --host 127.0.0.1 --port 8001 &   # API local
uv run python eval/correr.py --url http://127.0.0.1:8001 --marca dev_http
uv run python eval/correr.py --modelo openai/gpt-4.1-mini --marca mini_dev   # otro modelo
```

Resultados en `eval/resultados/<marca>/{resultados.jsonl, resumen.json, reporte.txt}`.
