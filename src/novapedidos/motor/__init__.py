"""Motor de reglas: cálculos de negocio en código puro y probado (nunca el LLM)."""

from novapedidos.motor.credito import credito_excedido
from novapedidos.motor.fechas import (
    FORMATO_FECHA_HORA,
    HORA_CORTE,
    ErrorFechaHora,
    fecha_efectiva_recepcion,
    fecha_entrega,
    parsear_fecha_hora,
)
from novapedidos.motor.mensaje import redactar_mensaje
from novapedidos.motor.modelos import (
    TEXTO_LO_DE_SIEMPRE,
    Accion,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
)
from novapedidos.motor.pedido import (
    ErrorClienteInexistente,
    armar_pedido,
    convertir_a_unidades_de_venta,
)
from novapedidos.motor.precios import (
    LineaCalculada,
    a_decimal,
    calcular_linea,
    descuento_pct,
    redondear,
    subtotal,
    total,
)

__all__ = [
    "TEXTO_LO_DE_SIEMPRE",
    "Accion",
    "ErrorClienteInexistente",
    "InterpretacionResuelta",
    "Mencion",
    "Resolucion",
    "Unidad",
    "armar_pedido",
    "convertir_a_unidades_de_venta",
    "redactar_mensaje",
    "FORMATO_FECHA_HORA",
    "HORA_CORTE",
    "ErrorFechaHora",
    "LineaCalculada",
    "a_decimal",
    "calcular_linea",
    "credito_excedido",
    "descuento_pct",
    "fecha_efectiva_recepcion",
    "fecha_entrega",
    "parsear_fecha_hora",
    "redondear",
    "subtotal",
    "total",
]
