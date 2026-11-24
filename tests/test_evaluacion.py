"""Pruebas de la evaluación local (`novapedidos.evaluacion`), sin LLM ni datos publicados.

Cubren cada calificador (incluidos los bordes: orden de líneas, tolerancia del total, texto
laxo de pendientes, manipulación de precios), el calificador del mensaje al cliente, la
construcción de casos con el motor y una corrida completa con un ejecutor falso.
"""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from novapedidos.datos import construir_datos
from novapedidos.evaluacion import calificadores as cal
from novapedidos.evaluacion.casos import (
    SUELTA,
    ambiguo,
    cargar_casos,
    construir_caso,
    escribir_casos,
    no_existe,
    pedido,
    sku,
)
from novapedidos.evaluacion.corrida import correr_evaluacion, guardar_resultados
from novapedidos.evaluacion.ejecutores import Ejecucion
from novapedidos.evaluacion.mensaje_cliente import calificar_mensaje_cliente, reglas_mensaje
from novapedidos.motor import armar_pedido


@pytest.fixture(scope="module")
def datos():
    """B01 (fardo 24, Q120), B11 (fardo 12, Q150), A06 (caja, Q228), A07 (agotado → A06),
    S02 (agotado sin sustituto). Escala de bebidas: 10 → 3 %."""
    productos = pd.DataFrame(
        {
            "sku": ["B01", "B11", "A06", "A07", "S02"],
            "nombre": [
                "Kola Norte 600 ml",
                "Energía Max 473 ml",
                "Aceite Girasol Dorado 750 ml",
                "Aceite Palma Real 750 ml",
                "Galletas Dulcitas Chocolate",
            ],
            "marca": ["Kola Norte", "Energía Max", "Dorado", "Palma Real", "Dulcitas"],
            "categoria": ["bebidas", "bebidas", "abarrotes", "abarrotes", "snacks"],
            "presentacion": ["600 ml", "473 ml", "750 ml", "750 ml", "12 paquetes"],
            "unidad_venta": ["fardo", "fardo", "caja", "caja", "display"],
            "unidades_por_empaque": [24, 12, 12, 12, 12],
            "precio": [120.0, 150.0, 228.0, 192.0, 48.0],
            "disponible": [True, True, True, False, False],
            "sustituto_sku": [None, None, None, "A06", None],
        }
    )
    sinonimos = pd.DataFrame({"sku": ["B01"], "alias": ["kola 600"]})
    escalas = pd.DataFrame(
        {"categoria": ["bebidas"], "cantidad_minima": [10], "descuento_pct": [3]}
    )
    clientes = pd.DataFrame(
        {
            "cliente_id": ["C001", "C002"],
            "nombre_tienda": ["Tienda Uno", "Tienda Dos"],
            "propietario": ["Ana", "Beto"],
            "municipio": ["Mixco", "Mixco"],
            "dias_visita": ["lunes,jueves", "martes,viernes"],
            "limite_credito": [8000, 1000],
            "saldo_pendiente": [1200, 900],
        }
    )
    historial = pd.DataFrame(
        {
            "pedido_id": ["P1"],
            "cliente_id": ["C001"],
            "fecha_entrega": pd.to_datetime(["2026-11-02"]),
            "sku": ["B01"],
            "cantidad": [1],
            "estado": ["entregado"],
        }
    )
    return construir_datos(productos, sinonimos, escalas, clientes, historial)


FECHA = "2026-11-23T10:00"  # lunes antes de las 14:00 → entrega jueves 26


@pytest.fixture
def caso(datos):
    """Caso con una línea con descuento, una sustitución y un pendiente ambiguo."""
    return construir_caso(
        "T001",
        "prueba",
        "C001",
        FECHA,
        ["10 kola 600, 2 cajas de aceite palma real y unas cocas"],
        pedido(
            sku("kola 600", "B01", 10),
            sku("aceite palma real", "A07", 2),
            ambiguo("cocas", ["B01", "B11"]),
        ),
        datos,
    )


def _copia(respuesta: dict) -> dict:
    return json.loads(json.dumps(respuesta))


# --------------------------------------------------------------------------- construir_caso


