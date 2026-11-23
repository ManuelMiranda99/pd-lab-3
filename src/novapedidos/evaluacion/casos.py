"""Casos de evaluación: lectura de `.jsonl` y construcción con `esperado` del motor (D34).

Formato de cada línea (el de `ejemplos/mensajes_dev.jsonl`):
`{id, tipo, entrada{cliente_id, fecha_hora, mensajes}, esperado{estado, lineas, total,
fecha_entrega, sustituciones, pendientes, motivos_revision}}`.

Para casos nuevos (variantes de la Fase 6, casos propios de la Fase 7) el `esperado` **no se
escribe a mano**: se escribe a mano la interpretación (qué productos, cuánto, qué acción) con
el mini-lenguaje `sku()`, `ambiguo()`, `no_existe()`, `pedido()` y el motor calcula líneas,
precios, descuentos, total, fecha, crédito, pendientes y estado (`construir_caso`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from novapedidos.datos import Datos
from novapedidos.motor import (
    Accion,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
    armar_pedido,
    parsear_fecha_hora,
)

#: Campos de `esperado` (la respuesta del contrato sin `cliente_id` ni `mensaje_cliente`).
CAMPOS_ESPERADO = (
    "estado",
    "lineas",
    "total",
    "fecha_entrega",
    "sustituciones",
    "pendientes",
    "motivos_revision",
)


def cargar_casos(rutas: Iterable[Path | str]) -> list[dict[str, Any]]:
    """Lee uno o varios `.jsonl`; cada caso conserva su archivo de origen en `_origen`."""
    casos: list[dict[str, Any]] = []
    for ruta in map(Path, rutas):
        with ruta.open(encoding="utf-8") as f:
            for numero, linea in enumerate(f, start=1):
                if not linea.strip():
                    continue
                caso = json.loads(linea)
                faltan = {"id", "tipo", "entrada"} - caso.keys()
                if faltan:
                    raise ValueError(f"{ruta}:{numero}: faltan campos {sorted(faltan)}")
                caso["_origen"] = ruta.name
                casos.append(caso)
    ids = [c["id"] for c in casos]
    repetidos = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos:
        raise ValueError(f"ids repetidos entre los archivos: {repetidos}")
    return casos


# --------------------------------------------------------------------------- mini-lenguaje

SUELTA = Unidad.UNIDAD_SUELTA
DOCENA = Unidad.DOCENA
CORREGIR = Accion.CORREGIR
QUITAR = Accion.QUITAR


def sku(
    texto: str,
    codigo: str,
    cantidad: int | None = None,
    unidad: Unidad = Unidad.VENTA,
    accion: Accion = Accion.AGREGAR,
) -> Mencion:
    """Mención resuelta a un sku del catálogo."""
    return Mencion(
        texto=texto,
        resolucion=Resolucion.SKU,
        sku=codigo,
        cantidad=cantidad,
        unidad=unidad,
        accion=accion,
    )


def ambiguo(texto: str, opciones: list[str], cantidad: int | None = None) -> Mencion:
    """Mención que puede ser varios productos (`producto_ambiguo`)."""
    return Mencion(texto=texto, resolucion=Resolucion.AMBIGUO, opciones=opciones, cantidad=cantidad)


def no_existe(texto: str, cantidad: int | None = None) -> Mencion:
    """Mención de algo que la distribuidora no vende (`producto_no_existe`)."""
    return Mencion(texto=texto, resolucion=Resolucion.NO_EXISTE, cantidad=cantidad)


def pedido(
    *menciones: Mencion, siempre: bool = False, es_pedido: bool = True, posicion: int = 0
) -> InterpretacionResuelta:
    """Interpretación completa de una conversación."""
    return InterpretacionResuelta(
        es_pedido=es_pedido,
        lo_de_siempre=siempre,
        posicion_lo_de_siempre=posicion,
        menciones=list(menciones),
    )


FUERA = pedido(es_pedido=False)


def construir_caso(
    id_caso: str,
    tipo: str,
    cliente_id: str,
    fecha_hora: str,
    mensajes: list[str],
    interpretacion: InterpretacionResuelta,
    datos: Datos,
) -> dict[str, Any]:
    """Caso en formato dev con `esperado` calculado por `armar_pedido` (nunca a mano)."""
    respuesta = armar_pedido(cliente_id, parsear_fecha_hora(fecha_hora), interpretacion, datos)
    completo = respuesta.model_dump(mode="json")
    return {
        "id": id_caso,
        "tipo": tipo,
        "entrada": {"cliente_id": cliente_id, "fecha_hora": fecha_hora, "mensajes": mensajes},
        "esperado": {campo: completo[campo] for campo in CAMPOS_ESPERADO},
    }


def escribir_casos(casos: Iterable[dict[str, Any]], ruta: Path | str) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8") as f:
        for caso in casos:
            f.write(json.dumps(caso, ensure_ascii=False) + "\n")


__all__ = [
    "CAMPOS_ESPERADO",
    "CORREGIR",
    "DOCENA",
    "FUERA",
    "QUITAR",
    "SUELTA",
    "ambiguo",
    "cargar_casos",
    "construir_caso",
    "escribir_casos",
    "no_existe",
    "pedido",
    "sku",
]
