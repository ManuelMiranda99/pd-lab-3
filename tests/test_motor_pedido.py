"""Pruebas unitarias del armado del pedido (`novapedidos.motor.armar_pedido`).

Corren siempre (también en la CI): usan un catálogo sintético mínimo construido con
`construir_datos`. Cubren cada regla de `politicas.md` §2–§9 y los bordes que los casos dev no
tienen (conversión no exacta, menciones repetidas, corrección, quitar de "lo de siempre",
varios motivos con crédito excedido, cliente sin pedidos entregados).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest
from pydantic import ValidationError

from novapedidos.contrato import EstadoPedido, MotivoRevision, Pendiente, RespuestaPedido
from novapedidos.datos import construir_datos
from novapedidos.motor import (
    TEXTO_LO_DE_SIEMPRE,
    Accion,
    ErrorClienteInexistente,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
    armar_pedido,
    convertir_a_unidades_de_venta,
)
from novapedidos.motor.mensaje import fecha_en_espanol, plural_unidad, saludo

# --------------------------------------------------------------------------- mundo sintético


@pytest.fixture(scope="module")
def datos():
    """8 productos (2 agotados con sustituto, 1 sin sustituto), 3 clientes:

    - C1: lunes/jueves, holgado; último entregado P1 = GV×2, AP×1 (→AD), AD×3, GC×1 (agotado
      sin sustituto); P2 posterior pero cancelado (K6×9).
    - C2: martes/viernes, sin ningún pedido entregado.
    - C3: lunes/jueves, crédito casi agotado (saldo 900 de 1000).
    """
    productos = pd.DataFrame(
        {
            "sku": ["K6", "K15", "K3", "AD", "AP", "GC", "GV", "AQ"],
            "nombre": [
                "Kola 600 ml",
                "Kola 1.5 L",
                "Kola 3 L",
                "Aceite Dorado",
                "Aceite Palma",
                "Galleta Choco",
                "Galleta Vainilla",
                "Arroz quintal",
            ],
            "marca": ["K", "K", "K", "D", "P", "G", "G", "A"],
            "categoria": [
                "bebidas",
                "bebidas",
                "bebidas",
                "abarrotes",
                "abarrotes",
                "snacks",
                "snacks",
                "abarrotes",
            ],
            "presentacion": ["600 ml", "1.5 L", "3 L", "750 ml", "750 ml", "12", "12", "100 lb"],
            "unidad_venta": [
                "fardo",
                "fardo",
                "fardo",
                "caja",
                "caja",
                "display",
                "display",
                "quintal",
            ],
            "unidades_por_empaque": [24, 12, 6, 12, 12, 12, 12, 1],
            "precio": [120.0, 144.0, 132.0, 228.0, 192.0, 48.0, 42.0, 480.0],
            "disponible": [True, True, False, True, False, False, True, True],
            "sustituto_sku": [None, None, "K15", None, "AD", None, None, None],
        }
    )
    sinonimos = pd.DataFrame({"sku": ["K6"], "alias": ["kola 600"]})
    escalas = pd.DataFrame(
        {
            "categoria": ["bebidas", "bebidas", "snacks"],
            "cantidad_minima": [10, 20, 10],
            "descuento_pct": [3, 5, 3],
        }
    )
    clientes = pd.DataFrame(
        {
            "cliente_id": ["C1", "C2", "C3"],
            "nombre_tienda": ["T1", "T2", "T3"],
            "propietario": ["A", "B", "C"],
            "municipio": ["Mixco", "Mixco", "Mixco"],
            "dias_visita": ["lunes,jueves", "martes,viernes", "lunes,jueves"],
            "limite_credito": [10000, 5000, 1000],
            "saldo_pendiente": [1000, 0, 900],
        }
    )
    historial = pd.DataFrame(
        {
            "pedido_id": ["P1", "P1", "P1", "P1", "P2", "P3"],
            "cliente_id": ["C1", "C1", "C1", "C1", "C1", "C3"],
            "fecha_entrega": pd.to_datetime(["2026-11-02"] * 4 + ["2026-11-09", "2026-11-05"]),
            "sku": ["GV", "AP", "AD", "GC", "K6", "K6"],
            "cantidad": [2, 1, 3, 1, 9, 1],
            "estado": ["entregado"] * 4 + ["cancelado", "entregado"],
        }
    )
    return construir_datos(productos, sinonimos, escalas, clientes, historial)


#: Lunes 23 de noviembre de 2026, 10:00 (antes del corte). Ruta lunes/jueves → jueves 26.
LUNES = datetime(2026, 11, 23, 10, 0)


def s(sku, cantidad=None, unidad=Unidad.VENTA, accion=Accion.AGREGAR, texto=None):
    return Mencion(
        texto=texto or f"producto {sku}",
        resolucion=Resolucion.SKU,
        sku=sku,
        cantidad=cantidad,
        unidad=unidad,
        accion=accion,
    )


def amb(texto, opciones, cantidad=None, accion=Accion.AGREGAR):
    return Mencion(
        texto=texto,
        resolucion=Resolucion.AMBIGUO,
        opciones=opciones,
        cantidad=cantidad,
        accion=accion,
    )


def no(texto, cantidad=None, accion=Accion.AGREGAR):
    return Mencion(texto=texto, resolucion=Resolucion.NO_EXISTE, cantidad=cantidad, accion=accion)


def pedido(*menciones, siempre=False, posicion=0):
    return InterpretacionResuelta(
        es_pedido=True,
        lo_de_siempre=siempre,
        posicion_lo_de_siempre=posicion,
        menciones=list(menciones),
    )


def armar(datos, *menciones, cliente="C1", fecha_hora=LUNES, siempre=False, posicion=0):
    return armar_pedido(
        cliente, fecha_hora, pedido(*menciones, siempre=siempre, posicion=posicion), datos
    )


def lineas(r: RespuestaPedido) -> list[tuple[str, int]]:
    return [(ln.sku, ln.cantidad) for ln in r.lineas]


def subs(r: RespuestaPedido) -> list[tuple[str, str, int]]:
    return [(x.sku_solicitado, x.sku_entregado, x.cantidad) for x in r.sustituciones]


def pends(r: RespuestaPedido) -> list[tuple[str, str, list[str]]]:
    return [(p.motivo.value, p.texto, p.opciones) for p in r.pendientes]


# --------------------------------------------------------------------------- básico


def test_pedido_simple_confirmado(datos):
    r = armar(datos, s("K6", 2))
    assert r.estado is EstadoPedido.CONFIRMADO
    assert lineas(r) == [("K6", 2)]
    assert r.lineas[0].precio_unitario == Decimal("120.00")
    assert r.lineas[0].descuento_pct == 0
    assert r.total == Decimal("240.00")
    assert r.fecha_entrega == date(2026, 11, 26)
    assert r.sustituciones == [] and r.pendientes == [] and r.motivos_revision == []


def test_sin_cantidad_vale_una_unidad_de_venta(datos):
    assert lineas(armar(datos, s("K6"), s("GV", None, Unidad.DOCENA))) == [("K6", 1), ("GV", 1)]


def test_fecha_con_hora_de_corte(datos):
    # Miércoles 25 a las 14:00 → recibido el jueves 26 → siguiente visita lunes 30.
    r = armar(datos, s("K6", 1), fecha_hora=datetime(2026, 11, 25, 14, 0))
    assert r.fecha_entrega == date(2026, 11, 30)


def test_cliente_inexistente(datos):
    with pytest.raises(ErrorClienteInexistente):
        armar(datos, s("K6", 1), cliente="C-NO")


# --------------------------------------------------------------------------- conversión


@pytest.mark.parametrize(
    ("cantidad", "unidad", "sku", "esperado"),
    [
        (3, Unidad.VENTA, "K6", 3),
        (48, Unidad.UNIDAD_SUELTA, "K6", 2),
        (24, Unidad.UNIDAD_SUELTA, "K6", 1),
        (36, Unidad.UNIDAD_SUELTA, "K6", None),  # 1.5 fardos: no se redondea
        (12, Unidad.UNIDAD_SUELTA, "K6", None),
        (2, Unidad.DOCENA, "K6", 1),
        (1, Unidad.DOCENA, "K6", None),
        (1, Unidad.DOCENA, "GV", 1),
        (3, Unidad.UNIDAD_SUELTA, "AQ", 3),  # quintal: 1 unidad por empaque
    ],
)
def test_convertir_a_unidades_de_venta(datos, cantidad, unidad, sku, esperado):
    assert convertir_a_unidades_de_venta(cantidad, unidad, datos.producto(sku)) == esperado


def test_unidades_sueltas_exactas_se_convierten(datos):
    r = armar(datos, s("K6", 48, Unidad.UNIDAD_SUELTA))
    assert lineas(r) == [("K6", 2)]
    assert r.estado is EstadoPedido.CONFIRMADO


def test_conversion_no_exacta_es_pendiente(datos):
    r = armar(datos, s("GV", 1), s("K6", 36, Unidad.UNIDAD_SUELTA, texto="botellas de kola"))
    assert lineas(r) == [("GV", 1)]
    assert pends(r) == [("cantidad_no_convertible", "botellas de kola", ["K6"])]
    assert r.motivos_revision == [MotivoRevision.CANTIDAD_NO_CONVERTIBLE]
    assert r.estado is EstadoPedido.REQUIERE_REVISION


def test_solo_conversion_no_exacta_queda_sin_lineas(datos):
    r = armar(datos, s("K6", 36, Unidad.UNIDAD_SUELTA))
    assert r.lineas == [] and r.total == Decimal("0.00")
    assert r.estado is EstadoPedido.REQUIERE_REVISION
    assert r.fecha_entrega == date(2026, 11, 26)


def test_agotado_sin_sustituto_con_cantidad_no_convertible_es_agotado(datos):
    r = armar(datos, s("GC", 5, Unidad.UNIDAD_SUELTA))
    assert pends(r) == [("agotado_sin_sustituto", "Galleta Choco", ["GC"])]


# --------------------------------------------------------------------------- sumas y correcciones


def test_menciones_repetidas_se_suman_en_la_primera_posicion(datos):
    r = armar(datos, s("K6", 6), s("GV", 1), s("K6", 4))
    assert lineas(r) == [("K6", 10), ("GV", 1)]
    assert r.lineas[0].descuento_pct == 3  # descuento con la cantidad final (D05)


def test_suma_mezcla_unidades(datos):
    assert lineas(armar(datos, s("K6", 1), s("K6", 24, Unidad.UNIDAD_SUELTA))) == [("K6", 2)]


def test_correccion_reemplaza_y_conserva_posicion(datos):
    r = armar(datos, s("K6", 3), s("GV", 1), s("K6", 5, accion=Accion.CORREGIR))
    assert lineas(r) == [("K6", 5), ("GV", 1)]


def test_correccion_sin_mencion_previa_agrega(datos):
    assert lineas(armar(datos, s("GV", 1), s("K6", 5, accion=Accion.CORREGIR))) == [
        ("GV", 1),
        ("K6", 5),
    ]


def test_correccion_quita_pendiente_de_conversion(datos):
    r = armar(
        datos,
        s("K6", 36, Unidad.UNIDAD_SUELTA),
        s("GV", 1),
        s("K6", 2, accion=Accion.CORREGIR),
    )
    assert lineas(r) == [("K6", 2), ("GV", 1)]
    assert r.pendientes == [] and r.estado is EstadoPedido.CONFIRMADO


def test_correccion_a_cantidad_no_convertible_reemplaza_la_linea(datos):
    r = armar(datos, s("K6", 2), s("K6", 30, Unidad.UNIDAD_SUELTA, accion=Accion.CORREGIR))
    assert r.lineas == []
    assert [p.motivo for p in r.pendientes] == [MotivoRevision.CANTIDAD_NO_CONVERTIBLE]


def test_cantidades_no_positivas(datos):
    # agregar 0 se ignora; corregir a 0 equivale a quitar (D16).
    assert lineas(armar(datos, s("K6", 0), s("GV", 1))) == [("GV", 1)]
    assert lineas(armar(datos, s("K6", 2), s("GV", 1), s("K6", 0, accion=Accion.CORREGIR))) == [
        ("GV", 1)
    ]
    assert lineas(armar(datos, s("K6", -3), s("GV", 1))) == [("GV", 1)]


def test_quitar_elimina_la_linea(datos):
    assert lineas(armar(datos, s("K6", 2), s("GV", 1), s("K6", accion=Accion.QUITAR))) == [
        ("GV", 1)
    ]


def test_todo_quitado_es_fuera_de_alcance(datos):
    r = armar(datos, s("K6", 2), s("K6", accion=Accion.QUITAR))
    assert r.estado is EstadoPedido.FUERA_DE_ALCANCE
    assert r.lineas == [] and r.total == Decimal("0.00") and r.fecha_entrega is None


def test_pedido_sin_menciones_es_fuera_de_alcance(datos):
    assert armar(datos).estado is EstadoPedido.FUERA_DE_ALCANCE


# --------------------------------------------------------------------------- lo de siempre


def test_lo_de_siempre_ultimo_entregado_con_sustitucion_fusionada(datos):
    r = armar(datos, siempre=True)
    # P2 (cancelado, posterior) se ignora; AP→AD se fusiona con AD×3 en la posición de AP (D07).
    assert lineas(r) == [("GV", 2), ("AD", 4)]
    assert subs(r) == [("AP", "AD", 1)]
    assert pends(r) == [("agotado_sin_sustituto", "Galleta Choco", ["GC"])]
    assert r.motivos_revision == [MotivoRevision.AGOTADO_SIN_SUSTITUTO]


def test_lo_de_siempre_sin_el_agotado_quita_su_pendiente(datos):
    r = armar(datos, s("GC", accion=Accion.QUITAR), siempre=True)
    assert lineas(r) == [("GV", 2), ("AD", 4)]
    assert r.pendientes == [] and r.estado is EstadoPedido.CONFIRMADO


def test_quitar_de_lo_de_siempre_incluye_lo_sustituido(datos):
    # "sin aceite dorado": se quita AD y también AP, que se entregaría como AD.
    r = armar(datos, s("AD", accion=Accion.QUITAR), s("GC", accion=Accion.QUITAR), siempre=True)
    assert lineas(r) == [("GV", 2)]
    assert r.sustituciones == []


def test_quitar_el_agotado_con_sustituto_conserva_el_resto(datos):
    r = armar(datos, s("AP", accion=Accion.QUITAR), s("GC", accion=Accion.QUITAR), siempre=True)
    assert lineas(r) == [("GV", 2), ("AD", 3)]
    assert r.sustituciones == []


def test_lo_de_siempre_mas_agregados_y_correcciones(datos):
    r = armar(datos, s("GV", 3), s("K6", 1), s("GC", accion=Accion.QUITAR), siempre=True)
    assert lineas(r) == [("GV", 5), ("AD", 4), ("K6", 1)]
    r = armar(
        datos, s("GV", 1, accion=Accion.CORREGIR), s("GC", accion=Accion.QUITAR), siempre=True
    )
    assert lineas(r) == [("GV", 1), ("AD", 4)]


def test_lo_de_siempre_en_su_posicion(datos):
    r = armar(datos, s("K6", 1), s("GC", accion=Accion.QUITAR), siempre=True, posicion=1)
    assert lineas(r) == [("K6", 1), ("GV", 2), ("AD", 4)]


def test_lo_de_siempre_posicion_mayor_que_menciones_va_al_final(datos):
    r = armar(datos, s("K6", 1), siempre=True, posicion=99)
    assert lineas(r) == [("K6", 1), ("GV", 2), ("AD", 4)]


def test_lo_de_siempre_sin_pedidos_entregados(datos):
    r = armar(datos, cliente="C2", siempre=True)
    assert r.estado is EstadoPedido.REQUIERE_REVISION
    assert r.lineas == []
    assert pends(r) == [("producto_ambiguo", TEXTO_LO_DE_SIEMPRE, [])]
    assert "lo de siempre" in r.mensaje_cliente and "asesor" in r.mensaje_cliente
    r = armar(datos, s("K6", 2), cliente="C2", siempre=True)
    assert lineas(r) == [("K6", 2)] and len(r.pendientes) == 1


# --------------------------------------------------------------------------- agotados


def test_agotado_con_sustituto(datos):
    r = armar(datos, s("K3", 2))
    assert lineas(r) == [("K15", 2)]
    assert subs(r) == [("K3", "K15", 2)]
    assert r.estado is EstadoPedido.CONFIRMADO


def test_sustituto_ya_presente_se_fusiona(datos):
    r = armar(datos, s("K15", 1), s("K3", 2))
    assert lineas(r) == [("K15", 3)]
    assert subs(r) == [("K3", "K15", 2)]


def test_agotado_sin_sustituto(datos):
    r = armar(datos, s("GC", 5, texto="galletas de chocolate"), s("GV", 1))
    assert lineas(r) == [("GV", 1)]
    assert pends(r) == [("agotado_sin_sustituto", "Galleta Choco", ["GC"])]


# --------------------------------------------------------------------------- ambiguos y no existe


def test_ambiguo_opciones_filtradas_y_ordenadas(datos):
    r = armar(datos, amb("aceite", ["AP", "AD", "AD", "ZZ"], 2))
    assert pends(r) == [("producto_ambiguo", "aceite", ["AD", "AP"])]
    assert r.lineas == [] and r.estado is EstadoPedido.REQUIERE_REVISION


def test_ambiguo_con_una_sola_opcion_valida_se_resuelve(datos):
    assert lineas(armar(datos, amb("kola", ["K6", "XX"], 2))) == [("K6", 2)]


def test_ambiguo_sin_opciones_validas_es_no_existe(datos):
    assert pends(armar(datos, amb("vino", ["XX"]))) == [("producto_no_existe", "vino", [])]


def test_ambiguo_repetido_un_solo_pendiente(datos):
    r = armar(datos, amb("aceite", ["AD", "AP"], 2), amb("aceite", ["AP", "AD"], 3))
    assert len(r.pendientes) == 1


def test_quitar_ambiguo_quita_opciones_del_habitual(datos):
    r = armar(datos, amb("aceite", ["AD", "AP"], accion=Accion.QUITAR), siempre=True)
    assert lineas(r) == [("GV", 2)]
    assert r.sustituciones == []
    assert [p.motivo for p in r.pendientes] == [MotivoRevision.AGOTADO_SIN_SUSTITUTO]


def test_sku_fuera_del_catalogo_es_no_existe(datos):
    r = armar(datos, s("ZZ9", 3, texto="cerveza"), s("K6", 1))
    assert pends(r) == [("producto_no_existe", "cerveza", [])]
    assert lineas(r) == [("K6", 1)]
    m = Mencion(texto="algo", resolucion=Resolucion.SKU, sku=None, cantidad=1)
    assert pends(armar(datos, m)) == [("producto_no_existe", "algo", [])]


def test_no_existe_repetido_y_quitado(datos):
    r = armar(datos, no("cerveza", 5), no("Cerveza", 2), s("K6", 1))
    assert pends(r) == [("producto_no_existe", "cerveza", [])]
    r = armar(datos, no("cerveza", 5), s("K6", 1), no("cerveza", accion=Accion.QUITAR))
    assert r.pendientes == [] and r.estado is EstadoPedido.CONFIRMADO


# ---------------------------------------------------------------- estado, motivos, crédito


def test_credito_excedido(datos):
    r = armar(datos, s("K6", 1), cliente="C3")  # 900 + 120 > 1000
    assert r.estado is EstadoPedido.REQUIERE_REVISION
    assert r.motivos_revision == [MotivoRevision.CREDITO_EXCEDIDO]
    assert r.pendientes == []
    assert lineas(r) == [("K6", 1)]  # las líneas se calculan igual


def test_varios_motivos_en_orden_y_credito_al_final(datos):
    r = armar(
        datos,
        no("cerveza", 2),
        s("K6", 10),
        amb("aceite", ["AD", "AP"]),
        s("GC", 1),
        no("velas", 1),
        amb("aceite", ["AD", "AP"]),
        cliente="C3",
    )
    assert [p.motivo.value for p in r.pendientes] == [
        "producto_no_existe",
        "producto_ambiguo",
        "agotado_sin_sustituto",
        "producto_no_existe",
    ]
    assert [m.value for m in r.motivos_revision] == [
        "producto_no_existe",
        "producto_ambiguo",
        "agotado_sin_sustituto",
        "credito_excedido",
    ]
    assert r.total == Decimal("1164.00")  # 10 × 120 × 0.97


def test_fuera_de_alcance_ignora_menciones(datos):
    interp = InterpretacionResuelta(es_pedido=False, menciones=[s("K6", 5)])
    r = armar_pedido("C3", LUNES, interp, datos)
    assert r.estado is EstadoPedido.FUERA_DE_ALCANCE
    assert r.lineas == [] and r.pendientes == [] and r.sustituciones == []
    assert r.motivos_revision == []  # nunca credito_excedido en fuera de alcance
    assert r.total == Decimal("0.00") and r.fecha_entrega is None
    assert "asesor" in r.mensaje_cliente


# --------------------------------------------------------------------------- contrato / JSON


def test_serializacion_json_del_contrato(datos):
    r = armar(datos, s("K6", 10), s("GC", 1))
    obj = json.loads(r.model_dump_json())
    assert list(obj) == [
        "cliente_id",
        "estado",
        "lineas",
        "total",
        "fecha_entrega",
        "sustituciones",
        "pendientes",
        "motivos_revision",
        "mensaje_cliente",
    ]
    assert obj["lineas"][0] == {
        "sku": "K6",
        "cantidad": 10,
        "precio_unitario": 120.0,
        "descuento_pct": 3,
        "subtotal": 1164.0,
    }
    assert isinstance(obj["total"], float) and obj["total"] == 1164.0
    assert obj["fecha_entrega"] == "2026-11-26"
    assert obj["estado"] == "requiere_revision"
    assert obj["pendientes"][0]["motivo"] == "agotado_sin_sustituto"
    fuera = json.loads(
        armar_pedido("C1", LUNES, InterpretacionResuelta(es_pedido=False), datos).model_dump_json()
    )
    assert fuera["fecha_entrega"] is None and fuera["total"] == 0.0


def test_pendiente_no_admite_credito_excedido():
    with pytest.raises(ValidationError):
        Pendiente(motivo=MotivoRevision.CREDITO_EXCEDIDO, texto="x")


def test_esquema_intermedio_sin_precios_ni_descuentos():
    esquema = json.dumps(InterpretacionResuelta.model_json_schema())
    assert "precio" not in esquema and "descuento" not in esquema and "credito" not in esquema


# --------------------------------------------------------------------------- mensaje al cliente


@pytest.mark.parametrize(
    ("hora", "esperado"),
    [
        (5, "¡Buenos días!"),
        (11, "¡Buenos días!"),
        (12, "¡Buenas tardes!"),
        (18, "¡Buenas tardes!"),
        (19, "¡Buenas noches!"),
        (2, "¡Buenas noches!"),
    ],
)
def test_saludo_segun_hora(hora, esperado):
    assert saludo(datetime(2026, 11, 23, hora, 30)) == esperado


def test_fecha_y_unidades_en_espanol():
    assert fecha_en_espanol(date(2026, 11, 26)) == "jueves 26 de noviembre"
    assert fecha_en_espanol(date(2026, 12, 5)) == "sábado 5 de diciembre"
    assert plural_unidad("fardo", 1) == "fardo"
    assert plural_unidad("caja", 2) == "cajas"
    assert plural_unidad("display", 3) == "displays"
    assert plural_unidad("quintal", 2) == "quintales"


def test_mensaje_confirmado(datos):
    r = armar(datos, s("K6", 2), s("AQ", 2), s("K3", 1), fecha_hora=datetime(2026, 11, 23, 15, 20))
    m = r.mensaje_cliente
    assert m.startswith("¡Buenas tardes!")
    assert "2 fardos de Kola 600 ml" in m and "2 quintales de Arroz quintal" in m
    assert "Kola 3 L está agotado" in m and "1 fardo de Kola 1.5 L" in m
    assert "jueves 26 de noviembre" in m
    assert "asesor" not in m
    # El único monto citado es el total calculado.
    assert re.findall(r"Q[\d,]+\.\d{2}", m) == ["Q1,344.00"]
    assert r.total == Decimal("1344.00")


def test_mensaje_requiere_revision_no_promete_pendientes(datos):
    r = armar(
        datos,
        s("K6", 1),
        amb("aceite", ["AD", "AP"]),
        s("GC", 2),
        no("cerveza"),
        s("K6", 30, Unidad.UNIDAD_SUELTA, texto="kola"),
    )
    m = r.mensaje_cliente
    assert "un asesor le confirmará" in m.lower()
    assert "«aceite»" in m and "«cerveza»" in m
    assert "Galleta Choco está agotado" in m and "no va incluido" in m
    assert "fardos enteros" in m
    assert "Entrega prevista" in m
    assert re.findall(r"Q[\d,]+\.\d{2}", m) == ["Q120.00"]


def test_mensaje_credito_y_sin_lineas(datos):
    m = armar(datos, s("K6", 1), cliente="C3").mensaje_cliente
    assert "crédito" in m and "asesor" in m
    m = armar(datos, amb("aceite", ["AD", "AP"])).mensaje_cliente
    assert "Recibimos su mensaje" in m and "Total" not in m and "Entrega" not in m


def test_mensaje_cita_textos_largos_recortados(datos):
    largo = "producto " + "x" * 200
    m = armar(datos, no(largo)).mensaje_cliente
    assert largo not in m and "…" in m