def test_construir_caso_calcula_esperado_con_el_motor(caso):
    esp = caso["esperado"]
    assert set(esp) == {
        "estado",
        "lineas",
        "total",
        "fecha_entrega",
        "sustituciones",
        "pendientes",
        "motivos_revision",
    }
    assert esp["estado"] == "requiere_revision"
    assert esp["lineas"][0] == {
        "sku": "B01",
        "cantidad": 10,
        "precio_unitario": 120.0,
        "descuento_pct": 3,
        "subtotal": 1164.0,
    }
    assert esp["sustituciones"] == [
        {"sku_solicitado": "A07", "sku_entregado": "A06", "cantidad": 2}
    ]
    assert esp["total"] == 1164.0 + 456.0
    assert esp["fecha_entrega"] == "2026-11-26"


def test_escribir_y_cargar_casos(tmp_path, caso):
    ruta = tmp_path / "casos.jsonl"
    escribir_casos([caso], ruta)
    (cargado,) = cargar_casos([ruta])
    assert cargado["id"] == "T001" and cargado["_origen"] == "casos.jsonl"
    with pytest.raises(ValueError, match="repetidos"):
        cargar_casos([ruta, ruta])


# --------------------------------------------------------------------------- calificadores


def test_respuesta_identica_aprueba_todo(caso, datos):
    obtenido = _copia(caso["esperado"])
    califs = cal.calificar_respuesta(obtenido, caso["esperado"], datos)
    assert all(c.aprobado for c in califs.values()), califs
    assert set(califs) >= {"caso_exacto", "lineas_estricto", "integridad_precios"}


def test_estado_y_fecha(caso):
    obtenido = {**caso["esperado"], "estado": "confirmado", "fecha_entrega": "2026-11-27"}
    assert not cal.calificar_estado(obtenido, caso["esperado"]).aprobado
    assert not cal.calificar_fecha_entrega(obtenido, caso["esperado"]).aprobado
    exacto = cal.calificar_caso_exacto(obtenido, caso["esperado"])
    assert not exacto.aprobado and "estado" in exacto.detalle and "fecha_entrega" in exacto.detalle


def test_lineas_ignoran_orden_pero_el_estricto_no(caso):
    obtenido = _copia(caso["esperado"])
    obtenido["lineas"].reverse()
    assert cal.calificar_lineas(obtenido, caso["esperado"]).aprobado
    assert not cal.calificar_lineas_estricto(obtenido, caso["esperado"]).aprobado


def test_lineas_multiconjunto_detecta_cantidad_y_faltantes(caso):
    obtenido = _copia(caso["esperado"])
    obtenido["lineas"][0]["cantidad"] = 9
    r = cal.calificar_lineas(obtenido, caso["esperado"])
    assert not r.aprobado and "faltan" in r.detalle and "sobran" in r.detalle


def test_lineas_estricto_detecta_precio(caso):
    obtenido = _copia(caso["esperado"])
    obtenido["lineas"][0]["precio_unitario"] = 100.0
    r = cal.calificar_lineas_estricto(obtenido, caso["esperado"])
    assert not r.aprobado and "precio_unitario" in r.detalle


@pytest.mark.parametrize(("delta", "ok"), [(0.004, True), (0.005, True), (0.006, False)])
def test_total_con_tolerancia(caso, delta, ok):
    obtenido = {**caso["esperado"], "total": caso["esperado"]["total"] + delta}
    assert cal.calificar_total(obtenido, caso["esperado"]).aprobado is ok


def test_total_no_numerico_falla(caso):
    assert not cal.calificar_total({"total": "abc"}, caso["esperado"]).aprobado


def test_motivos_como_conjunto():
    e = {"motivos_revision": ["producto_ambiguo", "credito_excedido"]}
    assert cal.calificar_motivos_revision(
        {"motivos_revision": ["credito_excedido", "producto_ambiguo"]}, e
    ).aprobado
    assert not cal.calificar_motivos_revision(
        {"motivos_revision": ["producto_ambiguo"]}, e
    ).aprobado


def _pend(motivo: str, texto: str, opciones: list[str] | None = None) -> dict:
    return {"motivo": motivo, "texto": texto, "opciones": opciones or []}


@pytest.mark.parametrize(
    ("obtenido", "esperado"),
    [
        ("papel higienico", "papel higiénico"),  # tildes
        ("recargas de q50 x 3", "recargas de Q50"),  # contenido
        ("Aceite", "aceite"),  # mayúsculas
        ("cilindros de gas propano", "gas propano"),
        ("naranjitass", "naranjitas"),  # similitud
    ],
)
def test_texto_laxo_acepta(obtenido, esperado):
    assert cal.texto_equivalente(obtenido, esperado)


def test_texto_laxo_rechaza_otro_producto():
    assert not cal.texto_equivalente("cerveza", "velas")
    assert not cal.texto_equivalente("", "velas")


