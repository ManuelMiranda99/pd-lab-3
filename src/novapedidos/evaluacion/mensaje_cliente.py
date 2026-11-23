"""Calificador por reglas del `mensaje_cliente` (`politicas.md` §9, `DECISIONES.md` D36).

Reglas verificadas con código (sin LLM), sobre la respuesta obtenida:

1. `no_vacio`: el mensaje tiene texto.
2. `longitud`: entre `LARGO_MINIMO` y `LARGO_MAXIMO` caracteres (breve, pero completo).
3. `espanol`: contiene palabras frecuentes del español.
4. `fecha`: si hay líneas y `fecha_entrega`, la menciona en español ("26 de noviembre"); si
   no hay fecha (`fuera_de_alcance`), no promete una entrega. Un pedido sin líneas (solo
   pendientes) no está obligado a dar fecha.
5. `agotados`: un producto agotado (sin disponibilidad en el catálogo) solo se nombra en una
   oración que dice que está agotado (nunca como entregado).
6. `asesor`: si el pedido requiere revisión, dice que un asesor lo confirmará.
7. `montos`: todo monto en quetzales que aparece (`Q1,234.50`) coincide con el `total`.

`fecha_referencia` permite exigir la fecha **esperada** en lugar de la obtenida.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from novapedidos.datos import Datos
from novapedidos.evaluacion.calificadores import Calificacion, montos_iguales, normalizar_texto
from novapedidos.motor.mensaje import MESES

LARGO_MINIMO = 20
LARGO_MAXIMO = 700
_PALABRAS_ESPANOL = ("gracias", "pedido", "su ", "el ", "de ", "le ", "por ")
_MONTO = re.compile(r"Q\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)")
_PROMESA_ENTREGA = re.compile(r"\bentreg", re.IGNORECASE)


def _oraciones(texto: str) -> list[str]:
    return [o for o in re.split(r"(?<=[.!?])\s+", texto) if o.strip()]


def reglas_mensaje(
    respuesta: Mapping[str, Any], datos: Datos, fecha_referencia: str | None = None
) -> dict[str, str]:
    """Reglas incumplidas: `{regla: detalle}` (vacío = mensaje correcto)."""
    mensaje = str(respuesta.get("mensaje_cliente") or "")
    fallos: dict[str, str] = {}
    if not mensaje.strip():
        return {"no_vacio": "mensaje vacío"}
    if not LARGO_MINIMO <= len(mensaje) <= LARGO_MAXIMO:
        fallos["longitud"] = f"{len(mensaje)} caracteres"
    normal = normalizar_texto(mensaje) + " "
    if not any(p in normal for p in _PALABRAS_ESPANOL):
        fallos["espanol"] = "no parece español"

    fecha_txt = fecha_referencia if fecha_referencia is not None else respuesta.get("fecha_entrega")
    hay_lineas = bool(respuesta.get("lineas"))
    if fecha_txt and hay_lineas:
        dia = date.fromisoformat(str(fecha_txt))
        buscado = f"{dia.day} de {MESES[dia.month - 1]}"
        if buscado not in mensaje.lower():
            fallos["fecha"] = f"no menciona «{buscado}»"
    elif not fecha_txt and _PROMESA_ENTREGA.search(mensaje):
        fallos["fecha"] = "promete una entrega sin fecha de entrega"

    for oracion in _oraciones(mensaje):
        oracion_n = normalizar_texto(oracion)
        for producto in datos.productos.values():
            if producto.disponible:
                continue
            if normalizar_texto(producto.nombre) in oracion_n and "agotad" not in oracion_n:
                fallos["agotados"] = f"nombra {producto.sku} agotado como entregado"

    if respuesta.get("estado") == "requiere_revision" and "asesor" not in normal:
        fallos["asesor"] = "requiere revisión y no dice que un asesor confirmará"

    for monto in _MONTO.findall(mensaje):
        if not montos_iguales(monto.replace(",", ""), respuesta.get("total")):
            fallos["montos"] = f"menciona Q{monto} y el total es {respuesta.get('total')}"
    return fallos


def calificar_mensaje_cliente(
    respuesta: Mapping[str, Any], datos: Datos, fecha_referencia: str | None = None
) -> Calificacion:
    """1 si el mensaje cumple todas las reglas; el detalle lista las incumplidas."""
    fallos = reglas_mensaje(respuesta, datos, fecha_referencia)
    detalle = "; ".join(f"{regla}: {d}" for regla, d in fallos.items())
    return Calificacion("mensaje_cliente", 0.0 if fallos else 1.0, detalle)


__all__ = ["LARGO_MAXIMO", "LARGO_MINIMO", "calificar_mensaje_cliente", "reglas_mensaje"]
