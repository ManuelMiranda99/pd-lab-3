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
