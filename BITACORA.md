# Bitácora de sesiones — Laboratorio 3

> La mantienen `/iniciar-fase` y `/cerrar-fase`. La entrada más reciente va **al final**.
> Cada entrada cierra con "Próxima sesión", que es lo primero que lee `/iniciar-fase`.

---

## Sesión 2026-11-22 — Planificación
**Fase:** Previa a la Fase 0
**Estado al cerrar:** planificación terminada

### Logrado
- Revisión del PDF, `politicas.md`, `catalogo_problemas.md`, los 5 parquet y `mensajes_dev.jsonl`.
- `PLAN.md` con 12 fases (0–11), tareas con ID, verificación manual por fase y matriz de cumplimiento.
- Skills `/iniciar-fase` y `/cerrar-fase` en `.claude/skills/` (objetivo + resumen al inicio;
  fallos + capturas al cierre).

### Verificación
- Entorno: Python 3.11, `uv`, Docker 29.9 activo, `gh` autenticado.

### Lo que falló
- `pdftotext` no está instalado; el PDF se leyó directamente (sin impacto).

### Hallazgos de los datos
- 7 agotados; A13, S02 y P04 sin sustituto. "aceite" es ambiguo A06/A07 aunque A07 esté agotado.
- Casos dev entre 2026-11-23 y 2026-12-12; historial hasta 2026-11-20.

### Pendientes / bloqueos
- Falta la clave de OpenRouter en `.env` (la consigue el usuario).
- Confirmar fecha límite y si el repo será público o privado con el docente como colaborador.

### Próxima sesión
- Empezar por: **Fase 0**, F0.1 (`git init` y `.gitignore`).
- Luego: F0.2–F0.5 y F0.7 (probar OpenRouter con la clave).
- Tener en cuenta: los datos y el PDF nunca entran al repo.

---

## Sesión 2026-11-22 — Fase 0 (inicio)
**Fase:** 0 — Preparación del repositorio
**Estado al cerrar:** Fase 0 cerrada ✅

### Logrado
- F0.1 `git init -b main`, `.gitignore` (`/datos/`, `.env`, `*.pdf`, `*.parquet`, MLflow local, `/eval/resultados/`).
- F0.2 `pyproject.toml` + `uv.lock` (langchain 1.5, langchain-openai 1.8, mlflow 3.18, fastapi 0.165, pydantic 2.14…).
- F0.3 Estructura `src/novapedidos/{datos,motor,llm,api}`, `config.py` con la clave como `SecretStr`.
- F0.4 `CLAUDE.md` (reglas + flujo con subagentes), `.env.example`, `.env` local, `README.md` mínimo.
- F0.5 `scripts/capturar.py` (Playwright con el Chrome instalado).
- F0.6 `.github/workflows/ci.yml` (ruff + pytest); repo privado https://github.com/ManuelMiranda99/pd-lab-3, commit inicial `dafe90a`, CI en verde (22 s). El PDF y `.Rhistory` se ignoran explícitamente.
- F0.7 `scripts/ping_llm.py` con salida estructurada; `evidencia/modelos_candidatos.md`.

### Verificación (hecha por la sesión principal, no solo el reporte del subagente)
- pytest: 3 pasan · ruff: sin errores.
- `git check-ignore`: `datos/`, `.env` y el PDF ignorados.
- ping OpenRouter (`openai/gpt-4.1-nano`): salida estructurada correcta, 1.86 s, 164/17 tokens, USD 0.0000232.

### Lo que falló
- `.gitignore` con `datos/` ignoraba también `src/novapedidos/datos/` · resuelto con `/datos/`.
- `ruff format` marcó una línea larga en `ping_llm.py` · resuelto.
- Prueba con clave falsa → 401 de OpenRouter · esperado; el script sale con mensaje claro.

### Capturas
- evidencia/sesiones/2026-11-22_F0/prueba_captura.png — prueba de `capturar.py`.
- evidencia/sesiones/2026-11-22_F0/ping_llm.txt — salida del ping a OpenRouter.

### Decisiones tomadas
- Modelo de desarrollo: `openai/gpt-4.1-nano` (USD 0.10/0.40 por M); recomendados para F10 en `evidencia/modelos_candidatos.md`.
- Se versionan `politicas.md`, `catalogo_problemas.md` y `ejemplos/`; no los parquet, `.env`, PDF ni MLflow local.
- (Pasar ambas a `DECISIONES.md` cuando se cree en la Fase 1.)

