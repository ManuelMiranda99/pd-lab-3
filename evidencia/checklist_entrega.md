# Checklist de entrega contra el enunciado

Revisión del 2026-11-23 contra `Laboratorio_3_PD_26.pdf` (Requerimientos, Evaluación del
funcionamiento, Entregables y Anexo). ✅ = cumplido y verificado · ⚠️ = pendiente o con reserva.

## Requerimientos

| # | Requisito | Estado | Dónde se cumple / evidencia |
|---|---|---|---|
| 1 | Aplicación con LangChain y modelos de OpenRouter configurados en `.env` | ✅ | `src/novapedidos/llm/cliente.py` (`ChatOpenAI` → OpenRouter), `config.py`, `.env.example` (`OPENROUTER_MODEL=google/gemini-2.5-flash-lite`); D27, D30 |
| 1 | Lo que se consume del modelo, con salida estructurada | ✅ | `llm/esquema.py` + `with_structured_output(method="json_schema", strict)`; D28 |
| 1 | Cálculos de negocio (precios, descuentos, totales, fechas, crédito) con código probado | ✅ | `src/novapedidos/motor/`; `tests/test_motor_calculos.py`, `test_motor_pedido*.py`, `test_regresion_dev.py` (465 pruebas); D03–D19 |
| 1 | Ningún mensaje altera precios, descuentos, crédito ni el comportamiento | ✅ | D29/D32/D41; 12 casos `inyeccion` + 2 `manipulacion`: 14/14 e integridad de precios 100 % (`evidencia/evaluacion_final/reporte.txt`, `comparacion/comparacion.md`); `test_mensajes_de_precio_no_alteran_montos` |
| 2 | API que cumple el contrato del Anexo, con validación | ✅ | `src/novapedidos/api/`; ver sección Anexo |
| 2 | Pruebas automatizadas que no dependen del modelo | ✅ | intérprete inyectado y falso en tests (D25); CI sin datos ni clave (`.github/workflows/ci.yml`) |
| 3 | Trazas de las solicitudes en MLflow | ✅ | D42; `evidencia/mlflow/trazas_api_lista.png`, `traza_solicitud_spans.png` |
| 3 | Conjunto de evaluación con casos propios + publicados y calificaciones de calidad | ✅ | `eval/casos_propios.jsonl` (55), variantes (24), control (14), dev (84); 11 calificadores con código (D33, D36); `evidencia/mlflow/evaluacion_*.png` |
| 3 | Comparación de ≥ 2 alternativas en calidad, latencia y costo que respalde la configuración | ✅ | 6 modelos + arquitectura sin resolución: `evidencia/comparacion/comparacion.md`, CSV y gráficas; D49 |
| 4 | API y MLflow con `docker compose up` siguiendo el README | ✅ | `docker-compose.yml`, `Dockerfile`; simulación del docente abajo |
| 4 | Datos y credenciales fuera de imágenes y repo; `.env.example` | ✅ | `.gitignore` y `.dockerignore` excluyen `datos/`, `*.parquet`, `.env`, `*.pdf`; imagen revisada (solo `.venv`, `src`, `README.md`, `pyproject.toml`, `uv.lock`, `datos/` vacío); la clave no aparece en archivos versionados, nuevos ni en el historial |
| 4 | MLflow no disponible ⇒ API responde y `/health` lo refleja | ✅ | D43; `evidencia/resiliencia/resiliencia_docker_flashlite.txt` (stop y pause: todos 200, `degradado` en ≤ 1 s, recuperación sin reiniciar) |
| 5 | Repo en GitHub con estructura clara y archivo de contexto para el agente | ✅ | `CLAUDE.md`, `.claude/skills/`, `PLAN.md`, `BITACORA.md`; estructura en `README.md` |
| 5 | `README.md` para levantar el producto y reproducir la evaluación | ✅ | `README.md` §1–§5 (seguido al pie de la letra en la simulación) |
| 5 | `DECISIONES.md` con decisiones, alternativas y justificación, incluida la elección del modelo | ✅ | `DECISIONES.md` (resumen ejecutivo + D01–D49; modelo en D49) |

## Anexo (contrato)