def test_pendientes_motivo_y_opciones_exactos_texto_laxo():
    esp = {"pendientes": [_pend("producto_ambiguo", "aceite", ["A06", "A07"])]}
    # opciones en otro orden y texto con tilde distinta: aprueba
    ok = {"pendientes": [_pend("producto_ambiguo", "Aceíte", ["A07", "A06"])]}
    assert cal.calificar_pendientes(ok, esp).aprobado
    # opciones incompletas: falla
    malo = {"pendientes": [_pend("producto_ambiguo", "aceite", ["A06"])]}
    assert not cal.calificar_pendientes(malo, esp).aprobado
    # otro motivo: falla
    otro = {"pendientes": [_pend("producto_no_existe", "aceite")]}
    assert not cal.calificar_pendientes(otro, esp).aprobado
    # texto de otro producto: falla con detalle de texto
    texto = {"pendientes": [_pend("producto_ambiguo", "cocas", ["A06", "A07"])]}
    r = cal.calificar_pendientes(texto, esp)
    assert not r.aprobado and "texto" in r.detalle


def test_pendientes_repetidos_se_emparejan_por_texto():
    esp = {"pendientes": [_pend("producto_no_existe", "velas"), _pend("producto_no_existe", "gas")]}
    obt = {"pendientes": [_pend("producto_no_existe", "gas"), _pend("producto_no_existe", "velas")]}
    assert cal.calificar_pendientes(obt, esp).aprobado


def test_sustituciones(caso):
    obtenido = _copia(caso["esperado"])
    obtenido["sustituciones"] = []
    assert not cal.calificar_sustituciones(obtenido, caso["esperado"]).aprobado
    assert cal.calificar_sustituciones(_copia(caso["esperado"]), caso["esperado"]).aprobado


# --------------------------------------------------------------------------- integridad


def test_integridad_aprueba_respuesta_del_motor(datos):
    respuesta = armar_pedido(
        "C001",
        datetime(2026, 11, 23, 10),
        pedido(sku("kola", "B01", 20), sku("energia", "B11", 48, SUELTA), no_existe("velas")),
        datos,
    ).model_dump(mode="json")
    assert cal.calificar_integridad_precios(respuesta, datos).aprobado


@pytest.mark.parametrize(
    ("campo", "valor", "texto"),
    [
        ("precio_unitario", 1.0, "precio"),
        ("descuento_pct", 50, "descuento"),
        ("subtotal", 10.0, "subtotal"),
        ("sku", "Z99", "catálogo"),
        ("sku", "A07", "agotado"),
    ],
)
def test_integridad_detecta_manipulacion(caso, datos, campo, valor, texto):
    obtenido = _copia(caso["esperado"])
    obtenido["lineas"][0][campo] = valor
    r = cal.calificar_integridad_precios(obtenido, datos)
    assert not r.aprobado and texto in r.detalle


def test_integridad_detecta_total_distinto(caso, datos):
    obtenido = {**_copia(caso["esperado"]), "total": 1.0}
    r = cal.calificar_integridad_precios(obtenido, datos)
    assert not r.aprobado and "total" in r.detalle


def test_integridad_fuera_de_alcance(datos):
    assert cal.calificar_integridad_precios({"lineas": [], "total": 0.0}, datos).aprobado


# --------------------------------------------------------------------------- latencia


def test_latencia_y_percentiles():
    assert cal.calificar_latencia(1.2).aprobado
    assert not cal.calificar_latencia(7.0).aprobado
    assert not cal.calificar_latencia(None).aprobado
    r = cal.resumen_latencias([1.0, 2.0, 3.0, 4.0, None])
    assert r["n"] == 4 and r["p50"] == 2.5 and r["max"] == 4.0
    assert r["p95"] == pytest.approx(3.85)
    assert cal.resumen_latencias([])["p50"] == 0.0


# --------------------------------------------------------------------------- mensaje


def test_mensaje_del_motor_cumple_las_reglas(caso, datos):
    respuesta = armar_pedido(
        "C001",
        datetime(2026, 11, 23, 10),
        pedido(sku("kola", "B01", 10), sku("aceite palma", "A07", 2), sku("dulcitas", "S02", 1)),
        datos,
    ).model_dump(mode="json")
    assert respuesta["estado"] == "requiere_revision"
    assert reglas_mensaje(respuesta, datos) == {}
    assert calificar_mensaje_cliente(respuesta, datos, "2026-11-26").aprobado


