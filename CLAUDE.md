# CLAUDE.md — Reglas del proyecto

Servicio con LLMs para **NovaDistribuidora** (Lab 3, Product Development). `POST
/v1/pedidos/interpretar` recibe `cliente_id`, `fecha_hora` y los mensajes de WhatsApp de un tendero
y devuelve el pedido estructurado (líneas, total, fecha de entrega, sustituciones, pendientes,
motivos de revisión, estado y mensaje al cliente). `GET /health` reporta `ok` o `degradado`.

Stack: Python 3.11 · `uv` · LangChain + `langchain-openai` contra **OpenRouter** (salida
estructurada) · Pydantic / pydantic-settings · FastAPI (puerto 8000) · MLflow (trazas y
evaluación) · pandas + pyarrow · rapidfuzz · Docker Compose · pytest · ruff · GitHub Actions.

Fuentes de verdad: `Laboratorio_3_PD_26.pdf` (enunciado, fuera del repo), `politicas.md`
(reglas de negocio), `catalogo_problemas.md` (situaciones observadas, no exhaustivas) y
`ejemplos/mensajes_dev.jsonl` (casos con respuesta esperada).

## Cómo se trabaja (flujo con IA, visible en el repo)

- `PLAN.md`: plan por fases (0–11) con IDs `F#.#`, verificación manual por fase y matriz de
  cumplimiento contra el PDF. Los IDs son **solo internos**: nunca en commits, ramas ni PRs.
- `BITACORA.md`: una entrada por sesión; su "Próxima sesión" dice por dónde seguir.
- `/iniciar-fase [N]` y `/cerrar-fase` (en `.claude/skills/`) abren y cierran cada sesión
  (capturas en `evidencia/sesiones/AAAA-MM-DD_FN/`).
- **La sesión principal orquesta y cada fase la implementa un subagente** con un brief propio;
  la sesión principal verifica el resultado y actualiza `PLAN.md` y `BITACORA.md`.
- `DECISIONES.md` (desde la Fase 1): cada decisión con alternativas y justificación.
- **Modelos:** desarrollo y pruebas con modelos baratos de OpenRouter (ver
  `evidencia/modelos_candidatos.md`); en la Fase 10 se comparan con modelos recomendados y se
  fija en `.env.example` el que se entrega (el evaluador usa **ese** modelo con su clave).

## Comandos

```bash
uv sync                                  # dependencias según uv.lock
uv run pytest -q                         # pruebas (sin datos publicados ni clave)
uv run ruff check                        # lint (la CI falla si no pasa)
uv run ruff format                       # formato
cp .env.example .env                     # y pegar OPENROUTER_API_KEY
uv run python scripts/ping_llm.py        # prueba de acceso a OpenRouter (costo y latencia)
uv run python scripts/capturar.py <url> <salida.png> [--pagina-completa]   # evidencia
```

## Estructura

```
src/novapedidos/
  config.py     variables de entorno (pydantic-settings): OPENROUTER_*, MLFLOW_TRACKING_URI, DATOS_DIR
  datos/        carga y validación de los parquet publicados
  motor/        reglas de negocio en código puro (unidades, precios, descuentos, crédito, fecha, estado)
  llm/          intérprete LangChain + OpenRouter: solo extracción estructurada
  api/          FastAPI: contrato, /v1/pedidos/interpretar, /health
tests/          pytest (los que necesiten datos/ o la clave se saltan si no están)
eval/           evaluación local contra mensajes_dev.jsonl y casos propios
scripts/        ping_llm.py, capturar.py y utilidades
evidencia/      capturas por sesión, modelos candidatos, resultados de evaluación
```

## Reglas no negociables

1. **Cálculos de negocio con código probado, nunca con el LLM.** El LLM solo extrae menciones
   (texto, cantidad, unidad, acción, "lo de siempre", si es pedido). Precios, descuentos,
   conversiones, fechas, crédito y estado los calcula `motor/`.
2. **Todo lo que se consume del modelo usa salida estructurada** (esquemas Pydantic +
   `with_structured_output`). Nada de parsear texto libre.
3. **Ningún mensaje altera precios, descuentos, crédito ni el comportamiento.** El esquema de
   extracción no tiene campos de precio ni descuento; el motor ignora "autorizaciones" del cliente.
4. **Nunca inventar un `sku`.** Todo `sku` sale del catálogo; si no existe → `producto_no_existe`;
   si hay varias opciones → `producto_ambiguo` con las opciones.
5. **La fecha de entrega se calcula con `fecha_hora` de la solicitud, nunca con el reloj del
   sistema** (`datetime.now()`, `date.today()` prohibidos en la lógica).
6. **MLflow caído ⇒ la API sigue respondiendo.** Trazas no bloqueantes y con timeout; `/health`
   pasa a `degradado`.
7. **Datos y credenciales fuera del repo y de las imágenes.** `datos/`, `*.parquet`, `.env` y el
   PDF están en `.gitignore`; los datos se montan como volumen. Solo `.env.example` se versiona.
   Nunca imprimir ni copiar el valor de `OPENROUTER_API_KEY`.
8. **Pruebas de la API sin depender del modelo**: el intérprete LLM se inyecta y en tests se
   sustituye por uno falso. La CI no tiene datos ni clave.
9. **Contrato exacto del PDF**: nombres de campos, tipos y estados; ninguna entrada termina en 500.

## Convenciones

- Git: después de la Fase 0 **nunca se trabaja en `main`**. Ramas `feat/…`, `fix/…`, `test/…`,
  `docs/…`, `ci/…`, `chore/…`; PR con CI en verde y squash merge. Conventional Commits en
  español, con la firma de Claude cuando intervino.
- Commits, push y PRs solo con permiso explícito de la persona.
- Idioma: documentación, mensajes, nombres de dominio y docstrings en español.
- Estilo: ruff (line-length 100), type hints en funciones públicas.
- Dependencias con `uv add` (quedan fijadas en `uv.lock`); la CI usa `uv sync --locked`.
