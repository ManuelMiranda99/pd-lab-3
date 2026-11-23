"""Corre `eval/correr.py` registrando cada intento HTTP al modelo y la duración de cada caso.

Uso: `uv run python sonda.py <salida.jsonl> [argumentos de eval/correr.py]`. Envuelve el
transporte `httpx2.HTTPTransport.handle_request` (el que usa `langchain-openai`): una línea por
intento (caso, inicio, duración hasta las cabeceras, estado) y una por caso. Sirve para
distinguir reintentos de red de latencia del proveedor (D41). No registra cabeceras ni la clave.
"""

import contextvars
import json
import sys
import threading
import time

import httpx2

from novapedidos.evaluacion import cli, ejecutores

CASO: contextvars.ContextVar[str | None] = contextvars.ContextVar("caso", default=None)
_candado = threading.Lock()
_original_http = httpx2.HTTPTransport.handle_request
_original_caso = ejecutores.EjecutorLocal.__call__


def _escribir(registro: dict) -> None:
    with _candado, open(sys.argv[1], "a", encoding="utf-8") as f:
        f.write(json.dumps(registro) + "\n")


def _intento(self, request):
    t0, inicio, estado, error = time.perf_counter(), time.time(), None, None
    try:
        respuesta = _original_http(self, request)
        estado = respuesta.status_code
        return respuesta
    except Exception as exc:
        error = type(exc).__name__
        raise
    finally:
        dur = round(time.perf_counter() - t0, 3)
        _escribir({"caso": CASO.get(), "inicio": round(inicio, 3), "dur": dur, "estado": estado,
                   "err": error})  # fmt: skip


def _caso(self, caso):
    CASO.set(caso["id"])
    inicio = time.time()
    try:
        return _original_caso(self, caso)
    finally:
        dur = round(time.time() - inicio, 3)
        _escribir({"caso": caso["id"], "tipo": "caso", "inicio": round(inicio, 3), "dur": dur})


httpx2.HTTPTransport.handle_request = _intento
ejecutores.EjecutorLocal.__call__ = _caso

if __name__ == "__main__":
    open(sys.argv[1], "w").close()
    sys.exit(cli.main(sys.argv[2:]))
