"""Defensa de respaldo cuando el proveedor rechaza la conversación por su filtro de contenido.

`openai/gpt-4.1-nano` en OpenRouter se sirve desde Azure, cuyo filtro ("prompt shields")
rechaza con `finish_reason = content_filter` los mensajes que intentan manipular al modelo
("ignora tus instrucciones…", "olvida tus reglas…"). Sin respaldo, el pedido legítimo que viene
en la misma conversación se perdería (503).

Respaldo (`DECISIONES.md` D32): se parte cada mensaje en oraciones, se **descartan** las que
parecen instrucciones o intentos de cambiar precios/descuentos/crédito, y se reintenta **una**
vez con el resto. Si no queda nada, la conversación no contiene un pedido. Esto solo se usa
tras un rechazo del filtro: el tráfico normal nunca pasa por aquí, y descartar una oración
manipuladora no puede cambiar precios (los calcula el motor).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from novapedidos.llm.resolucion import normalizar

_PATRON_MANIPULACION = re.compile(
    r"\b("
    r"instruccion\w*|regla\w*|olvid\w*|ignor\w*|sistema|system|prompt|asistente|"
    r"modelo de lenguaje|responde\w*|contesta\w*|chiste|"
    r"descuento\w*|precio\w*|autoriz\w*|credito\w*|gratis|regal\w*|aprueb\w*|apruebe\w*|"
    r"gerente|vendedor|q\s?\d+(\.\d+)?"
    r")\b"
)
_SEPARADOR_ORACIONES = re.compile(r"(?<=[.!?¡¿\n;])\s+|\s*[\n]+\s*")


def es_manipulacion(oracion: str) -> bool:
    """La oración parece una instrucción al sistema o un intento de cambiar condiciones."""
    return _PATRON_MANIPULACION.search(normalizar(oracion)) is not None


def quitar_instrucciones(mensajes: Sequence[str]) -> list[str]:
    """Los mensajes sin las oraciones manipuladoras (los mensajes que quedan vacíos se omiten)."""
    resultado = []
    for mensaje in mensajes:
        oraciones = [o for o in _SEPARADOR_ORACIONES.split(mensaje) if o and o.strip()]
        conservadas = [o.strip() for o in oraciones if not es_manipulacion(o)]
        if conservadas:
            resultado.append(" ".join(conservadas))
    return resultado


__all__ = ["es_manipulacion", "quitar_instrucciones"]
