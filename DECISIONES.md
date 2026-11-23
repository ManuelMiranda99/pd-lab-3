# Decisiones del proyecto

Registro de decisiones de diseño y de interpretación de políticas. Cada entrada tiene:
**Contexto · Decisión · Alternativas · Justificación / evidencia**.

La evidencia de las decisiones de política sale de `scripts/explorar_casos.py`, que verifica
automáticamente las reglas contra los 84 casos de `ejemplos/mensajes_dev.jsonl` (salida en
`evidencia/sesiones/2026-11-22_F1/exploracion.txt`). Resultado: **todas las reglas numéricas
(precio, descuento, redondeo, total, fecha y crédito) se cumplen en el 100 % de los casos donde
aplican**, así que las interpretaciones de abajo reproducen exactamente las respuestas esperadas.

| # | Tema | Decisión en una línea |
|---|---|---|
| D01 | Modelo de desarrollo | `openai/gpt-4.1-nano` vía OpenRouter (se revisa en la comparación final) |
| D02 | Qué se versiona | Sí: políticas, catálogo de problemas, ejemplos. No: parquet, `.env`, PDF, MLflow local |
| D03 | Hora de corte y "posterior" | `hora ≥ 14:00` ⇒ día siguiente; entrega = primera visita **estrictamente** posterior |
| D04 | Redondeo | `Decimal`, mitad hacia arriba por subtotal; total = suma sin re-redondear |
| D05 | Descuento por escala | Por línea, sobre la cantidad final; también para "lo de siempre" y sustitutos |
| D06 | "Lo de siempre" | Último pedido `entregado` (fecha, luego id); agotados → sustituto o pendiente |
| D07 | Sustituto que ya está en el pedido | Se suma a esa línea; la sustitución reporta solo la cantidad sustituida |
| D08 | Orden de las líneas | Orden de primera mención; "lo de siempre" en su posición, en el orden del historial |
| D09 | `texto` de un pendiente | Ambiguo/no existe: fragmento que nombra el producto; agotado: nombre del catálogo |
| D10 | Ambigüedad y agotados | "aceite" es ambiguo (A06, A07) aunque A07 esté agotado; las opciones incluyen agotados |
| D11 | Orden de pendientes y motivos | Orden de aparición; `credito_excedido` al final; sin duplicados |
| D12 | Crédito junto a pendientes | Se evalúan ambos; el crédito usa el total de las líneas calculadas |
| D13 | Datos: carga y validación | Carga única por proceso, estructuras inmutables, validación estricta con `ErrorDatos` |
| D14 | Tipos del motor de cálculos | `Decimal` en todo el motor (`LineaCalculada` congelada); a `float` de 2 decimales solo al serializar en la API; `fecha_hora` con parser estricto |

---

## D01 · Modelo de desarrollo: `openai/gpt-4.1-nano`

- **Contexto.** El desarrollo y las pruebas llaman muchas veces al modelo; el evaluador usará el
  modelo de `.env.example` con su propia clave y medirá latencia.
- **Decisión.** Desarrollar con `openai/gpt-4.1-nano` en OpenRouter (valor por defecto en
  `config.py` y `.env.example`). En la fase de comparación se mide contra modelos más capaces y
  se fija el que se entrega.
- **Alternativas.** `google/gemini-2.5-flash-lite` (mismo precio, otro proveedor),
  `mistralai/mistral-small-3.2-24b-instruct` (más barato), `openai/gpt-4o-mini` (reserva).
- **Justificación / evidencia.** USD 0.10 / 0.40 por millón de tokens, salida estructurada nativa
  (JSON Schema estricto), sin razonamiento obligatorio (baja latencia) y proveedor estable.
  Lista y criterios en `evidencia/modelos_candidatos.md`; prueba de acceso en
  `evidencia/sesiones/2026-11-22_F0/ping_llm.txt`.

## D02 · Qué se versiona y qué no

- **Contexto.** El enunciado exige datos y credenciales fuera del repositorio y de las imágenes.
- **Decisión.** Se versionan `politicas.md`, `catalogo_problemas.md` y `ejemplos/` (son parte de
  la especificación y de la evaluación). **No** se versionan `datos/` ni `*.parquet`, `.env`, el
  PDF del enunciado ni MLflow local (`mlruns/`, `mlartifacts/`, `mlflow.db`); todo está en
  `.gitignore`. Solo `.env.example` (sin clave) va al repo.
