"""Esquema de salida estructurada del LLM y su conversión a `InterpretacionResuelta`.

El esquema se genera a partir del catálogo cargado: `sku` y `opciones` están restringidos a
los sku existentes (enum en el JSON Schema), así el modelo **no puede inventar** un sku
(`DECISIONES.md` D28). Es plano, sin valores por defecto (todos los campos son obligatorios,
como pide la salida estructurada estricta) y **sin campos de precio, descuento, crédito ni
fecha**: ningún mensaje puede alterar esos valores (regla 3 de `CLAUDE.md`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from novapedidos.motor.modelos import (
    Accion,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
)

NOMBRE_ESQUEMA = "InterpretacionPedido"

_DESC_TEXTO = (
    "Fragmento del mensaje que nombra el producto, sin cantidad, sin unidad de empaque y sin "
    "saludos (p. ej. 'kola 600', 'aceite', 'cerveza'). Para 'quitar', el producto a quitar."
)
_DESC_RESOLUCION = (
    "'sku' si el texto corresponde a un único producto del catálogo; 'ambiguo' si puede ser "
    "más de uno; 'no_existe' si la distribuidora no lo vende."
)
_DESC_SKU = "El sku elegido si resolucion='sku'; null en otro caso."
_DESC_OPCIONES = (
    "Si resolucion='ambiguo': TODOS los sku del catálogo que encajan (incluidos los "
    "agotados). Lista vacía en otro caso."
)
_DESC_CANTIDAD = "Número que dijo el cliente (en letras o cifras); null si no dijo cantidad."
_DESC_UNIDAD = (
    "'venta' (fardo, caja, display, quintal, '3 de …' o sin unidad); 'unidad_suelta' "
    "(botellas, latas, bolsas, unidades sueltas; media docena = 6); 'docena'."
)
_DESC_ACCION = (
    "'agregar' (pedido normal o 'también'); 'corregir' (el cliente cambia la cantidad de un "
    "producto ya pedido: 'no, mejor 5', 'corrijo: son 8'); 'quitar' ('sin X', 'quíteme X')."
)


def construir_esquema(skus: Iterable[str]) -> type[BaseModel]:
    """Modelo Pydantic de la salida del LLM con `sku`/`opciones` restringidos al catálogo."""
    lista = tuple(sorted(dict.fromkeys(skus)))
    if not lista:
        raise ValueError("El catálogo no tiene productos: no se puede construir el esquema")
    Sku = Literal.__getitem__(lista)  # type: ignore[attr-defined]  # enum de sku válidos

    config = ConfigDict(extra="forbid")
    mencion = create_model(
        "MencionLLM",
        __config__=config,
        __doc__="Un producto mencionado, en orden de aparición.",
        texto=(str, Field(description=_DESC_TEXTO)),
        resolucion=(Literal["sku", "ambiguo", "no_existe"], Field(description=_DESC_RESOLUCION)),
        sku=(Sku | None, Field(description=_DESC_SKU)),
        opciones=(list[Sku], Field(description=_DESC_OPCIONES)),
        cantidad=(int | None, Field(description=_DESC_CANTIDAD)),
        unidad=(Literal["venta", "unidad_suelta", "docena"], Field(description=_DESC_UNIDAD)),
        accion=(Literal["agregar", "corregir", "quitar"], Field(description=_DESC_ACCION)),
    )
    return create_model(
        NOMBRE_ESQUEMA,
        __config__=config,
        __doc__="Extracción estructurada de los mensajes de un tendero (sin precios).",
        es_pedido=(
            bool,
            Field(
                description="true si el cliente pide productos; false para consultas, "
                "agradecimientos, reclamos o saludos sin pedido."
            ),
        ),
        lo_de_siempre=(
            bool,
            Field(
                description="true si pide 'lo de siempre', 'lo mismo de la otra vez', 'mi "
                "pedido de siempre', 'lo de cada semana' o similar."
            ),
        ),
        menciones_antes_de_lo_de_siempre=(
            int,
            Field(
                description="Cuántas menciones aparecen ANTES de la expresión 'lo de "
                "siempre' (0 si va al inicio o si no la hay)."
            ),
        ),
        menciones=(list[mencion], Field(description="Productos en orden de aparición.")),
    )


def _valor(objeto: Any, campo: str) -> Any:
    if isinstance(objeto, dict):
        return objeto.get(campo)
    return getattr(objeto, campo, None)


def a_interpretacion(salida: Any) -> InterpretacionResuelta:
    """Convierte la salida del LLM (modelo Pydantic o dict) en `InterpretacionResuelta`.

    Tolerante: valores desconocidos caen a los valores seguros (`no_existe`, `venta`,
    `agregar`); el motor normaliza el resto (D15).
    """
    menciones: list[Mencion] = []
    for m in _valor(salida, "menciones") or []:
        texto = str(_valor(m, "texto") or "").strip()
        try:
            resolucion = Resolucion(_valor(m, "resolucion"))
        except ValueError:
            resolucion = Resolucion.NO_EXISTE
        try:
            unidad = Unidad(_valor(m, "unidad"))
        except ValueError:
            unidad = Unidad.VENTA
        try:
            accion = Accion(_valor(m, "accion"))
        except ValueError:
            accion = Accion.AGREGAR
        cantidad = _valor(m, "cantidad")
        sku = _valor(m, "sku")
        menciones.append(
            Mencion(
                texto=texto,
                resolucion=resolucion,
                sku=str(sku) if sku else None,
                opciones=[str(o) for o in (_valor(m, "opciones") or [])],
                cantidad=(
                    int(cantidad)
                    if isinstance(cantidad, int | float) and not isinstance(cantidad, bool)
                    else None
                ),
                unidad=unidad,
                accion=accion,
            )
        )
    lo_de_siempre = bool(_valor(salida, "lo_de_siempre"))
    posicion = _valor(salida, "menciones_antes_de_lo_de_siempre") or 0
    try:
        posicion = int(posicion)
    except (TypeError, ValueError):
        posicion = 0
    posicion = max(0, min(posicion, len(menciones))) if lo_de_siempre else 0
    return InterpretacionResuelta(
        es_pedido=bool(_valor(salida, "es_pedido")),
        lo_de_siempre=lo_de_siempre,
        posicion_lo_de_siempre=posicion,
        menciones=menciones,
    )


__all__ = ["NOMBRE_ESQUEMA", "a_interpretacion", "construir_esquema"]