def test_mensaje_fuera_de_alcance_cumple(datos):
    respuesta = armar_pedido(
        "C001", datetime(2026, 11, 23, 10), pedido(es_pedido=False), datos
    ).model_dump(mode="json")
    assert reglas_mensaje(respuesta, datos) == {}


def _resp(mensaje: str, **campos) -> dict:
    base = {
        "estado": "confirmado",
        "lineas": [{"sku": "B01", "cantidad": 1}],
        "total": 120.0,
        "fecha_entrega": "2026-11-26",
        "mensaje_cliente": mensaje,
    }
    return {**base, **campos}


@pytest.mark.parametrize(
    ("respuesta", "regla"),
    [
        (_resp(""), "no_vacio"),
        (_resp("Ok."), "longitud"),
        (_resp("Recibimos su pedido. Total: Q120.00. Entrega el jueves 27 de noviembre."), "fecha"),
        (_resp("Recibimos su pedido. Total: Q99.00. Entrega el jueves 26 de noviembre."), "montos"),
        (
            _resp(
                "Recibimos su pedido de Aceite Palma Real 750 ml. "
                "Entrega el jueves 26 de noviembre."
            ),
            "agotados",
        ),
        (
            _resp(
                "Recibimos su pedido. Entrega el jueves 26 de noviembre. ¡Gracias!",
                estado="requiere_revision",
            ),
            "asesor",
        ),
        (
            _resp(
                "Gracias por escribir, la entrega será pronto.",
                estado="fuera_de_alcance",
                lineas=[],
                fecha_entrega=None,
                total=0.0,
            ),
            "fecha",
        ),
        (_resp("Thank you for your order, it will arrive soon " * 2), "espanol"),
    ],
)
def test_mensaje_reglas_incumplidas(datos, respuesta, regla):
    fallos = reglas_mensaje(respuesta, datos)
    assert regla in fallos, fallos
    assert not calificar_mensaje_cliente(respuesta, datos).aprobado


def test_mensaje_monto_con_separador_de_miles(datos):
    r = _resp("Recibimos su pedido. Total: Q1,620.00. Entrega el jueves 26 de noviembre.")
    assert "montos" not in reglas_mensaje({**r, "total": 1620.0}, datos)


# --------------------------------------------------------------------------- corrida


def test_corrida_con_ejecutor_falso(tmp_path, caso, datos):
    caso_fuera = construir_caso(
        "T002", "fuera_de_alcance", "C001", FECHA, ["gracias"], pedido(es_pedido=False), datos
    )
    obtenido_bueno = armar_pedido(
        "C001",
        datetime(2026, 11, 23, 10),
        pedido(
            sku("kola 600", "B01", 10),
            sku("aceite palma real", "A07", 2),
            ambiguo("cocas", ["B01", "B11"]),
        ),
        datos,
    ).model_dump(mode="json")

    def ejecutor(c: dict) -> Ejecucion:
        if c["id"] == "T001":
            return Ejecucion(
                obtenido=obtenido_bueno,
                latencia_s=1.0,
                meta_llm={"costo_usd": 0.001, "tokens_entrada": 10, "tokens_salida": 2},
            )
        return Ejecucion(latencia_s=0.5, error="ErrorInterprete: caído")

    vistos = []

    class Observador:
        def al_caso(self, resultado):
            vistos.append(resultado["id"])

    resultados, resumen = correr_evaluacion(
        [caso, caso_fuera], ejecutor, datos, concurrencia=2, observadores=[Observador()]
    )
    assert sorted(vistos) == ["T001", "T002"]
    assert [r["exacto"] for r in resultados] == [True, False]
    assert resumen["exactos"] == 1 and resumen["exactitud"] == 0.5
    assert resumen["errores"] == 1
    assert resumen["por_tipo"]["prueba"]["exactitud"] == 1.0
    assert resumen["por_calificador"]["integridad_precios"]["aprobados"] == 1
    assert resumen["costo_usd"]["total"] == 0.001
    assert resumen["fallos"] == ["T002"]

    reporte = guardar_resultados(tmp_path / "marca", resultados, {**resumen, "marca": "m"})
    assert (tmp_path / "marca" / "resultados.jsonl").read_text().count("\n") == 2
    assert json.loads((tmp_path / "marca" / "resumen.json").read_text())["exactos"] == 1
    assert "T002" in reporte and "ErrorInterprete" in reporte and "Exactitud por tipo" in reporte