| Punto | Estado | Verificación |
|---|---|---|
| Puerto 8000 | ✅ | `uvicorn … --port 8000` en el contenedor; publicado en 8000 por defecto (`API_PUERTO` solo cambia el puerto del host) |
| `POST /v1/pedidos/interpretar` con `cliente_id`, `fecha_hora`, `mensajes` | ✅ | `api/esquemas.py`; `curl` del README |
| Respuesta con `cliente_id, estado, lineas[sku, cantidad, precio_unitario, descuento_pct, subtotal], total, fecha_entrega, sustituciones[sku_solicitado, sku_entregado, cantidad], pendientes[motivo, texto, opciones], motivos_revision, mensaje_cliente` | ✅ | `contrato.py`; `test_respuesta_200_tiene_la_forma_exacta_del_contrato`; ejemplo del Anexo en Docker: mismas líneas, total 840, fecha 2026-11-26, pendiente `producto_ambiguo` con `["A06","A07"]` |
| `estado` ∈ {`confirmado`, `requiere_revision`, `fuera_de_alcance`}; `fuera_de_alcance` sin líneas, total 0, `fecha_entrega: null` | ✅ | `EstadoPedido`; `test_fuera_de_alcance_con_fecha_entrega_null`; 10/10 casos `fuera_de_alcance` |
| Motivos (`producto_ambiguo`, `producto_no_existe`, `agotado_sin_sustituto`, `cantidad_no_convertible`, `credito_excedido`); `credito_excedido` solo en `motivos_revision` | ✅ | `MotivoRevision` + validador de `Pendiente`; D11, D12 |
| Líneas solo del catálogo, disponibles, cantidad entera positiva; subtotales a 2 decimales | ✅ | enum de sku (D28), motor (D04, D16) |
| Fechas `AAAA-MM-DD`; `fecha_hora` `AAAA-MM-DDTHH:MM` (hora de Guatemala, nunca el reloj del sistema) | ✅ | parser estricto (D14); fecha de entrega con `fecha_hora` (D03) |
| Montos en quetzales | ✅ | catálogo en Q; mensaje `Total: Q840.00` |
| Cliente inexistente → 404 | ✅ | `{"error":"cliente_no_encontrado","mensaje":"No existe un cliente con cliente_id 'C999'."}` (resiliencia F, simulación) |
| Solicitud inválida (campos faltantes, mensajes vacíos, fecha mal formada) → 422 con mensaje que explica el motivo | ✅ | 5 variantes en `resiliencia_docker_flashlite.txt` (cuerpo vacío, sin mensajes, fecha inválida, mensajes no lista, JSON malformado); D22 |
| `GET /health` 200 con `estado` `ok`, o `degradado` + `motivo` | ✅ | `{"estado":"degradado","motivo":"MLflow no responde (...)"}` con MLflow detenido; también degrada si faltan datos o clave (D24) |

## Evaluación del funcionamiento (lo que hará el docente)

| Punto | Estado | Evidencia |
|---|---|---|
| Clonar el tag, poner los datos en la carpeta del README, `.env` con su clave y el modelo de `.env.example`, levantar sin intervención | ✅ | Simulación abajo; el modelo ya viene fijado en `.env.example` |
| Mensajes no publicados, situaciones fuera del catálogo | ✅ / ⚠️ | 55 propios (10 `fuera_catalogo`) + 14 de control: 100 % en la simulación. Reserva: el prompt se ajustó con dev/variantes/propios; casos realmente nuevos pueden fallar más (README, Limitaciones) |
| Solicitudes inválidas | ✅ | 422/404 verificados en Docker |
| MLflow interrumpido temporalmente | ✅ | `resiliencia_docker_flashlite.txt` |
| Latencia | ✅ | p50 0.83 s, p95 1.08 s por HTTP con 4 en paralelo |
| Estado, líneas, total, fecha y motivos correctos | ✅ / ⚠️ | 175/177 y 177/177 en las dos corridas en Docker. Reserva: con 4 solicitudes en paralelo OpenRouter devolvió 429 en 2 de 177 (503 de la API, sin pedido inventado); ver "Riesgo abierto" |
| Evidencia en MLflow, análisis y justificación | ✅ | `evidencia/mlflow/`, `evidencia/comparacion/`, `DECISIONES.md` |

## Entregables

