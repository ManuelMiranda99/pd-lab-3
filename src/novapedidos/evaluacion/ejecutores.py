"""Cómo se obtiene la respuesta de un caso: en proceso o contra la API por HTTP (D35).

Un ejecutor es un invocable `caso -> Ejecucion`, seguro para usar desde varios hilos:

- `EjecutorLocal`: `InterpreteLLM` (LLM + resolución) + `armar_pedido`, sin servidor. Da los
  metadatos de la llamada al modelo (tokens, costo, latencia del LLM). `--modelo` y
  `--sin-resolucion` sirven para comparar alternativas (Fase 10).
- `EjecutorHTTP`: `POST {url}/v1/pedidos/interpretar` (la API local o el contenedor de la
  Fase 9). Mide la latencia de punta a punta desde el cliente; la API no expone tokens ni
  costo, así que esos campos quedan vacíos.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx

from novapedidos.config import Configuracion, obtener_configuracion
from novapedidos.datos import Datos
from novapedidos.llm.cliente import crear_modelo_chat
from novapedidos.llm.interfaz import ErrorInterprete
from novapedidos.llm.interprete import InterpreteLLM
from novapedidos.motor import armar_pedido, parsear_fecha_hora

RUTA_INTERPRETAR = "/v1/pedidos/interpretar"


@dataclass(slots=True)
class Ejecucion:
    """Lo que devolvió el sistema para un caso."""

    obtenido: dict[str, Any] | None = None
    latencia_s: float | None = None  # de punta a punta (intérprete + motor, o HTTP)
    codigo_http: int | None = None
    error: str | None = None
    interpretacion: dict[str, Any] | None = None
    meta_llm: dict[str, Any] = field(default_factory=dict)

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


class Ejecutor(Protocol):
    descripcion: str

    def __call__(self, caso: dict[str, Any]) -> Ejecucion: ...


class EjecutorLocal:
    """Intérprete LLM + motor en el mismo proceso."""

    def __init__(
        self,
        datos: Datos,
        *,
        modelo: str | None = None,
        resolver: bool = True,
        config: Configuracion | None = None,
    ) -> None:
        config = config or obtener_configuracion()
        if not config.tiene_clave:
            raise ErrorInterprete("falta OPENROUTER_API_KEY en .env")
        self.datos = datos
        self.modelo = modelo or config.openrouter_model
        self.resolver = resolver
        self._interprete = InterpreteLLM(
            lambda: datos,
            lambda: crear_modelo_chat(config, modelo=self.modelo),
            nombre_modelo=self.modelo,
            resolver=resolver,
        )
        self.descripcion = (
            f"local · modelo {self.modelo} · resolución post-LLM {'sí' if resolver else 'no'}"
        )

    def __call__(self, caso: dict[str, Any]) -> Ejecucion:
        entrada = caso["entrada"]
        inicio = time.perf_counter()
        try:
            fecha_hora = parsear_fecha_hora(entrada["fecha_hora"])
            interp, meta = self._interprete.interpretar_con_metadatos(
                entrada["cliente_id"], list(entrada["mensajes"]), fecha_hora
            )
            respuesta = armar_pedido(entrada["cliente_id"], fecha_hora, interp, self.datos)
        except ErrorInterprete as exc:
            meta = self._interprete.ultima_llamada
            return Ejecucion(
                latencia_s=time.perf_counter() - inicio,
                error=f"ErrorInterprete: {exc}",
                meta_llm=meta.como_dict() if meta else {},
            )
        except Exception as exc:  # noqa: BLE001 - un caso roto no detiene la corrida
            return Ejecucion(
                latencia_s=time.perf_counter() - inicio, error=f"{type(exc).__name__}: {exc}"
            )
        return Ejecucion(
            obtenido=respuesta.model_dump(mode="json"),
            latencia_s=time.perf_counter() - inicio,
            codigo_http=200,
            interpretacion=interp.model_dump(mode="json"),
            meta_llm=meta.como_dict(),
        )


class EjecutorHTTP:
    """Cliente de la API (`--url http://127.0.0.1:8001`)."""

    def __init__(self, url: str, *, timeout_s: float = 60.0) -> None:
        self.url = url.rstrip("/")
        self._cliente = httpx.Client(base_url=self.url, timeout=timeout_s)
        self.descripcion = f"HTTP · {self.url}{RUTA_INTERPRETAR}"

    def __call__(self, caso: dict[str, Any]) -> Ejecucion:
        inicio = time.perf_counter()
        try:
            r = self._cliente.post(RUTA_INTERPRETAR, json=caso["entrada"])
        except httpx.HTTPError as exc:
            return Ejecucion(
                latencia_s=time.perf_counter() - inicio, error=f"{type(exc).__name__}: {exc}"
            )
        latencia = time.perf_counter() - inicio
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = {"texto": r.text[:300]}
        if r.status_code != 200:
            return Ejecucion(
                latencia_s=latencia,
                codigo_http=r.status_code,
                error=f"HTTP {r.status_code}: {cuerpo}",
            )
        return Ejecucion(obtenido=cuerpo, latencia_s=latencia, codigo_http=200)

    def cerrar(self) -> None:
        self._cliente.close()


__all__ = ["RUTA_INTERPRETAR", "Ejecucion", "Ejecutor", "EjecutorHTTP", "EjecutorLocal"]
