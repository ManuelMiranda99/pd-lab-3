"""Crédito del cliente (`politicas.md` §6, `DECISIONES.md` D12)."""

from __future__ import annotations

from decimal import Decimal

from novapedidos.datos import Cliente
from novapedidos.motor.precios import a_decimal


def credito_excedido(cliente: Cliente, total: Decimal | int | float | str) -> bool:
    """`saldo_pendiente + total > limite_credito` (estricto: igual al límite no excede)."""
    return cliente.saldo_pendiente + a_decimal(total) > cliente.limite_credito
