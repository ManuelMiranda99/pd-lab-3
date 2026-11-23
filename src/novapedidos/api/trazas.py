"""Gancho para registrar cada solicitud en MLflow (se implementa con las trazas de MLflow).

La API llama a `registrador.registrar(...)` después de responder cada pedido (éxito o error),
dentro de un `try/except`: un registrador que falla **nunca** afecta la respuesta (regla 6 de
`CLAUDE.md`). La implementación real debe además ser no bloqueante y con timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from novapedidos.api.esquemas import SolicitudPedido
from novapedidos.contrato import RespuestaPedido


@dataclass(frozen=True, slots=True)
class RegistroSolicitud:
    """Lo que se traza de una solicitud."""

    solicitud: SolicitudPedido
    codigo_http: int
    latencia_s: float
    respuesta: RespuestaPedido | None = None
    error: str | None = None  # código de error de la API (p. ej. "interprete_no_disponible")


class RegistradorTrazas(Protocol):
    """Destino de las trazas (MLflow u otro)."""

    def registrar(self, registro: RegistroSolicitud) -> None: ...


class RegistradorNulo:
    """No registra nada (valor por defecto hasta que existan las trazas de MLflow)."""

    def registrar(self, registro: RegistroSolicitud) -> None:
        return None


__all__ = ["RegistradorNulo", "RegistradorTrazas", "RegistroSolicitud"]