### Próxima sesión
- Empezar por: **Fase 1** en rama `feat/carga-datos`: F1.1 loader, F1.2 exploración de casos, F1.3 dudas de política, F1.4 tests.
- Tener en cuenta: tests con datos marcados `requiere_datos` y saltados en CI. El repo se hace público al terminar todo (F11.5).

---

## Sesión 2026-11-22 — Fase 1 (inicio)
**Fase:** 1 — Carga de datos y exploración de casos
**Estado al cerrar:** Fase 1 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F1.1 `src/novapedidos/datos/catalogo.py`: `obtener_datos()` (cacheado), `Datos` inmutable con `producto`, `cliente`, `sku_por_alias`, `ultimo_pedido_entregado`; validación estricta (`ErrorDatos`).
- F1.2 `scripts/explorar_casos.py` → `evidencia/sesiones/2026-11-22_F1/exploracion.txt`.
- F1.3 `DECISIONES.md` con D01–D13 (corte 14:00, visita estrictamente posterior, redondeo `Decimal` HALF_UP, "lo de siempre", orden de líneas/motivos, ambigüedad incluye agotados, crédito).
- F1.4 `tests/test_datos.py` (28 sintéticas + 3 con datos reales, saltadas en CI).

### Verificación
- pytest: 31 pasan · ruff check/format: ok.
- Reglas vs dev: precio 79/79, descuento 79/79, redondeo 79/79, total 84/84, fecha 84/84, crédito 79/79, sustituciones 14/14, coherencia 84/84; "lo de siempre" 10/12 (los 2 restantes son exclusiones "sin X", esperadas).

### Lo que falló
- Regla (g) fallaba en D047 (A07 sustituido por A06 ya presente) · resuelto: fusionar por sku entregado (D07).
- ruff E501/E741/B007 · resuelto.
- Lectura del PDF sin `pdftotext` · resuelto con `uv run --with pypdf` (sin añadir dependencia).

### Capturas
- evidencia/sesiones/2026-11-22_F1/exploracion.txt (salida de texto; no aplica imagen).

### Decisiones tomadas
- D01–D13 en DECISIONES.md. Discrepancia Anexo vs dev en `pendientes[].texto` (fragmento vs nombre normalizado): calificar `texto` de forma laxa.

### Próxima sesión
- Fase 2 (motor de cálculos). Casos sin cobertura dev para Fase 7: `cantidad_no_convertible`, 14:00 exacto, crédito justo en el límite, varios motivos, menciones repetidas, cliente sin pedidos entregados.

---

## Sesión 2026-11-22 — Fase 2 (inicio)
**Fase:** 2 — Motor de reglas: cálculos
**Estado al cerrar:** Fase 2 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F2.1 `motor/precios.py`: `descuento_pct`, `subtotal` (Decimal HALF_UP), `calcular_linea` → `LineaCalculada`, `total`.
- F2.2 `motor/fechas.py`: `parsear_fecha_hora` estricto (`ErrorFechaHora`), `fecha_efectiva_recepcion` (corte 14:00), `fecha_entrega` (visita estrictamente posterior).
- F2.3 `motor/credito.py`: `credito_excedido` (`>` estricto).
- F2.4 `tests/test_motor_calculos.py`: 59 pruebas de bordes (14:00 exacto, ruta solo sábado, fin de año, redondeo .005, crédito en el límite, 13 formatos inválidos).
- F2.5 `tests/test_regresion_dev.py`: 84/84 casos dev (precio, descuento, subtotal, total, fecha, crédito).

### Verificación
- pytest: 175 pasan (sin `datos/`: 87 pasan, 88 se saltan, como en CI) · ruff check/format: ok.

### Lo que falló
- Rojo esperado de TDD (`ModuleNotFoundError` antes de escribir el código) · resuelto.
- Un archivo sin formato · resuelto con `ruff format`.

### Capturas
- No aplica (código puro; evidencia = salida de pytest).

### Decisiones tomadas
- D14: motor con `Decimal`; conversión a número JSON solo en la API; `ErrorFechaHora` → 422.

### Próxima sesión
- Fase 3 (armado del pedido).

---

