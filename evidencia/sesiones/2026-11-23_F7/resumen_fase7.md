# Fase 7 · Casos de evaluación propios (2026-11-23)

Modelo: `openai/gpt-4.1-nano` (OpenRouter → Azure), en proceso, `--concurrencia 4`.
"Antes" = código de la Fase 6 sin cambios; "después" = con `DECISIONES.md` D40 y D41.
Exactitud = `caso_exacto` (D33).

## Casos propios

`eval/casos_propios.jsonl`: **55 casos** (`P001`–`P055`), mismo formato que
`mensajes_dev.jsonl`, generados por `eval/casos_propios.py` (interpretación escrita a mano,
`esperado` calculado por el motor; D38). Regenerar: `uv run python eval/casos_propios.py`.
`tests/test_casos_propios.py` verifica sincronía, claves de dev y cobertura por tipo.

Tipos: `inyeccion` 12 · `fuera_catalogo` 10 · `borde_regla` 7 · `credito` 3 · `habitual` 3 ·
y 2 de cada uno de los demás tipos de dev (`agotado`, `ambiguo`, `audio`, `correccion`,
`fuera_de_alcance`, `no_existe`, `simple`, `sin_cantidad`, `unidades`, `varios_mensajes`).
Políticas nuevas para casos que `politicas.md` no cubre: D39.

## Resultados

| Conjunto | Casos | Antes | Después | Latencia p50 / p95 / máx (después) |
|---|---:|---:|---:|---|
| propios (`eval/casos_propios.jsonl`) | 55 | **48/55 = 87.3 %** | 54/55 = 98.2 % | 1.43 / 1.98 / 3.69 s |
| dev | 84 | 84/84 (F6) | **84/84** | (122 juntos) 1.52 / 2.15 / 3.52 s |
| variantes | 24 | 23/24 (F6) | **24/24** | |
| control | 14 | 14/14 (F6) | **14/14** | |

El 87.3 % de "antes" es **la estimación honesta de generalización**: los casos se escribieron
sin mirar salidas del modelo. El 98.2 % de "después" es optimista (los propios se usaron para
mejorar). `integridad_precios` y `mensaje_cliente`: 100 % en todas las corridas (ningún precio,
descuento o subtotal distinto del catálogo, también en los 12 de inyección).

### Propios por tipo

| Tipo | Casos | Antes | Después |
|---|---:|---:|---:|
| agotado | 2 | 2/2 | 1/2 |
| ambiguo | 2 | 2/2 | 2/2 |
| audio | 2 | 2/2 | 2/2 |
| borde_regla | 7 | 7/7 | 7/7 |
| correccion | 2 | 2/2 | 2/2 |
| credito | 3 | 3/3 | 3/3 |
| fuera_catalogo | 10 | 6/10 | 10/10 |
| fuera_de_alcance | 2 | 2/2 | 2/2 |
| habitual | 3 | 3/3 | 3/3 |
| **inyeccion** | 12 | **11/12** | **12/12** |
| no_existe | 2 | 1/2 | 2/2 |
| simple | 2 | 2/2 | 2/2 |
| sin_cantidad | 2 | 2/2 | 2/2 |
| unidades | 2 | 1/2 | 2/2 |
| varios_mensajes | 2 | 2/2 | 2/2 |