- **Alternativas.** Versionar los parquet para que la CI pruebe con datos reales (descartado: lo
  prohíbe el enunciado); excluir también los ejemplos (descartado: la evaluación local los usa).
- **Justificación / evidencia.** Requisito del PDF §4. Las pruebas que necesitan datos se marcan
  `requiere_datos` y se saltan si `datos/` no existe (la CI no tiene datos).

## D03 · Hora de corte (14:00) y "primera visita posterior"

- **Contexto.** `politicas.md` §7: "primera visita … posterior a la fecha de recepción" y "un
  pedido recibido a las 14:00 o después se considera recibido al día siguiente". Dudas: ¿14:00
  exacto?, ¿"posterior" incluye el mismo día si es día de visita?
- **Decisión.**
  1. `fecha_hora` (hora de Guatemala, la del **último** mensaje, según el Anexo) se usa tal cual,
     sin el reloj del sistema. Si `hora ≥ 14` (es decir, desde las **14:00 exactas**, inclusive),
     la fecha de recepción es el día siguiente; si no, es el mismo día.
  2. La entrega es el primer día de `dias_visita` **estrictamente posterior** a la fecha de
     recepción: si la recepción cae en un día de visita, ese día **no** cuenta.
  3. Sin feriados. Siempre hay entrega a ≤ 7 días.
- **Alternativas.** Tratar 14:00 exacto como antes del corte (contradice "14:00 o después");
  entregar el mismo día de recepción si es día de visita (contradice los ejemplos).
- **Justificación / evidencia.** Regla (e) 84/84. 25 casos tienen recepción en día de visita y
  todos saltan a la visita siguiente: p. ej. D013 (lunes 07:57, ruta lun/mié/vie → miércoles),
  D022 (martes 06:50, ruta mar/vie → viernes), D005 (jueves 20:02 → recepción viernes, ruta
  mar/vie → martes 1-dic). Cerca del corte: D041 (sábado 14:02 → recepción domingo → lunes) y
  D037 (martes 13:30 → miércoles). No hay casos a las 14:00 exactas: se aplica el texto literal
  ("o después"); queda como caso propio para la evaluación.

## D04 · Redondeo de subtotales

- **Contexto.** `subtotal = cantidad × precio × (1 − descuento/100)`, "redondeado a 2 decimales
  (mitad hacia arriba)". El `round()` de Python con `float` usa redondeo bancario y arrastra error
  binario.
- **Decisión.** El motor calcula con `decimal.Decimal` (precio convertido con `Decimal(str(x))`),
  y redondea **cada subtotal** con `quantize(Decimal("0.01"), ROUND_HALF_UP)`. `total` = suma de
  los subtotales ya redondeados (no se vuelve a redondear ni se recalcula desde el bruto). En el
  JSON se emiten como números con 2 decimales.
- **Alternativas.** `float` + `round()` (falla en los ,xx5); redondear solo el total.
- **Justificación / evidencia.** Reglas (c) 79/79 y (d) 84/84. En los casos dev ningún subtotal
  bruto termina en 5 en la tercera cifra (el `float` coincidiría), por eso el comportamiento
  mitad-arriba se cubrirá con pruebas unitarias propias (p. ej. 3 × 42.50 × 0.97 = 123.675 →
  `Decimal` da 123.68; `round(float)` da 123.67).

## D05 · Descuento por escala

- **Contexto.** "Por categoría, según la cantidad de la línea (en unidades de venta). Se aplica el
  mayor porcentaje cuyo mínimo se alcance." ¿Qué pasa con líneas de "lo de siempre", sustitutos,
  o líneas que resultan de sumar menciones?
- **Decisión.** El descuento se calcula **al final**, por línea, con la cantidad final de la línea
  (después de sumas, correcciones, "lo de siempre" y fusiones) y la categoría del producto
  **entregado**. El mínimo se alcanza con `cantidad ≥ cantidad_minima`. Las líneas de "lo de
  siempre" y las sustituidas reciben descuento igual que cualquier otra. Nunca se suman
  cantidades de productos distintos de la misma categoría.