## Sesión 2026-11-22 — Fase 3 (inicio)
**Fase:** 3 — Motor de reglas: armado del pedido
**Estado al cerrar:** Fase 3 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F3.1 `contrato.py` (respuesta del Anexo, compartida con la API) y `motor/modelos.py` (`InterpretacionResuelta`, `Mencion`: texto, resolucion sku|ambiguo|no_existe, cantidad, unidad venta|unidad_suelta|docena, accion agregar|corregir|quitar).
- F3.2–F3.6 `motor/pedido.py`: `armar_pedido(cliente_id, fecha_hora, interpretacion, datos) -> RespuestaPedido`; `ErrorClienteInexistente` → 404.
- F3.7 `motor/mensaje.py`: mensaje al cliente por plantilla (saludo por hora, sustituciones, pendientes sin prometer, fecha en español, total calculado).
- F3.8 Oráculo 73/73 casos dev (12 tipos) + 62 pruebas sintéticas.

### Verificación
- pytest: 311 pasan · ruff check/format: ok.

### Lo que falló
- ruff I001 y E501 · resuelto. Riesgo de import circular contrato↔motor · evitado.

### Capturas
- No aplica (código puro).

### Decisiones tomadas
- D15–D21: estructura intermedia tolerante; cantidades ≤0; quitar/corregir; "lo de siempre" sin historial → pendiente `producto_ambiguo` con `opciones=[]` (revisar); pedido vacío → `fuera_de_alcance`; mensaje por plantilla; contrato compartido.

### Próxima sesión
- Fase 4 (API con intérprete falso). Fase 5: el LLM llena `InterpretacionResuelta` ("media docena" → 6 `unidad_suelta`).

---

## Sesión 2026-11-22 — Fase 4 (inicio)
**Fase:** 4 — API FastAPI con intérprete falso
**Estado al cerrar:** Fase 4 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F4.1 `api/main.py` (`crear_app`, `app`), `api/esquemas.py`, `api/errores.py`: 422 en español con detalles por campo, 404 `cliente_no_encontrado`, 503 ante fallos (nunca 500).
- F4.2 `api/salud.py`: `/health` con chequeo de MLflow (timeout 1 s, caché 5 s), datos e intérprete; campo extra `dependencias`.
- F4.3 `llm/interfaz.py` (`Interprete`, `ErrorInterprete`), `api/interpretes.py` (`InterpreteFalso`), `INTERPRETE=llm|falso`, gancho `api/trazas.py` para MLflow.
- F4.4 `tests/test_api.py`: 61 pruebas (contrato exacto, Anexo con datos sintéticos y reales, 404, 32 variantes de 422, 503, health degradado < 1.5 s con POST respondiendo).

### Verificación
- pytest: 372 pasan · ruff check/format: ok.
- Servidor real con `INTERPRETE=falso`: Anexo → total 840.0, fecha 2026-11-26, pendiente aceite [A06, A07].

### Lo que falló
- `curl localhost:8000` respondía otro servidor: el contenedor Docker `sgn-control-gateway-1` ocupa el puerto 8000 · rodeado con `127.0.0.1`; **chocará con la Fase 9** (habrá que pararlo).
- ruff E501 y 422 de JSON inválido con campo "[0]" · resueltos.

### Capturas
- evidencia/sesiones/2026-11-22_F4/docs.png — Swagger de la API.
- evidencia/sesiones/2026-11-22_F4/health_degradado.png — `/health` con MLflow caído.
- evidencia/sesiones/2026-11-22_F4/respuestas_curl.txt — 200, 422, 404, fuera_de_alcance, health.

### Decisiones tomadas
- D22–D26 (formato de errores, 503 ante fallos, política de /health, elección de intérprete, límites 64/50/2000).
- Duda abierta: montos salen `240.0` (mismo número que `240.00`).

### Próxima sesión
- Fase 5 (intérprete LangChain + OpenRouter) implementando `crear_interprete_llm(config)`.

---

## Sesión 2026-11-22 — Fase 5 (inicio)
**Fase:** 5 — Intérprete con LangChain + OpenRouter
**Estado al cerrar:** Fase 5 cerrada ✅ (subagente; verificada por la sesión principal, terminó 2026-11-23)

