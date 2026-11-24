"""Pruebas unitarias del motor de cálculos (`novapedidos.motor`): precios, fechas y crédito.

Usan dataclasses sintéticas (sin `datos/`), así que corren siempre, también en la CI.
Referencias: `politicas.md` §5–§7 y `DECISIONES.md` D03, D04, D05, D12.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from novapedidos.datos import Cliente, Escala, Producto
from novapedidos.motor.credito import credito_excedido
from novapedidos.motor.fechas import (
    ErrorFechaHora,
    fecha_efectiva_recepcion,
    fecha_entrega,
    parsear_fecha_hora,
)
from novapedidos.motor.precios import (
    LineaCalculada,
    calcular_linea,
    descuento_pct,
    subtotal,
    total,
)

ESCALAS = {
    "bebidas": (Escala(10, 3), Escala(20, 5)),
    "snacks": (Escala(10, 3),),
}

# Índices de `date.weekday()`.
LUN, MAR, MIE, JUE, VIE, SAB, DOM = range(7)


def producto(sku: str = "X01", categoria: str = "bebidas", precio: str = "120.00") -> Producto:
    return Producto(
        sku=sku,
        nombre=f"Producto {sku}",
        marca="M",
        categoria=categoria,
        presentacion="",
        unidad_venta="fardo",
        unidades_por_empaque=24,
        precio=Decimal(precio),
        disponible=True,
        sustituto_sku=None,
    )


def cliente(limite: str = "5000.00", saldo: str = "1000.00") -> Cliente:
    return Cliente(
        cliente_id="C1",
        nombre_tienda="Tienda",
        propietario="Ana",
        municipio="Mixco",
        dias_visita=(LUN, MIE, VIE),
        limite_credito=Decimal(limite),
        saldo_pendiente=Decimal(saldo),
    )


# =========================================================================== precios


class TestDescuento:
    def test_debajo_del_primer_minimo_es_cero(self):
        assert descuento_pct("bebidas", 9, ESCALAS) == 0

    def test_minimo_exacto_alcanza_la_escala(self):
        assert descuento_pct("bebidas", 10, ESCALAS) == 3

    def test_entre_escalones_usa_el_alcanzado(self):
        assert descuento_pct("bebidas", 19, ESCALAS) == 3

    def test_escalon_superior_exacto(self):
        assert descuento_pct("bebidas", 20, ESCALAS) == 5

    def test_por_encima_del_ultimo_escalon(self):
        assert descuento_pct("bebidas", 500, ESCALAS) == 5

    def test_categoria_sin_escalas_es_cero(self):
        assert descuento_pct("limpieza", 1000, ESCALAS) == 0

    def test_mayor_porcentaje_aunque_las_escalas_vengan_desordenadas(self):
        escalas = {"bebidas": (Escala(20, 5), Escala(10, 3), Escala(50, 8))}
        assert descuento_pct("bebidas", 60, escalas) == 8
        assert descuento_pct("bebidas", 25, escalas) == 5
        assert descuento_pct("bebidas", 12, escalas) == 3

    def test_cantidad_no_positiva_rechazada(self):
        with pytest.raises(ValueError):
            descuento_pct("bebidas", 0, ESCALAS)


class TestSubtotal:
    def test_sin_descuento(self):
        assert subtotal(3, Decimal("60.00"), 0) == Decimal("180.00")

    def test_con_descuento(self):
        # 24 × 120 × 0.95 = 2736.00
        assert subtotal(24, Decimal("120.00"), 5) == Decimal("2736.00")

    def test_redondeo_mitad_hacia_arriba_en_005(self):
        # 3 × 42.50 × 0.97 = 123.675 → 123.68 (mitad hacia arriba).
        assert subtotal(3, Decimal("42.50"), 3) == Decimal("123.68")
        # Con float + round() (redondeo bancario y error binario) saldría 123.67.
        assert round(3 * 42.50 * 0.97, 2) == 123.67

    def test_redondeo_hacia_abajo_debajo_de_la_mitad(self):
        # 1 × 10.33 × 0.97 = 10.0201 → 10.02
        assert subtotal(1, Decimal("10.33"), 3) == Decimal("10.02")

    def test_siempre_dos_decimales(self):
        resultado = subtotal(1, Decimal("5"), 0)
        assert resultado == Decimal("5.00")
        assert resultado.as_tuple().exponent == -2

    def test_acepta_float_sin_arrastrar_error_binario(self):
        assert subtotal(3, 42.5, 3) == Decimal("123.68")

    def test_cantidad_no_positiva_rechazada(self):
        with pytest.raises(ValueError):
            subtotal(0, Decimal("10"), 0)


class TestLineaYTotal:
    def test_calcular_linea_con_escala(self):
        linea = calcular_linea(producto(precio="120.00"), 24, ESCALAS)
        assert linea == LineaCalculada(
            sku="X01",
            cantidad=24,
            precio_unitario=Decimal("120.00"),
            descuento_pct=5,
            subtotal=Decimal("2736.00"),
        )

    def test_calcular_linea_minimo_exacto_snacks(self):
        linea = calcular_linea(producto("Y01", "snacks", "42.50"), 10, ESCALAS)
        assert linea.descuento_pct == 3
        assert linea.subtotal == Decimal("412.25")

    def test_calcular_linea_categoria_sin_escalas(self):
        linea = calcular_linea(producto("L01", "limpieza", "33.33"), 100, ESCALAS)
        assert linea.descuento_pct == 0
        assert linea.subtotal == Decimal("3333.00")

    def test_total_es_suma_de_subtotales_redondeados(self):
        # Cada línea: 1 × 0.335 → 0.34 (redondeado). Suma = 0.68; desde el bruto saldría 0.67.
        lineas = [
            calcular_linea(producto("A", "limpieza", "0.335"), 1, ESCALAS),
            calcular_linea(producto("B", "limpieza", "0.335"), 1, ESCALAS),
        ]
        assert [ln.subtotal for ln in lineas] == [Decimal("0.34"), Decimal("0.34")]
        assert total(lineas) == Decimal("0.68")

    def test_total_sin_lineas_es_cero(self):
        resultado = total([])
        assert resultado == Decimal("0.00")
        assert resultado.as_tuple().exponent == -2


# =========================================================================== fechas


class TestParserFechaHora:
    def test_formato_valido(self):
        assert parsear_fecha_hora("2026-11-28T14:53") == datetime(2026, 11, 28, 14, 53)

    @pytest.mark.parametrize(
        "texto",
        [
            "",
            "2026-11-28",
            "2026-11-28 14:53",
            "2026-11-28T14:53:00",
            "2026-11-28T4:53",
            "2026-1-28T14:53",
            "28/11/2026 14:53",
            "2026-11-28T14:53Z",
            " 2026-11-28T14:53",
            "2026-02-30T10:00",
            "2026-11-28T24:00",
            "2026-11-28T10:60",
            "abc",
        ],
    )
    def test_formato_invalido_lanza_error_claro(self, texto):
        with pytest.raises(ErrorFechaHora, match="AAAA-MM-DDTHH:MM"):
            parsear_fecha_hora(texto)

    def test_no_texto_rechazado(self):
        with pytest.raises(ErrorFechaHora):
            parsear_fecha_hora(None)  # type: ignore[arg-type]

    def test_error_es_value_error(self):
        assert issubclass(ErrorFechaHora, ValueError)


class TestFechaEfectiva:
    def test_antes_del_corte_mismo_dia(self):
        assert fecha_efectiva_recepcion(datetime(2026, 11, 24, 13, 59)) == date(2026, 11, 24)

    def test_corte_exacto_14_00_es_dia_siguiente(self):
        assert fecha_efectiva_recepcion(datetime(2026, 11, 24, 14, 0)) == date(2026, 11, 25)

    def test_medianoche_mismo_dia(self):
        assert fecha_efectiva_recepcion(datetime(2026, 11, 24, 0, 0)) == date(2026, 11, 24)

    def test_despues_del_corte_fin_de_mes(self):
        assert fecha_efectiva_recepcion(datetime(2026, 11, 30, 23, 59)) == date(2026, 12, 1)

    def test_despues_del_corte_fin_de_anio(self):
        assert fecha_efectiva_recepcion(datetime(2026, 12, 31, 14, 0)) == date(2027, 1, 1)


class TestFechaEntrega:
    RUTA_LMV = (LUN, MIE, VIE)

    def test_1359_vs_1400_en_lunes(self):
        # Lunes 23-nov-2026. 13:59 → recepción lunes (día de visita, no cuenta) → miércoles.
        assert fecha_entrega(datetime(2026, 11, 23, 13, 59), self.RUTA_LMV) == date(2026, 11, 25)
        # 14:00 → recepción martes → miércoles (mismo resultado por otra vía).
        assert fecha_entrega(datetime(2026, 11, 23, 14, 0), self.RUTA_LMV) == date(2026, 11, 25)

    def test_corte_cambia_la_entrega(self):
        # Martes 24-nov, ruta mié/sáb. 13:59 → recepción martes → miércoles 25.
        assert fecha_entrega(datetime(2026, 11, 24, 13, 59), (MIE, SAB)) == date(2026, 11, 25)
        # 14:00 → recepción miércoles (día de visita, no cuenta) → sábado 28.
        assert fecha_entrega(datetime(2026, 11, 24, 14, 0), (MIE, SAB)) == date(2026, 11, 28)

    def test_recepcion_en_dia_de_visita_no_cuenta(self):
        # Martes 24-nov 06:50, ruta mar/vie → viernes 27 (caso tipo D022).
        assert fecha_entrega(datetime(2026, 11, 24, 6, 50), (MAR, VIE)) == date(2026, 11, 27)

    def test_recepcion_sabado(self):
        # Sábado 28-nov 10:00, ruta lun/jue → lunes 30.
        assert fecha_entrega(datetime(2026, 11, 28, 10, 0), (LUN, JUE)) == date(2026, 11, 30)

    def test_sabado_despues_del_corte_pasa_a_domingo(self):
        # Sábado 28-nov 14:02 → recepción domingo 29 → lunes 30 (caso tipo D041).
        assert fecha_entrega(datetime(2026, 11, 28, 14, 2), self.RUTA_LMV) == date(2026, 11, 30)

    def test_recepcion_domingo(self):
        # Domingo 29-nov 09:00, ruta mié/sáb → miércoles 2-dic.
        assert fecha_entrega(datetime(2026, 11, 29, 9, 0), (MIE, SAB)) == date(2026, 12, 2)

    def test_ruta_de_un_solo_dia_salta_una_semana(self):
        # Sábado 28-nov 09:00, ruta solo sábado → sábado siguiente 5-dic (7 días).
        assert fecha_entrega(datetime(2026, 11, 28, 9, 0), (SAB,)) == date(2026, 12, 5)

    def test_ruta_de_un_solo_dia_viernes_despues_del_corte(self):
        # Viernes 27-nov 14:00 → recepción sábado (día de visita) → sábado 5-dic.
        assert fecha_entrega(datetime(2026, 11, 27, 14, 0), (SAB,)) == date(2026, 12, 5)
        # Viernes 27-nov 13:59 → recepción viernes → sábado 28.
        assert fecha_entrega(datetime(2026, 11, 27, 13, 59), (SAB,)) == date(2026, 11, 28)

    def test_cruce_de_mes(self):
        # Jueves 26-nov 20:02 → recepción viernes 27 (visita, no cuenta) → martes 1-dic (D005).
        assert fecha_entrega(datetime(2026, 11, 26, 20, 2), (MAR, VIE)) == date(2026, 12, 1)

    def test_cruce_de_anio(self):
        # Jueves 31-dic-2026 14:30 → recepción viernes 1-ene → lunes 4-ene-2027.
        assert fecha_entrega(datetime(2026, 12, 31, 14, 30), (LUN, JUE)) == date(2027, 1, 4)

    def test_orden_de_dias_visita_no_importa(self):
        assert fecha_entrega(datetime(2026, 11, 24, 6, 50), (VIE, MAR)) == date(2026, 11, 27)

    def test_sin_dias_de_visita_rechazado(self):
        with pytest.raises(ValueError):
            fecha_entrega(datetime(2026, 11, 24, 6, 50), ())

    def test_dia_invalido_rechazado(self):
        with pytest.raises(ValueError):
            fecha_entrega(datetime(2026, 11, 24, 6, 50), (7,))


# =========================================================================== crédito


class TestCredito:
    def test_dentro_del_limite(self):
        assert credito_excedido(cliente("5000.00", "1000.00"), Decimal("3999.99")) is False

    def test_exactamente_en_el_limite_no_excede(self):
        assert credito_excedido(cliente("5000.00", "1000.00"), Decimal("4000.00")) is False

    def test_un_centavo_encima_excede(self):
        assert credito_excedido(cliente("5000.00", "1000.00"), Decimal("4000.01")) is True

    def test_total_cero_con_saldo_ya_por_encima(self):
        assert credito_excedido(cliente("1000.00", "1000.01"), Decimal("0.00")) is True

    def test_acepta_total_float(self):
        assert credito_excedido(cliente("5000.00", "1000.00"), 4000.0) is False
        assert credito_excedido(cliente("5000.00", "1000.00"), 4000.01) is True
