# Imagen de la API de NovaPedidos (FastAPI + LangChain/OpenRouter).
# Sin datos ni credenciales: `datos/` se monta como volumen y la clave llega por `.env`
# (env_file de docker-compose.yml). Ver DECISIONES.md D45–D48.

FROM python:3.11-slim

# uv fijado a una versión concreta (binario estático desde su imagen oficial).
COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1) Dependencias (capa cacheable: solo cambia si cambian pyproject.toml o uv.lock).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# 2) El paquete del proyecto.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# Usuario sin privilegios; `datos/` es el punto de montaje (solo lectura) de los parquet.
RUN useradd --create-home --uid 10001 novapedidos \
    && mkdir -p /app/datos \
    && chown -R novapedidos:novapedidos /app/datos
USER novapedidos

ENV DATOS_DIR=/app/datos

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "novapedidos.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
