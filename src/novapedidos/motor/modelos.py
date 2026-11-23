"""Estructura intermedia que recibe el motor: el pedido "ya entendido" (`DECISIONES.md` D15).

La llenan, en la Fase 5, el LLM (qué se pidió, cuánto, en qué unidad, con qué acción) y el
resolvedor de productos (texto → sku / ambiguo / no existe). Todo lo determinista (conversión
de unidades, sumas, correcciones, "lo de siempre", agotados, precios, fecha, crédito, estado y
mensaje) lo hace el motor. **No hay campos de precio, descuento ni crédito**: ningún mensaje
puede alterarlos (regla 3 de `CLAUDE.md`).

Los modelos son tolerantes a propósito (pocos validadores duros): el motor normaliza cualquier
combinación rara (un `sku` que no existe, un ambiguo sin opciones, cantidades ≤ 0) en lugar de
fallar, para que ninguna entrada termine en un 500.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: Texto del pendiente cuando se pide "lo de siempre" sin pedidos entregados (D18).
TEXTO_LO_DE_SIEMPRE = "lo de siempre"


class Resolucion(StrEnum):
    """A qué corresponde el texto de la mención en el catálogo."""

    SKU = "sku"  # un único producto del catálogo (campo `sku`)
    AMBIGUO = "ambiguo"  # varios productos posibles (campo `opciones`)
    NO_EXISTE = "no_existe"  # la distribuidora no lo vende


class Unidad(StrEnum):
    """En qué unidad dijo la cantidad el cliente."""

    VENTA = "venta"  # fardo, caja, display, quintal, "3 de …" o sin unidad
    UNIDAD_SUELTA = "unidad_suelta"  # botellas, latas, bolsas, sobres, unidades, paquetitos
    DOCENA = "docena"  # "una docena de …" (media docena = 6 en `unidad_suelta`)


class Accion(StrEnum):
    """Qué hace la mención sobre el pedido acumulado."""

    AGREGAR = "agregar"  # suma a lo que ya se pidió de ese producto
    CORREGIR = "corregir"  # reemplaza la cantidad acumulada ("no, mejor que sean 5")
    QUITAR = "quitar"  # elimina el producto ("sin las papalinas"), también de "lo de siempre"


class Mencion(BaseModel):
    """Un producto mencionado en la conversación, en orden de aparición."""

    model_config = ConfigDict(frozen=True)

    texto: str = Field(
        description="Fragmento que nombra el producto, sin cantidad ni unidad (p. ej. 'aceite')."
    )
    resolucion: Resolucion = Field(description="sku | ambiguo | no_existe")
    sku: str | None = Field(default=None, description="El sku, si resolucion = sku.")
    opciones: list[str] = Field(
        default_factory=list, description="Los sku posibles, si resolucion = ambiguo."
    )
    cantidad: int | None = Field(
        default=None, description="Cantidad dicha por el cliente; null si no dijo (vale 1)."
    )
    unidad: Unidad = Unidad.VENTA
    accion: Accion = Accion.AGREGAR


class InterpretacionResuelta(BaseModel):
    """Lo que el motor necesita saber de la conversación completa.

    - `es_pedido`: falso para consultas, agradecimientos o reclamos (→ `fuera_de_alcance`).
    - `lo_de_siempre`: el cliente pidió "lo de siempre" / "lo mismo de la otra vez".
    - `posicion_lo_de_siempre`: cuántas menciones van **antes** de esa expresión (0 = al
      inicio, lo habitual). Solo afecta el orden de las líneas y de los pendientes (D08).
    - `menciones`: productos en orden de aparición, concatenando los mensajes en orden.
    """

    model_config = ConfigDict(frozen=True)

    es_pedido: bool
    lo_de_siempre: bool = False
    posicion_lo_de_siempre: int = Field(default=0, ge=0)
    menciones: list[Mencion] = Field(default_factory=list)


__all__ = [
    "TEXTO_LO_DE_SIEMPRE",
    "Accion",
    "InterpretacionResuelta",
    "Mencion",
    "Resolucion",
    "Unidad",
]
