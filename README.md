# NovaPedidos — pedidos por WhatsApp con LLMs (Laboratorio 3)

Servicio que convierte mensajes de WhatsApp de los tenderos de **NovaDistribuidora** en pedidos
estructurados. El LLM (LangChain + OpenRouter, salida estructurada) solo **extrae**; precios,
descuentos, fechas, crédito y estado los calcula un motor de reglas en código probado.

> En construcción. Plan por fases en `PLAN.md`; bitácora de sesiones en `BITACORA.md`.

## Datos publicados (no están en el repo)

Coloca los 5 parquet publicados en la carpeta `datos/` en la raíz del repo (o define
`DATOS_DIR` en `.env`):

```
datos/
  productos.parquet  sinonimos.parquet  precios_escala.parquet
  clientes.parquet   historial_pedidos.parquet
```

`ejemplos/mensajes_dev.jsonl` (casos de desarrollo) sí se versiona: tiene el mismo formato que
usa el evaluador para sus casos.

## Puesta en marcha (desarrollo)

```bash
cp .env.example .env        # y pega tu OPENROUTER_API_KEY
uv sync                     # instala dependencias según uv.lock
uv run pytest -q            # pruebas (no requieren datos ni clave)
uv run ruff check           # lint
uv run python scripts/ping_llm.py   # prueba de acceso a OpenRouter
```

## Levantar con Docker

Requisitos: Docker con Compose v2. Desde la raíz del repo:

```bash
cp .env.example .env        # pega tu OPENROUTER_API_KEY (el modelo ya viene fijado)
# coloca los 5 parquet publicados en ./datos/ (ver "Datos publicados")
docker compose up --build   # la primera vez construye la imagen de la API (~1 min)
```

- **API**: <http://localhost:8000> (`/docs`, `GET /health`, `POST /v1/pedidos/interpretar`).
- **MLflow**: <http://localhost:5000> (experimento `novapedidos-api`: una traza por solicitud).
- Los datos se montan de solo lectura en el contenedor (`./datos:/app/datos:ro`); ni los datos
  ni `.env` entran en la imagen. MLflow guarda trazas y artefactos en el volumen
  `mlflow-datos` (`docker compose down -v` lo borra).
- Si los puertos están ocupados: `API_PUERTO=8080 MLFLOW_PUERTO=5050 docker compose up`.
  Otra carpeta de datos: `DATOS_HOST_DIR=/ruta/a/datos docker compose up`.
- Si MLflow se detiene (`docker compose stop mlflow`) o se cuelga, la API sigue respondiendo y
  `GET /health` devuelve `"estado": "degradado"`; al volver, las trazas siguen llegando sin
  reiniciar la API.
- Evaluar la API levantada:
  `uv run python eval/correr.py --url http://127.0.0.1:8000 ejemplos/mensajes_dev.jsonl`
  (agrega `--mlflow` para registrar la corrida en el MLflow del contenedor).
