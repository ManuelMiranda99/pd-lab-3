"""Precios, descuentos por escala, subtotales y total (`politicas.md` §5).

Código puro, sin I/O. Todo en `decimal.Decimal` con redondeo mitad hacia arriba por subtotal y
total = suma de los subtotales ya redondeados (`DECISIONES.md` D04). El descuento se calcula por
línea con la cantidad final de la línea y la categoría del producto entregado (D05).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from novapedidos.datos import Escala, Producto

#: Cuanto de redondeo de los montos (2 decimales).
CENTAVO = Decimal("0.01")


def a_decimal(valor: Decimal | int | float | str) -> Decimal:
    """Convierte a `Decimal` pasando por `str` (evita arrastrar el error binario de `float`)."""
    return valor if isinstance(valor, Decimal) else Decimal(str(valor))


def redondear(monto: Decimal) -> Decimal:
    """Redondea a 2 decimales, mitad hacia arriba (`ROUND_HALF_UP`)."""
    return monto.quantize(CENTAVO, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class LineaCalculada:
    """Una línea del pedido con su precio, descuento y subtotal ya calculados."""

    sku: str
    cantidad: int
    precio_unitario: Decimal
    descuento_pct: int
    subtotal: Decimal


def _validar_cantidad(cantidad: int) -> None:
    if isinstance(cantidad, bool) or not isinstance(cantidad, int) or cantidad <= 0:
        raise ValueError(f"La cantidad debe ser un entero positivo: {cantidad!r}")


def descuento_pct(categoria: str, cantidad: int, escalas: Mapping[str, Iterable[Escala]]) -> int:
    """Mayor porcentaje de la categoría cuyo mínimo se alcanza (`cantidad ≥ cantidad_minima`).

    Devuelve 0 si no se alcanza ningún escalón o si la categoría no tiene escalas.
    """
    _validar_cantidad(cantidad)
    alcanzados = [
        e.descuento_pct for e in escalas.get(categoria, ()) if cantidad >= e.cantidad_minima
    ]
    return max(alcanzados, default=0)


def subtotal(cantidad: int, precio: Decimal | int | float | str, descuento: int) -> Decimal:
    """`cantidad × precio × (1 − descuento/100)`, redondeado a 0.01 mitad hacia arriba."""
    _validar_cantidad(cantidad)
    if not 0 <= descuento < 100:
        raise ValueError(f"Descuento fuera de rango: {descuento!r}")
    bruto = cantidad * a_decimal(precio) * (Decimal(100) - Decimal(descuento)) / Decimal(100)
    return redondear(bruto)


def calcular_linea(
    producto: Producto, cantidad: int, escalas: Mapping[str, Iterable[Escala]]
) -> LineaCalculada:
    """Calcula la línea de `cantidad` unidades de venta de `producto` (el producto entregado)."""
    pct = descuento_pct(producto.categoria, cantidad, escalas)
    return LineaCalculada(
        sku=producto.sku,
        cantidad=cantidad,
        precio_unitario=a_decimal(producto.precio),
        descuento_pct=pct,
        subtotal=subtotal(cantidad, producto.precio, pct),
    )


def total(lineas: Iterable[LineaCalculada]) -> Decimal:
    """Suma de los subtotales ya redondeados (sin recalcular desde el bruto). Vacío → 0.00."""
    return sum((ln.subtotal for ln in lineas), start=Decimal("0.00")).quantize(CENTAVO)