| Entregable | Estado | Nota |
|---|---|---|
| Código de la aplicación, API, Docker y pruebas | ✅ | `src/`, `Dockerfile`, `docker-compose.yml`, `tests/` |
| `README.md` y `DECISIONES.md` | ✅ | |
| Casos propios en el formato de `mensajes_dev.jsonl` | ✅ | `eval/casos_propios.jsonl` (+ `casos_variantes.jsonl`, `casos_control.jsonl`) |
| `evidencia/` con la comparación y capturas de trazas y evaluaciones | ✅ | `evidencia/README.md` |
| Commit y push de este trabajo, PR y merge a `main` | ⚠️ | **Pendiente (persona):** los cambios de esta fase están sin commit en la rama `docs/entrega` |
| Tag `entrega-lab3` sobre el commit entregado | ⚠️ | **Pendiente (persona):** `git tag entrega-lab3 <commit de main>` y `git push origin entrega-lab3` |
| Repositorio accesible para el equipo docente | ⚠️ | **Pendiente (persona):** hoy es `PRIVATE`; hacerlo público o invitar al equipo docente |
| Enlace al repositorio en el GES | ⚠️ | **Pendiente (persona)** |
| Versión evaluada = último commit antes de la fecha límite | ⚠️ | Fecha límite por confirmar; no hacer commits a `main` después de ella |

## Simulación del docente (2026-11-23)

Copia limpia del árbol de trabajo (`git ls-files -co --exclude-standard`) en una carpeta temporal
fuera del repo, sin `datos/`, sin `.env` y sin `.venv`, siguiendo el README literalmente con
puertos de prueba (`API_PUERTO=8003 MLFLOW_PUERTO=5003`, proyecto `novapedidos-docente`):

1. `cp .env.example .env` y solo la clave → el resto de `.env` es idéntico a `.env.example`. ✅
2. `docker compose up -d --build` **sin datos** → la API arranca; `/health` =
   `degradado` con "Falta el archivo de datos /app/datos/productos.parquet"; `POST` → 503
   `datos_no_disponibles` (como dice el README). ✅
3. Copiar los 5 parquet a `datos/` **sin reiniciar** → `/health` = `ok` y el ejemplo del Anexo
   responde 200 con el pedido esperado. ✅
4. `curl` de 422 y 404 del README → 422 con el motivo de cada campo y 404. ✅
5. `docker compose stop mlflow` → `degradado` con motivo; `start` → `ok`. ✅
6. `uv sync` y el comando de evaluación por HTTP con `--mlflow` → **177/177**, p50 0.82 s, p95
   1.10 s, 0 errores; el run aparece en el experimento `novapedidos-evaluacion`. ✅
7. `uv run pytest -q` (465 pasan), `ruff check` y `ruff format --check` en la copia. ✅

Correcciones al README surgidas de la revisión: se documentó qué pasa si faltan los datos
(antes no se decía) y que la API los vuelve a cargar sin reiniciar; que la evaluación necesita
`uv`, `datos/` y `.env` (lee los datos para calificar); que `API_PUERTO`/`MLFLOW_PUERTO` son
variables del shell y que con otros puertos hay que cambiar los de los comandos; que por HTTP el
costo solo está en las trazas; y la forma de los errores y el 503. Ningún paso falló.

## Riesgo abierto

- **429 de OpenRouter con solicitudes en paralelo** (proveedor único de
  `gemini-2.5-flash-lite`): 2 de 177 solicitudes en la corrida final (`--concurrencia 4`), 0 en
  la simulación. El cliente reintenta una sola vez (`MAX_REINTENTOS = 1`, D30) y la API
  responde 503. Si el evaluador envía muchas solicitudes en paralelo, algún caso podría
  perderse. Mitigaciones posibles (no aplicadas en esta fase): más reintentos con espera solo
  para 429, o una lista de modelos de respaldo de OpenRouter.

## Otras verificaciones

- `uv run ruff check`, `uv run ruff format --check`, `uv run pytest -q` (465 pasan). ✅
- CI: se agregó el trabajo `imagen-docker` (`docker build` sin push y `docker compose config`). ✅
- Secretos: la clave real no aparece en archivos versionados ni nuevos, ni en el historial de
  git, ni en la imagen; no hay patrones `sk-or-v1-…` ni `Bearer …`. ✅
