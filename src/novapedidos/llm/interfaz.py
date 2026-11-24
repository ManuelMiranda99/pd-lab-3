"""Interfaz del intérprete de mensajes (lo que la API necesita del LLM).

La API no conoce LangChain: recibe cualquier objeto que cumpla `Interprete`. En las pruebas se
inyecta un `InterpreteFalso` (`novapedidos.api.interpretes`); el intérprete real con LangChain +
OpenRouter implementará este mismo protocolo (`DECISIONES.md` D25).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from novapedidos.motor.modelos import InterpretacionResuelta


class ErrorInterprete(RuntimeError):
    """El intérprete no pudo producir una interpretación (modelo caído, timeout, respuesta
    inválida, intérprete no configurado). La API responde 503 con un mensaje claro (D23)."""


@runtime_checkable
class Interprete(Protocol):
    """Convierte los mensajes de un cliente en la interpretación resuelta que recibe el motor.

    - `cliente_id`: cliente que escribe (ya validado: existe en los datos).
    - `mensajes`: los mensajes en orden, sin vacíos (ya validados por la API).
    - `fecha_hora`: recepción del último mensaje (hora de Guatemala, sin zona). Solo como
      contexto; la fecha de entrega la calcula el motor.

    Debe devolver una `InterpretacionResuelta` (solo extracción: nada de precios ni
    descuentos) o lanzar `ErrorInterprete`. Cualquier otra excepción también se trata como
    fallo del intérprete (503), pero lo correcto es envolverla en `ErrorInterprete`.
    """

    def interpretar(
        self, cliente_id: str, mensajes: list[str], fecha_hora: datetime
    ) -> InterpretacionResuelta: ...


__all__ = ["ErrorInterprete", "Interprete"]
