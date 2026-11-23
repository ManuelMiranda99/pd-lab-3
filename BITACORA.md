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