- **Alternativas.** No dar descuento a "lo de siempre" (sin base en la política); descontar por
  total de la categoría (contradice "la cantidad de la línea").
- **Justificación / evidencia.** Regla (b) 79/79 (p. ej. D032: B01×24 → 5 %, D019: B06×12 → 3 %,
  D062: L02×16 → 5 % tras la corrección 15→16). Ningún caso dev tiene una línea habitual que
  alcance una escala (lo dice la exploración), así que se aplica el texto de la política sin
  excepción. Los cuatro pares agotado/sustituto son de la misma categoría.

## D06 · "Lo de siempre"

- **Contexto.** `politicas.md` §3. Dudas: qué es "último", qué pasa si ese pedido incluye
  productos hoy agotados, y cómo se combina con agregados/exclusiones.
- **Decisión.**
  1. Último pedido = el de mayor `fecha_entrega` con estado `entregado` (empate: mayor
     `pedido_id`). Los cancelados se ignoran aunque sean más recientes.
  2. Se toman sus líneas con las mismas cantidades, aplicando las reglas normales de agotados:
     con sustituto → se entrega el sustituto y se registra en `sustituciones` (sin revisión);
     sin sustituto → pendiente `agotado_sin_sustituto` (`opciones = [sku]`, `texto` = nombre del
     catálogo) y el pedido pasa a `requiere_revision`.
  3. "Sin X" / "quíteme X" elimina el producto del pedido habitual; si X era un agotado sin
     sustituto, también desaparece su pendiente.
  4. Los agregados se añaden; si un agregado coincide con un sku habitual, las cantidades se
     suman (regla general de menciones repetidas), salvo corrección explícita.
  5. Cliente sin ningún pedido entregado: no se inventan líneas; el pedido queda en
     `requiere_revision` para que lo vea un asesor. Como el contrato no tiene un motivo para esto,
     el mecanismo exacto se fija en la fase de armado del pedido (no ocurre en los casos dev:
     todos los clientes que piden "lo de siempre" tienen pedidos entregados).
- **Alternativas.** Usar el pedido más reciente aunque esté cancelado; omitir en silencio los
  agotados sin sustituto.
- **Justificación / evidencia.** Regla (g): 10/12 automáticos; los 2 restantes son exclusiones
  pedidas en el mensaje (D025 "quíteme sal pacífico" quita A11; D040 "sin desodorante fresco"
  quita P04 y su pendiente). D012, D017, D031 y D044 tienen un pedido cancelado más reciente y el
  esperado usa el entregado anterior. Sustituciones en habitual: D012, D017, D025, D044, D047,
  D080. Agotados sin sustituto en habitual: D017 (P04), D031 (P04, S02), D033 (A13, S02).

## D07 · Sustituto que ya está en el pedido

- **Contexto.** El último pedido de C011 trae A06×3 y A07×1; A07 está agotado y su sustituto es
  A06.
- **Decisión.** Las cantidades se fusionan en **una sola línea** del sku entregado (A06×4, en la
  posición de su primera aparición) y `sustituciones` registra solo la parte sustituida
  (`A07→A06, cantidad 1`). El descuento se calcula sobre la línea fusionada (D05).
- **Alternativas.** Dos líneas con el mismo sku (el contrato no lo prohíbe, pero el esperado no lo
  hace).
- **Justificación / evidencia.** D047: líneas `A06×4, B09×3, …` y sustituciones
  `[A07→A06×1, B10→B09×3]`.

## D08 · Orden de las líneas

- **Contexto.** ¿El esperado ordena por sku o por orden de mención?
- **Decisión.** Orden de **primera mención** en la conversación (concatenando mensajes en orden).
  Una corrección o una suma conserva la posición original del producto. "Lo de siempre" inserta
  sus líneas en la posición donde aparece la expresión, en el orden del pedido histórico (los
  datos lo traen ordenado por sku); un sustituto ocupa la posición del producto original.
- **Alternativas.** Ordenar por sku (contradice D009: `B04, S05, L03, A15`).
- **Justificación / evidencia.** D009, D019, D057 (orden de mención); D021, D073, D076, D077
  (la corrección no mueve la línea); D012, D043, D044, D058, D080 (habitual primero, luego
  agregados); D012/D017 (A06 sustituto en la posición de A07). Aun así, el calificador local
  comparará líneas como conjunto (sku, cantidad) y reportará el orden aparte.