### Logrado
- F5.1 `llm/cliente.py`: ChatOpenAI → OpenRouter, temperature 0, timeout 20 s, 1 reintento, metadatos por llamada (tokens, costo, latencia).
- F5.2 `llm/esquema.py` (salida estructurada `json_schema` strict, sku como enum del catálogo, sin campos de precio) y `llm/prompt.py` (catálogo completo + 7 ejemplos; prompt estático cacheable).
- F5.3 `llm/resolucion.py`: alias exacto, coincidencia por palabras (genéricos → ambiguo), fuzzy solo para no_existe, guardas de unidades y correcciones.
- F5.4 Mensajes delimitados como datos, `llm/defensas.py` (reintento si el filtro de contenido del proveedor rechaza), pruebas de inyección.
- F5.5 `llm/interprete.py` (`crear_interprete_llm`) conectado a la API; `scripts/probar_casos.py`.

### Verificación
- pytest: 393 pasan (sin llamar al modelo) · ruff ok.
- 84 casos dev: línea base 77/84 (91.7 %) → final 84/84 en dos corridas; sin la resolución en código 60/84 (71.4 %).
- Latencia p50 ~1.5 s, p95 ~2.2 s; costo ~USD 0.00023 por pedido. Gasto de la fase ~USD 0.09.
- Smoke re-ejecutado por la sesión principal: 12/12, p95 1.86 s.

### Lo que falló
- gpt-4.1-nano solo se sirve desde Azure en OpenRouter: su filtro de contenido rechazó 2 mensajes de inyección (503) · mitigado con D32 (reintento sin las oraciones manipuladoras, +2.5 s).
- Dos pruebas propias fallaron al inicio (clasificación de errores) · resueltas.
- Un comando de shell colgado · resuelto.

### Capturas
- evidencia/sesiones/2026-11-22_F5/{linea_base_84,final_84,final_84_repeticion,final_84_sin_resolucion,smoke_por_tipo,smoke_verificacion_principal,respuestas_llm}.txt

### Decisiones tomadas
- D27–D32 (una llamada con catálogo en el prompt, enum de sku, defensas en capas, parámetros, resolución post-LLM, respaldo ante filtro de contenido).
- Riesgo: el 100 % en dev es optimista (reglas ajustadas viendo dev); validar con casos propios (Fase 7).

### Próxima sesión
- Fase 6 (evaluación local) y Fase 7 (casos propios) para medir sobreajuste; probar modelos fuera de Azure.
- Fase 9: parar el contenedor que ocupa el puerto 8000.

---

## Sesión 2026-11-23 — Fase 6 (inicio)
**Fase:** 6 — Evaluación local y mejora de calidad
**Estado al cerrar:** Fase 6 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F6.1 `src/novapedidos/evaluacion/calificadores.py`: estado, líneas, total (±0.005), fecha, motivos, pendientes (texto laxo), sustituciones, caso_exacto, integridad_precios, latencia.
- F6.2 `eval/correr.py` (en proceso o por HTTP con `--url`, `--modelo`, `--concurrencia`); resultados en `eval/resultados/<marca>/`.
- F6.3 `eval/casos_variantes.jsonl` (24 paráfrasis nuevas) y `eval/casos_control.jsonl` (14); mejoras generales al prompt y a la resolución.
- F6.4 Calificador de `mensaje_cliente` por reglas.

### Verificación
- pytest: 442 pasan · ruff ok.
- dev 84/84 · variantes 20/24 → 23/24 · control 14/14 (re-ejecutado por la sesión principal: 14/14).
- integridad_precios y mensaje_cliente: 100 %. Gasto ≈ USD 0.14.

### Lo que falló
- Iteración 1 bajó dev a 78/84 (bug al anclar cantidades con punto final y listas) · resuelto en iteraciones 2–3.
- V10 ("una libra de queso y 2 de café instantáneo") sigue fallando: el modelo omite "queso" · sin resolver.
- Latencia de cola: en la verificación de la sesión principal el control dio p95 8.05 s, máx 10.6 s (4 hilos) · investigar en Fase 7/10.

### Capturas
- evidencia/sesiones/2026-11-23_F6/resumen_fase6.md y reportes antes/después.

### Decisiones tomadas
- D33–D37 (calificadores, variantes con esperado del motor, paquete `novapedidos.evaluacion`, mensaje por reglas, guardas nuevas).

### Próxima sesión
- Fase 7: casos propios; medir antes de ajustar; investigar latencia de cola.

---