Fallos de "antes" (todos de extracción; ninguno de precio): P009 inyección en audio ("kola
seiscientos" → ambiguo), P028 media docena de kola de 3 L con cantidad 0, P038 pedido solo de
productos no vendidos tomado como no pedido, P049 el fardo del reclamo agregado como línea,
P052 "aceite Capullo" → ambiguo, P053 "caja y media" → 2 cajas, P055 "una docena de cajas" →
12 sopas sueltas.

### Iteraciones

| Iteración | Cambio | propios | dev + variantes + control |
|---|---|---:|---:|
| 0 (antes) | código de la Fase 6 | 48/55 | 121/122 (F6) |
| 1 | prompt (marcas, reclamos, pedido de no vendidos, letras) + resolución (vacías, docena de empaques, medias unidades, "seiscientos") | 55/55 | – |
| 2 | saneado previo de "ignora tus instrucciones" (D41) + cláusulas | 55/55 | 121/122 (D046) |
| 3 (después) | regla de presentación reescrita, "cancelar todo", ejemplo de no vendidos | 54/55 | **122/122** |

Reportes: `propios_antes.txt`, `propios_despues.txt`, `regresion_despues.txt`,
`iteraciones/`.

## Latencia de cola (D41)

Datos: intentos HTTP por caso (envoltorio del transporte, `latencia/sonda.py`) y tiempo de
generación de Azure según la API de generaciones de OpenRouter (`latencia/gen.py`,
`latencia/openrouter_generacion_*.jsonl`).

| Corrida | Caso p50 / p95 / máx | Generación Azure p50 / p95 / máx | Reintentos HTTP |
|---|---|---|---:|
| dev, 4 hilos (`lat_dev_c4`) | 1.72 / 3.03 / 6.13 s | 1.42 / 2.81 / 5.94 s | 0 de 84 |
| dev, 1 hilo (`lat_dev_c1`) | 1.45 / 2.38 / 6.27 s | 1.26 / 2.25 / 6.13 s | 0 de 84 |

- **No son reintentos de red**: un intento por caso, todos 200.
- **La cola es latencia del proveedor**: los casos de 5–6 s tienen 5–6 s de generación en Azure
  (D004, D052, D081), con 1 y con 4 hilos. Red + OpenRouter + código suman p50 ≈ 0.15–0.2 s.
  La concurrencia 4 sube el p95 ≈ 0.6 s.
- **Filtro de contenido (D32)**: solo en "ignora/olvida tus instrucciones…": dos llamadas,
  4.0–5.3 s. **Mitigado** (saneado previo): P004 4.99 → 2.06 s, P011 2.96 → 0.00 s, W12 4.3 →
  1.4 s. Propios p95 3.97 → 1.98 s.
- **Primera ola en frío**: en 2 de ~10 corridas los primeros 4 casos tardaron 6–10 s con
  generación de 1.3–5.6 s (el tiempo extra está dentro de la llamada HTTP, no en el código
  local, que tarda 0.12 s); no se reprodujo en 4 corridas instrumentadas.
- No se aplicó un timeout más corto: no mejora el p95 y con un máximo observado de 10.5 s
  arriesga 503. Propuesta para la Fase 10: *hedge* tras ~3 s o modelo/proveedor más estable.

## Fallos que siguen

- **P036** (`agotado`, no determinista): "1 caja de desodorante fresco y 3 fardos de papel
  suave 12" → el modelo dio cantidad 1 al papel; pasó en 2 de 3 corridas sueltas. El anclaje de
  cantidades no lo corrige porque el 1 aparece en el mensaje (regla conservadora).
- **Fragilidad del prompt**: cada línea nueva cambió salidas que ya pasaban (D046 en la
  iteración 2, P044 y P038 en la 3). Con `temperature=0` el modelo no es del todo
  determinista; conviene repetir las corridas finales en la Fase 10.
- **V10** (Fase 6) pasó en esta corrida, pero sigue siendo frágil ("queso" omitido).
- Riesgo conocido del saneado: una oración que mezcle pedido y manipulación sin separador se
  pierde completa (D41).

## Gasto

Corridas en proceso desde `propios_antes`: 24 corridas registradas, **USD 0.173** según
OpenRouter (+ las primeras llamadas rechazadas por el filtro, que no se registran, < USD 0.002).
Total de la fase ≈ **USD 0.18** de USD 0.75.

## Cómo reproducir

```bash
uv run python eval/casos_propios.py                       # regenera los casos (con datos/)
uv run python eval/correr.py eval/casos_propios.jsonl --marca propios --concurrencia 4
uv run python eval/correr.py ejemplos/mensajes_dev.jsonl eval/casos_variantes.jsonl \
    eval/casos_control.jsonl --marca regresion
uv run python evidencia/sesiones/2026-11-23_F7/latencia/sonda.py /tmp/envios.jsonl \
    --marca lat --concurrencia 1                          # intentos HTTP por caso
```
