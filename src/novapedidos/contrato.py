"""Contrato de la respuesta de `POST /v1/pedidos/interpretar` (Anexo del enunciado).

Lo producen el motor (`novapedidos.motor.armar_pedido`) y lo devuelve la API tal cual. Los
montos viajan dentro del programa como `Decimal` (`DECISIONES.md` D04, D14) y se serializan a
JSON como **número con 2 decimales**; `fecha_entrega` como `AAAA-MM-DD` o `null`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

#: 2 decimales (mismo cuanto que `novapedidos.motor.precios.CENTAVO`; sin importar el motor
#: para que el contrato no dependa de él).
CENTAVO = Decimal("0.01")


def _monto_json(valor: Decimal) -> float:
    """`Decimal` → número JSON con 2 decimales (el redondeo ya lo hizo el motor)."""
    return float(Decimal(valor).quantize(CENTAVO))


#: Monto en quetzales: `Decimal` en Python, número con 2 decimales en JSON.
Monto = Annotated[Decimal, PlainSerializer(_monto_json, return_type=float, when_used="json")]


class EstadoPedido(StrEnum):
    """Estado del pedido (`politicas.md` §8)."""

    CONFIRMADO = "confirmado"
    REQUIERE_REVISION = "requiere_revision"
    FUERA_DE_ALCANCE = "fuera_de_alcance"


class MotivoRevision(StrEnum):
    """Motivos de `motivos_revision` y de `pendientes[].motivo`.

    `credito_excedido` solo aparece en `motivos_revision`, nunca en un pendiente.
    """

    PRODUCTO_AMBIGUO = "producto_ambiguo"
    PRODUCTO_NO_EXISTE = "producto_no_existe"
    AGOTADO_SIN_SUSTITUTO = "agotado_sin_sustituto"
    CANTIDAD_NO_CONVERTIBLE = "cantidad_no_convertible"
    CREDITO_EXCEDIDO = "credito_excedido"


class _Modelo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LineaPedido(_Modelo):
    """Una línea del pedido: producto del catálogo, disponible, en unidades de venta."""

    sku: str
    cantidad: int = Field(ge=1)
    precio_unitario: Monto
    descuento_pct: int = Field(ge=0, lt=100)
    subtotal: Monto


class Sustitucion(_Modelo):
    """Un producto agotado reemplazado por su sustituto con la misma cantidad."""

    sku_solicitado: str
    sku_entregado: str
    cantidad: int = Field(ge=1)


class Pendiente(_Modelo):
    """Algo que una persona debe resolver antes de enviar el pedido a bodega."""

    motivo: MotivoRevision
    texto: str
    opciones: list[str] = Field(default_factory=list)

    @field_validator("motivo")
    @classmethod
    def _sin_credito(cls, motivo: MotivoRevision) -> MotivoRevision:
        if motivo is MotivoRevision.CREDITO_EXCEDIDO:
            raise ValueError("credito_excedido solo va en motivos_revision, no en pendientes")
        return motivo


class RespuestaPedido(_Modelo):
    """Respuesta 200 de `POST /v1/pedidos/interpretar`."""

    cliente_id: str
    estado: EstadoPedido
    lineas: list[LineaPedido] = Field(default_factory=list)
    total: Monto
    fecha_entrega: date | None
    sustituciones: list[Sustitucion] = Field(default_factory=list)
    pendientes: list[Pendiente] = Field(default_factory=list)
    motivos_revision: list[MotivoRevision] = Field(default_factory=list)
    mensaje_cliente: str


__all__ = [
    "EstadoPedido",
    "LineaPedido",
    "Monto",
    "MotivoRevision",
    "Pendiente",
    "RespuestaPedido",
    "Sustitucion",
]
