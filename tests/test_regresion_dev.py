"""Regresión del motor de cálculos contra los 84 casos de `ejemplos/mensajes_dev.jsonl`.

A partir de las **líneas esperadas** (sku y cantidad) se recalculan con el motor el precio, el
descuento, el subtotal y el total; desde `fecha_hora` y el cliente, la fecha de entrega
(`null` en `fuera_de_alcance`); y se verifica que `credito_excedido` en `motivos_revision`
coincide con `credito_excedido(cliente, total)`.

Necesita los parquet reales: se salta si `datos/` no existe (la CI no tiene datos).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from novapedidos.datos import cargar_datos
from novapedidos.motor.credito import credito_excedido
from novapedidos.motor.fechas import fecha_entrega, parsear_fecha_hora
from novapedidos.motor.precios import calcular_linea, total

RAIZ = Path(__file__).resolve().parent.parent
DATOS_DIR = RAIZ / "datos"
CASOS_DEV = RAIZ / "ejemplos" / "mensajes_dev.jsonl"

pytestmark = [
    pytest.mark.requiere_datos,
    pytest.mark.skipif(
        not (DATOS_DIR / "productos.parquet").is_file(), reason="sin datos publicados en datos/"
    ),
]


def _casos() -> list[dict]:
    with CASOS_DEV.open(encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


CASOS = _casos()


@pytest.fixture(scope="module")
def datos():
    return cargar_datos(DATOS_DIR)


def _dec(valor: float) -> Decimal:
    """Los montos del JSON vienen como `float`; se comparan como `Decimal` de 2 decimales."""
    return Decimal(str(valor)).quantize(Decimal("0.01"))


def test_son_84_casos():
    assert len(CASOS) == 84


@pytest.mark.parametrize("caso", CASOS, ids=[c["id"] for c in CASOS])
def test_caso_dev(caso, datos):
    entrada, esperado = caso["entrada"], caso["esperado"]
    cliente = datos.cliente(entrada["cliente_id"])
    assert cliente is not None

    # Líneas y total.
    lineas = []
    for esp in esperado["lineas"]:
        linea = calcular_linea(datos.producto(esp["sku"]), esp["cantidad"], datos.escalas)
        assert linea.precio_unitario == _dec(esp["precio_unitario"]), esp["sku"]
        assert linea.descuento_pct == esp["descuento_pct"], esp["sku"]
        assert linea.subtotal == _dec(esp["subtotal"]), esp["sku"]
        lineas.append(linea)
    calculado = total(lineas)
    assert calculado == _dec(esperado["total"])

    # Fecha de entrega.
    if esperado["estado"] == "fuera_de_alcance":
        assert esperado["fecha_entrega"] is None
    else:
        fecha = fecha_entrega(parsear_fecha_hora(entrada["fecha_hora"]), cliente.dias_visita)
        assert fecha.isoformat() == esperado["fecha_entrega"]

    # Crédito (nunca en fuera_de_alcance, D12).
    esperado_credito = "credito_excedido" in esperado["motivos_revision"]
    if esperado["estado"] == "fuera_de_alcance":
        assert not esperado_credito
    else:
        assert credito_excedido(cliente, calculado) is esperado_credito
