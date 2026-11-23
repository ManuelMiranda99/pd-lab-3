"""Prueba de acceso a OpenRouter con LangChain y salida estructurada.

Uso:
    uv run python scripts/ping_llm.py [--modelo proveedor/modelo]

Lee `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` y `OPENROUTER_BASE_URL` de `.env` (vía
`novapedidos.config`) y muestra: modelo, respuesta estructurada, latencia, tokens y costo.
Nunca imprime la clave.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import httpx
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from novapedidos.config import obtener_configuracion


class LineaPrueba(BaseModel):
    """Extracción mínima de prueba: un producto con su cantidad."""

    producto: str = Field(description="Nombre del producto mencionado, tal como aparece")
    cantidad: int = Field(description="Cantidad pedida (entero)")
    unidad: str | None = Field(default=None, description="Unidad mencionada, si la hay")


MENSAJE = "Buenas don, me manda 3 fardos de agua pura 600 porfa"


def _costo_desde_generacion(base_url: str, clave: str, id_generacion: str) -> float | None:
    """Plan B: consulta `/generation?id=` de OpenRouter (puede tardar unos segundos en existir)."""
    for _ in range(3):
        try:
            r = httpx.get(
                f"{base_url.rstrip('/')}/generation",
                params={"id": id_generacion},
                headers={"Authorization": f"Bearer {clave}"},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json().get("data", {}).get("total_cost")
        except httpx.HTTPError:
            pass
        time.sleep(1.5)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Llamada de prueba a OpenRouter.")
    parser.add_argument("--modelo", help="Modelo de OpenRouter (por defecto OPENROUTER_MODEL)")
    args = parser.parse_args(argv)

    config = obtener_configuracion()
    if not config.tiene_clave:
        print(
            "Falta OPENROUTER_API_KEY. Copia .env.example a .env y pega tu clave de "
            "https://openrouter.ai/keys en la línea OPENROUTER_API_KEY=",
            file=sys.stderr,
        )
        return 2

    modelo = args.modelo or config.openrouter_model
    clave = config.openrouter_api_key.get_secret_value()
    llm = ChatOpenAI(
        model=modelo,
        base_url=config.openrouter_base_url,
        api_key=config.openrouter_api_key,
        temperature=0,
        timeout=60,
        max_retries=1,
        extra_body={"usage": {"include": True}},
    )
    extractor = llm.with_structured_output(LineaPrueba, method="json_schema", include_raw=True)

    inicio = time.perf_counter()
    try:
        resultado: dict[str, Any] = extractor.invoke(
            [
                ("system", "Extrae el producto, la cantidad y la unidad del mensaje del cliente."),
                ("human", MENSAJE),
            ]
        )
    except Exception as error:  # se reporta sin traceback
        print(
            f"Error al llamar a OpenRouter con {modelo}: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    latencia = time.perf_counter() - inicio

    crudo = resultado["raw"]
    meta = getattr(crudo, "response_metadata", {}) or {}
    uso = meta.get("token_usage") or {}
    uso_lc = getattr(crudo, "usage_metadata", None) or {}
    entrada = uso.get("prompt_tokens", uso_lc.get("input_tokens"))
    salida = uso.get("completion_tokens", uso_lc.get("output_tokens"))
    costo = uso.get("cost")
    origen_costo = "usage.cost"
    if costo is None and meta.get("id"):
        costo = _costo_desde_generacion(config.openrouter_base_url, clave, meta["id"])
        origen_costo = "/generation"

    print(f"Modelo pedido:      {modelo}")
    print(f"Modelo que respondió: {meta.get('model_name', '?')}")
    print(f"Mensaje:            {MENSAJE}")
    if resultado.get("parsing_error"):
        print(f"Error de parseo:    {resultado['parsing_error']}")
    print(f"Respuesta:          {resultado.get('parsed')!r}")
    print(f"Latencia:           {latencia:.2f} s")
    print(f"Tokens entrada/salida: {entrada} / {salida}")
    if costo is not None:
        print(f"Costo:              USD {float(costo):.8f} (fuente: {origen_costo})")
    else:
        print("Costo:              no reportado por OpenRouter")
    return 0 if resultado.get("parsed") is not None else 1


if __name__ == "__main__":
    sys.exit(main())