## D09 · Texto de los pendientes

- **Contexto.** El Anexo dice "`texto` (el fragmento del mensaje)" y su ejemplo usa
  `"una caja de aceite"`; en los casos dev el texto es normalizado: `"aceite"`, `"papel higiénico"`
  (con tilde aunque el mensaje dice "papel higienico"), `"recargas de Q50"` (el mensaje dice
  "recargas de q50 x 3"), `"gas propano"` (el mensaje dice "cilindros de gas propano"); y para
  agotados sin sustituto el **nombre del catálogo** (`"Galletas Dulcitas Chocolate"`, aunque el
  mensaje dice "dulcitas chocolate").
- **Decisión.**
  - `producto_ambiguo`, `producto_no_existe`, `cantidad_no_convertible`: el fragmento que nombra
    el producto, tal como lo extrae el LLM (sin cantidad ni unidad, sin saludo), p. ej. `"aceite"`.
  - `agotado_sin_sustituto`: el `nombre` del producto en el catálogo (también cuando viene de
    "lo de siempre", donde no hay fragmento).
- **Alternativas.** Copiar literalmente el fragmento original con cantidad (como el Anexo);
  normalizar ortografía con un diccionario.
- **Justificación / evidencia.** Regla (h) verifica `texto == nombre` en los 6 agotados sin
  sustituto. Los textos de ambiguos/no existentes no son literales en 2 de 13 casos (D005, D045)
  y el Anexo contradice a los ejemplos, así que el campo `texto` es descriptivo: el calificador
  local compara `motivo` y `opciones` exactos y `texto` de forma laxa (normalizado/contenido).

## D10 · Ambigüedad con productos agotados ("aceite", "cocas")

- **Contexto.** "aceite" puede ser A06 (disponible) o A07 (agotado, sustituto A06). ¿Se resuelve
  a A06 porque al final se entregaría A06?
- **Decisión.** **Es ambiguo**: pendiente `producto_ambiguo` con **todas** las opciones del
  catálogo que encajan, incluidos los agotados (`["A06", "A07"]`), en orden de sku. No se
  aplica sustitución a un texto ambiguo. Si el texto es específico ("aceite dorado" → A06;
  "aceite palma real" → A07 → sustituido por A06) se resuelve normalmente.
- **Alternativas.** Excluir agotados de las opciones o resolver por sustitución.
- **Justificación / evidencia.** D082 (`aceite` → `["A06","A07"]`), Anexo del PDF (mismo caso),
  D064 (`cocas` → `["B01","B02","B03"]`, B03 agotado). Otros ambiguos: `naranjitas` → B04/B05,
  `papel higiénico` → L04/L05, `juguitos` → B09/B10, `pasta` → A09/A10/P03 (incluye la pasta
  dental).

## D11 · Orden de pendientes y de `motivos_revision`

- **Contexto.** Ningún caso dev tiene más de un motivo distinto; D031 y D033 tienen dos
  pendientes del mismo motivo.
- **Decisión.** `pendientes` en orden de aparición (los de "lo de siempre" en el orden del pedido
  histórico, en la posición de la expresión). `motivos_revision` = motivos de los pendientes **sin
  duplicados, en orden de primera aparición**, y `credito_excedido` siempre **al final** (se
  calcula después del total).
- **Alternativas.** Orden alfabético o un orden canónico fijo de motivos.
- **Justificación / evidencia.** D031 (`P04`, luego `S02`) y D033 (`A13`, luego `S02`) siguen el
  orden del historial. Regla (i) 84/84: los motivos coinciden siempre con el conjunto de motivos
  de los pendientes más `credito_excedido`, sin duplicados. Como el orden no está determinado por
  los ejemplos, el calificador local compara motivos como conjunto.

## D12 · Crédito excedido junto a pendientes

- **Contexto.** ¿Se evalúa el crédito si ya hay pendientes? ¿Con qué total?
- **Decisión.** Siempre que haya pedido (estado ≠ `fuera_de_alcance`) se evalúa
  `saldo_pendiente + total > limite_credito` (estricto: igual al límite **no** excede), con el
  total de las líneas calculadas (los pendientes no suman). Puede coexistir con otros motivos.
  En `fuera_de_alcance` nunca se agrega `credito_excedido`.