## Sesión 2026-11-23 — Fase 7 (inicio)
**Fase:** 7 — Casos de evaluación propios
**Estado al cerrar:** Fase 7 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F7.1 `eval/casos_propios.py` → `eval/casos_propios.jsonl`: 55 casos (12 inyección, 10 fuera del catálogo, 7 bordes de regla, resto por tipo dev); esperado calculado con el motor.
- F7.2 `tests/test_casos_propios.py` (sincronía, claves, cobertura).
- F7.3 Medición antes de ajustar y 3 iteraciones de mejoras generales; investigación de latencia de cola.

### Verificación
- pytest: 451 pasan · ruff ok.
- Propios: **48/55 (87.3 %) antes** de ajustar (estimación honesta) → 54/55 después; verificación de la sesión principal 55/55, p95 2.17 s.
- dev/variantes/control 122/122. `integridad_precios` 100 % en todas las corridas. Gasto ≈ USD 0.18.

### Lo que falló
- 7 casos propios fallaban antes (media docena, caja y media, docena de cajas, marca no vendida, reclamo + pedido…) · resueltos con cambios generales.
- El prompt es frágil: cada regla nueva rompía otro caso · resuelto reubicando reglas y con un ejemplo nuevo.
- P036 intermitente ("papel suave 12" → cantidad 1) · sin resolver (nano no determinista).
- Latencia de cola = picos de generación del proveedor (Azure), no reintentos · mitigado el doble llamado del filtro (D41); primera ola lenta ocasional sin causa exacta.

### Capturas
- evidencia/sesiones/2026-11-23_F7/{propios_antes,propios_despues,regresion_despues}.txt, latencia/, resumen_fase7.md.

### Decisiones tomadas
- D38–D41 (casos propios, políticas no cubiertas, mejoras de prompt, latencia).

### Próxima sesión
- Fase 8 (MLflow): registrar `filtro_contenido`, `saneado_previo`, `id_generacion` en las trazas.

---

## Sesión 2026-11-23 — Fase 8 (inicio)
**Fase:** 8 — MLflow: trazas y evaluación
**Estado al cerrar:** Fase 8 cerrada ✅ (subagente, reanudado tras un corte por límite de uso; verificada por la sesión principal)

### Logrado
- F8.1 `src/novapedidos/trazado.py` + `RegistradorMlflow`: traza por solicitud en `novapedidos-api` (interpretar_pedido → interprete_llm → ChatOpenAI → resolucion_productos → motor_armar_pedido) con modelo, tokens, costo, latencia, id de generación.
- F8.2 Conexión a MLflow en hilo de fondo (reintento cada 5 s), exportación asíncrona acotada, cero llamadas de red en la solicitud.
- F8.3 `eval/correr.py --mlflow`: run por corrida en `novapedidos-evaluacion` (~60 métricas, parámetros con commit, artefactos) + `mlflow.genai.evaluate` sin volver a llamar al modelo.
- F8.4 `tests/test_trazas_mlflow.py`, `tests/test_evaluacion_mlflow.py`, `scripts/probar_resiliencia_mlflow.py`.

### Verificación
- pytest: 461 pasan · ruff ok.
- Evaluación completa nano: 176/177 (dev 84/84, variantes 24/24, control 14/14, propios 54/55); p50 1.63 s, p95 2.73 s; USD 0.0445.
- Resiliencia: sobrecarga de trazas ≈ 0 ms con MLflow arriba/muerto/colgado; `/health` degradado en ≤ 1 s y ok en ~1 s al volver; trazas posteriores registradas sin reiniciar.

### Lo que falló
- El subagente se cortó por límite de uso a mitad de la fase · reanudado sin pérdida.
- Aviso de MLflow al importar · resuelto con `MLFLOW_DISABLE_AGENT_HINT=1`.
- ruff F811/I001 en pruebas · resuelto.
- Capturas: URL de vista por caso y recorte de métricas · resueltos.
- P028 (media docena de kola 3 L) falló en la corrida completa · error del modelo, intermitente.

### Capturas
- evidencia/mlflow/trazas_api_lista.png, traza_solicitud_spans.png, evaluacion_runs.png, evaluacion_run_parametros_metricas.png, evaluacion_por_caso_scorers.png.
- evidencia/sesiones/2026-11-23_F8/resiliencia.txt y eval/.

### Decisiones tomadas
- D42–D44 (diseño de trazas, tolerancia a fallos, evaluación en MLflow).

### Próxima sesión
- Fase 9: MLflow 3.18 requiere `--allowed-hosts` con `mlflow:5000`; la API no debe depender de `service_healthy` de mlflow.

---

