"""Tiempo de generación del proveedor para cada caso de una corrida (API de OpenRouter).

Uso: `uv run python gen.py eval/resultados/<marca>/ [...] > salida.jsonl`. Consulta
`GET <base>/generation?id=<id_generacion>` y compara la latencia vista por el cliente con la
de generación del proveedor (D41). La clave se lee de la configuración y nunca se imprime.
"""

import json
import sys
from pathlib import Path

import httpx

from novapedidos.config import obtener_configuracion

config = obtener_configuracion()
clave = config.openrouter_api_key.get_secret_value()
base = config.openrouter_base_url.rstrip("/")
for carpeta in map(Path, sys.argv[1:]):
    with (carpeta / "resultados.jsonl").open(encoding="utf-8") as f:
        resultados = [json.loads(linea) for linea in f]
    for r in resultados:
        meta = r.get("meta_llm") or {}
        if not meta.get("id_generacion"):
            continue
        try:
            datos = (
                httpx.get(
                    f"{base}/generation",
                    params={"id": meta["id_generacion"]},
                    headers={"Authorization": f"Bearer {clave}"},
                    timeout=20,
                )
                .json()
                .get("data")
                or {}
            )
        except httpx.HTTPError as exc:
            datos = {"error": type(exc).__name__}
        registro = {
            "run": carpeta.name,
            "id": r["id"],
            "lat_cliente": round(r["latencia_s"], 2),
            "lat_llm": round(meta.get("latencia_s") or 0, 2),
            "or_latency": datos.get("latency"),
            "gen_time": datos.get("generation_time"),
            "moderation": datos.get("moderation_latency"),
            "provider": datos.get("provider_name"),
            "cache": datos.get("native_tokens_cached"),
            "created": datos.get("created_at"),
        }
        print(json.dumps(registro))