- **Alternativas.** Omitir el crédito cuando ya hay revisión; estimar el valor de los pendientes.
- **Justificación / evidencia.** Regla (f) 79/79 (márgenes de −164 a −516 en los excedidos, p. ej.
  D062, D032). D032 combina crédito excedido con una sustitución; D040 con una exclusión en
  "lo de siempre". La política dice "las líneas se calculan igual".

## D13 · Carga y validación de datos

- **Contexto.** El motor y la API necesitan el catálogo en cada solicitud; los datos se montan
  como volumen y pueden faltar o venir mal.
- **Decisión.** `novapedidos.datos.obtener_datos()` carga los 5 parquet de `DATOS_DIR` **una vez
  por proceso** (`lru_cache`) en dataclasses congeladas y `MappingProxyType` (inmutables).
  Validación estricta con `ErrorDatos` y mensaje claro: archivo o columna faltante, sku
  duplicado, sustituto inexistente/agotado/propio, alias (normalizado a minúsculas) hacia sku
  inexistente o hacia dos sku, categoría de escala desconocida, día de visita inválido, historial
  con cliente/sku/estado inválido. Precios y montos como `Decimal`; `dias_visita` como índices de
  `date.weekday()`; fechas del historial como `date`.
- **Alternativas.** Leer los parquet en cada solicitud (latencia); validar con pandera (otra
  dependencia).
- **Justificación / evidencia.** `tests/test_datos.py` (pruebas sintéticas que corren siempre y
  pruebas con datos reales que se saltan sin `datos/`).


## D14 · Tipos del motor de cálculos y parser de `fecha_hora`

- **Contexto.** La Fase 3 (armado del pedido) y la API consumen el motor; hay que fijar qué tipos
  entran y salen y cómo se valida `fecha_hora`.
- **Decisión.**
  1. `motor.precios` trabaja y devuelve `Decimal` (precio, subtotal, total con exactamente 2
     decimales; `total([])` = `0.00`). `calcular_linea` devuelve `LineaCalculada`, dataclass
     congelada con `sku, cantidad, precio_unitario, descuento_pct, subtotal`. Las entradas
     `float` se aceptan pero se convierten con `Decimal(str(x))`. La conversión a número JSON
     (2 decimales) se hace **solo** en la capa de la API/esquema de respuesta.
  2. Cantidades no positivas o no enteras y descuentos fuera de `[0, 100)` lanzan `ValueError`
     (son errores de programación: la Fase 3 debe filtrar antes).
  3. `parsear_fecha_hora` acepta exactamente `AAAA-MM-DDTHH:MM` (regex + `strptime`): rechaza
     segundos, zona, espacios, un solo dígito y fechas imposibles con `ErrorFechaHora`
     (subclase de `ValueError`, mensaje con el formato esperado) para que la API responda 422.
  4. `fecha_entrega` valida `dias_visita` no vacío y en 0–6 (`ValueError`).
- **Alternativas.** Devolver `float` desde el motor (reintroduce el error binario); usar
  `datetime.fromisoformat` (acepta segundos, zona y otras variantes).
- **Justificación / evidencia.** `tests/test_motor_calculos.py` (59 pruebas sintéticas) y
  `tests/test_regresion_dev.py` (84/84 casos dev: precio, descuento, subtotal, total, fecha de
  entrega y crédito).

---

## Observaciones de los ejemplos para las fases siguientes

- **Sin contradicciones con `politicas.md`** en los 84 casos. La única discrepancia es de forma:
  el `texto` de los pendientes (D09) difiere entre el Anexo y los ejemplos.
- **No cubierto por los ejemplos dev** (crear casos propios): `cantidad_no_convertible`, 14:00
  exactas, crédito justo en el límite, varios motivos distintos a la vez, menciones repetidas que
  se suman, línea habitual con descuento, cliente sin pedidos entregados.
- **Unidades:** "botellas", "latas", "bolsas" y "docena" son unidades sueltas (D001, D024, D050,
  D074, D084); diminutivos ("farditos", "cajitas") son la unidad de venta (D018, D049, D005).