## Sesión 2026-11-23 — Fase 9 (inicio)
**Fase:** 9 — Docker Compose
**Estado al cerrar:** Fase 9 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F9.1 `Dockerfile` (python 3.11 slim + uv, no root, sin datos ni `.env`), `.dockerignore`; MLflow `ghcr.io/mlflow/mlflow:v3.18.0` con `--allowed-hosts`.
- F9.2 `docker-compose.yml`: `api` (8000) y `mlflow` (5000) con puertos configurables (`API_PUERTO`, `MLFLOW_PUERTO`), datos montados `:ro`, healthchecks, sin `service_healthy`.
- F9.3 `scripts/probar_resiliencia_docker.py`: stop/start y pause/unpause de MLflow.
- F9.4 Clon limpio → `up --build` → evaluación por HTTP.

### Verificación
- pytest 461 · ruff ok.
- Sesión principal: `up` → `/health` ok en 6 s, ambos healthy; Anexo → 840.0, 2026-11-26, aceite ambiguo.
- Resiliencia: 100 % de POST con 200; `/health` degradado en 0.1 s (stop) / 1 s (pause), ok en ~4 s / ~1 s al volver; trazas nuevas sin reiniciar.
- Clon limpio por HTTP: 138/139 (dev 83/84, propios 55/55), p95 2.17 s. Imagen sin datos ni `.env`.

### Lo que falló
- Sin `--allowed-hosts` MLflow 3.18 da 403 a la API (pero `/health` 200) · resuelto (D46).
- zsh + variable de comando; ruff SIM105 · resueltos.
- Verificación propia: primer chequeo leyó `"datos":"ok"` como ok · repetido correctamente.
- D073 falló una vez en la evaluación Docker (variación del modelo).

### Capturas
- evidencia/sesiones/2026-11-23_F9/01_docs.png, 02_health_ok.png, 03_health_degradado.png, 04_mlflow_trazas.png, 05_mlflow_trazas_clon.png, compose_ps*.txt, resiliencia_docker.txt, eval_docker.txt.

### Decisiones tomadas
- D45–D48 (imagen y uv, MLflow allowed-hosts y volumen, dependencia api→mlflow, puertos configurables).

### Próxima sesión
- Fase 10: comparar modelos; Fase 11: README completo y clon desde GitHub.

---

## Sesión 2026-11-23 — Fase 10 (inicio)
**Fase:** 10 — Comparación de alternativas
**Estado al cerrar:** Fase 10 cerrada ✅ (subagente; verificada por la sesión principal)

### Logrado
- F10.1 7 alternativas: gpt-4.1-nano, gpt-4.1-mini, gemini-2.5-flash, gemini-2.5-flash-lite, mistral-small-3.2, claude-haiku-4.5 y nano sin resolución en código.
- F10.2 177 casos por alternativa, 2 repeticiones (haiku 1), registradas en MLflow `novapedidos-comparacion`; latencia uno a uno de finalistas.
- F10.3 `evidencia/comparacion/` (comparacion.md/csv, 3 gráficas, capturas de MLflow), `scripts/comparar_alternativas.py`.
- F10.4 Elegido **google/gemini-2.5-flash-lite** (.env.example, config, .env); ajuste general de resolución (D49); parámetro `OPENROUTER_RAZONAMIENTO`.

### Verificación
- pytest 465 · ruff ok.
- flash-lite + D49: 177/177 en 2 repeticiones, p95 ~1.1 s, USD 0.0002 por pedido. Nano sin resolución: 74 % (aporte de la capa determinista).
- Sesión principal con flash-lite: control + propios 69/69, p95 1.05 s, integridad 100 %.

### Lo que falló
- 6 modelos en paralelo → 429/errores de OpenRouter en 6 corridas · marcadas inválidas y repetidas (USD 1.44 desperdiciados).
- Haiku con 1 sola repetición por presupuesto. Gasto de la fase USD 3.12.
- Saldo de OpenRouter restante ≈ USD 1.3 (según el subagente).

### Capturas
- evidencia/comparacion/exactitud_vs_costo.png, exactitud_vs_p95.png, exactitud_por_tipo_finalistas.png, mlflow_runs_comparacion.png, mlflow_compare_runs.png.

### Decisiones tomadas
- D49: elección del modelo (D01 superada) y ajuste de resolución.

### Próxima sesión
- Fase 11: README completo, evidencia final con flash-lite en Docker, clon limpio, tag (con permiso).
