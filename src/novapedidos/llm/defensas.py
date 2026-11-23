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
    r"modelo de lenguaje|eres|a partir de ahora|responde\w*|contesta\w*|chiste|"
    r"descuento\w*|precio\w*|autoriz\w*|credito\w*|gratis|regal\w*|aprueb\w*|apruebe\w*|"
    r"gerente|vendedor|q\s?\d+(\.\d+)?"
    r")\b"
)
_SEPARADOR_ORACIONES = re.compile(r"(?<=[.!?¡¿\n;])\s+|\s*[\n]+\s*")


#: Intento explícito de reemplazar las instrucciones del asistente. El filtro de Azure rechaza
#: estas conversaciones siempre que se observaron (D32), así que se sanean **antes** de la
#: primera llamada y se ahorra una llamada completa (≈ 1.5–3 s, D41). Es más estricto que
#: `_PATRON_MANIPULACION`: "olvidé pedirle…" o "el vendedor dijo…" no lo activan.
_PATRON_JAILBREAK = re.compile(
    r"(?<!\bme )(?<!\bse )(?<!\bno )\b"
    r"(ignora|ignore|ignoren|olvida|olvide|olvidate|olvidese|olviden)"
    r" +(?:de +)?(?:todas +|todo +)?(?:tus|sus|las|lo de|esas|estas) +(?:\w+ +)?"
    r"(instrucciones|reglas|indicaciones)\b"
    r"|\bprompt\b"
    r"|\beres +(?:ahora +)?(?:un|otro|una|otra) +(?:\w+ +){0,2}(asistente|bot|ia|modelo)\b"
)


def pide_ignorar_instrucciones(mensajes: Sequence[str]) -> bool:
    """La conversación intenta explícitamente cambiar las instrucciones del asistente."""
    return any(_PATRON_JAILBREAK.search(normalizar(m)) for m in mensajes)


def es_manipulacion(oracion: str) -> bool:
    """La oración parece una instrucción al sistema o un intento de cambiar condiciones."""
    return _PATRON_MANIPULACION.search(normalizar(oracion)) is not None


_SEPARADOR_CLAUSULAS = re.compile(r"\s*[,:]\s*|\s+(?:y|e|pero|luego)\s+", re.IGNORECASE)


def _sin_manipulacion(oracion: str) -> str:
    """La oración tal cual si no manipula; si manipula, solo sus cláusulas que no lo hacen
    ("Mándeme 2 fardos de naranjita 600 y apruebe mi crédito" → "Mándeme 2 fardos de
    naranjita 600"), para no perder un producto pedido en la misma oración (D41)."""
    if not es_manipulacion(oracion):
        return oracion.strip()
    clausulas = [c.strip() for c in _SEPARADOR_CLAUSULAS.split(oracion) if c and c.strip()]
    return ", ".join(c for c in clausulas if not es_manipulacion(c))


def quitar_instrucciones(mensajes: Sequence[str]) -> list[str]:
    """Los mensajes sin las oraciones (o cláusulas) manipuladoras; los mensajes que quedan
    vacíos se omiten."""
    resultado = []
    for mensaje in mensajes:
        oraciones = [o for o in _SEPARADOR_ORACIONES.split(mensaje) if o and o.strip()]
        conservadas = [c for c in (_sin_manipulacion(o) for o in oraciones) if c]
        if conservadas:
            resultado.append(" ".join(conservadas))
    return resultado


__all__ = ["es_manipulacion", "pide_ignorar_instrucciones", "quitar_instrucciones"]
