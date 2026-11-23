"""Pruebas de la carga y validación de datos (`novapedidos.datos`).

- Las pruebas con DataFrames sintéticos corren siempre (también en la CI, que no tiene datos).
- Las marcadas `requiere_datos` usan los parquet reales y se saltan si `datos/` no existe.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from novapedidos.datos import (
    ErrorDatos,
    Escala,
    cargar_datos,
    construir_datos,
    obtener_datos,
    parsear_dias_visita,
)

DATOS_DIR = Path(__file__).resolve().parent.parent / "datos"
hay_datos = pytest.mark.skipif(
    not (DATOS_DIR / "productos.parquet").is_file(), reason="sin datos publicados en datos/"
)


# --------------------------------------------------------------------------- sintéticos


def marcos() -> dict[str, pd.DataFrame]:
    """Un mundo mínimo y válido: 3 productos (uno agotado con sustituto), 1 cliente."""
    return {
        "productos": pd.DataFrame(
            {
                "sku": ["X01", "X02", "Y01"],
                "nombre": ["Kola 600", "Kola 3 L", "Galleta"],
                "marca": ["K", "K", "G"],
                "categoria": ["bebidas", "bebidas", "snacks"],
                "presentacion": ["600 ml", "3 L", "12 paquetes"],
                "unidad_venta": ["fardo", "fardo", "display"],
                "unidades_por_empaque": [24, 6, 12],
                "precio": [120.0, 132.0, 42.5],
                "disponible": [True, False, True],
                "sustituto_sku": [None, "X01", None],
            }
        ),
        "sinonimos": pd.DataFrame({"sku": ["X01", "X02"], "alias": ["Kola 600 ", "kola de 3"]}),
        "precios_escala": pd.DataFrame(
            {
                "categoria": ["bebidas", "bebidas", "snacks"],
                "cantidad_minima": [20, 10, 10],
                "descuento_pct": [5, 3, 3],
            }
        ),
        "clientes": pd.DataFrame(
            {
                "cliente_id": ["C1"],
                "nombre_tienda": ["Tienda"],
                "propietario": ["Ana"],
                "municipio": ["Mixco"],
                "dias_visita": ["lunes,miércoles,sabado"],
                "limite_credito": [5000],
                "saldo_pendiente": [1000],
            }
        ),
        "historial": pd.DataFrame(
            {
                "pedido_id": ["P1", "P1", "P2", "P3"],
                "cliente_id": ["C1"] * 4,
                "fecha_entrega": pd.to_datetime(
                    ["2026-11-02", "2026-11-02", "2026-11-05", "2026-11-09"]
                ),
                "sku": ["Y01", "X02", "X01", "Y01"],
                "cantidad": [2, 1, 3, 9],
                "estado": ["entregado", "entregado", "entregado", "cancelado"],
            }
        ),
    }


def construir(m: dict[str, pd.DataFrame]):
    return construir_datos(
        m["productos"], m["sinonimos"], m["precios_escala"], m["clientes"], m["historial"]
    )


def test_construye_datos_validos():
    datos = construir(marcos())
    assert set(datos.productos) == {"X01", "X02", "Y01"}
    x02 = datos.producto("X02")
    assert x02.disponible is False and x02.sustituto_sku == "X01"
    assert datos.producto("X01").sustituto_sku is None
    assert datos.producto("Y01").precio == Decimal("42.5")
    assert isinstance(datos.producto("X01").unidades_por_empaque, int)


def test_alias_normalizados_a_minusculas():
    datos = construir(marcos())
    assert datos.alias == {"kola 600": "X01", "kola de 3": "X02"}
    assert datos.sku_por_alias("  KOLA   600 ") == "X01"
    assert datos.sku_por_alias("inexistente") is None
    assert datos.alias_de("X02") == ("kola de 3",)


def test_escalas_ordenadas_por_minimo():
    datos = construir(marcos())
    assert datos.escalas["bebidas"] == (Escala(10, 3), Escala(20, 5))


def test_cliente_dias_visita_y_montos():
    cliente = construir(marcos()).cliente("C1")
    assert cliente.dias_visita == (0, 2, 5)
    assert cliente.nombres_dias_visita == ("lunes", "miércoles", "sábado")
    assert cliente.limite_credito == Decimal(5000)
    assert cliente.saldo_pendiente == Decimal(1000)


def test_ultimo_pedido_entregado_ignora_cancelados():
    datos = construir(marcos())
    ultimo = datos.ultimo_pedido_entregado("C1")
    assert ultimo.pedido_id == "P2"
    assert ultimo.fecha_entrega == date(2026, 11, 5)
    assert [(ln.sku, ln.cantidad) for ln in ultimo.lineas] == [("X01", 3)]
    assert datos.ultimo_pedido_entregado("C-NO") is None


def test_lineas_del_historial_conservan_orden():
    datos = construir(marcos())
    p1 = datos.pedidos["C1"][0]
    assert [ln.sku for ln in p1.lineas] == ["Y01", "X02"]
    assert isinstance(p1.fecha_entrega, date)


def test_datos_inmutables():
    datos = construir(marcos())
    with pytest.raises(TypeError):
        datos.productos["Z"] = None  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        datos.producto("X01").precio = Decimal(1)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("archivo", "cambio", "mensaje"),
    [
        ("productos", lambda df: df.drop(columns=["precio"]), "faltan columnas"),
        ("productos", lambda df: df.assign(sku=["X01", "X01", "Y01"]), "duplicado"),
        ("productos", lambda df: df.assign(sustituto_sku=[None, "Z99", None]), "no existe"),
        (
            "productos",
            lambda df: df.assign(
                disponible=[False, False, True], sustituto_sku=[None, "X01", None]
            ),
            "no está disponible",
        ),
        ("productos", lambda df: df.assign(sustituto_sku=[None, "X02", None]), "propio sustituto"),
        ("productos", lambda df: df.assign(precio=[0.0, 132.0, 42.5]), "precio"),
        ("sinonimos", lambda df: df.assign(sku=["X01", "Z99"]), "sku inexistente"),
        (
            "sinonimos",
            lambda df: pd.DataFrame({"sku": ["X01", "Y01"], "alias": ["kola", "KOLA"]}),
            "apunta a X01 y a Y01",
        ),
        ("precios_escala", lambda df: df.assign(categoria=["bebidas", "bebidas", "x"]), "categ"),
        ("clientes", lambda df: df.assign(dias_visita=["lunes,domingos"]), "Día de visita"),
        ("clientes", lambda df: pd.concat([df, df]), "duplicado"),
        ("historial", lambda df: df.assign(sku=["Y01", "X02", "Z99", "Y01"]), "sku inexistente"),
        ("historial", lambda df: df.assign(cliente_id=["C1", "C1", "C9", "C1"]), "cliente"),
        ("historial", lambda df: df.assign(estado=["entregado"] * 3 + ["perdido"]), "estado"),
    ],
)
def test_errores_de_validacion(archivo, cambio, mensaje):
    m = marcos()
    m[archivo] = cambio(m[archivo])
    with pytest.raises(ErrorDatos, match=mensaje):
        construir(m)


def test_parsear_dias_visita():
    assert parsear_dias_visita("martes,viernes") == (1, 4)
    assert parsear_dias_visita(" Sábado ") == (5,)
    assert parsear_dias_visita("miercoles,lunes") == (0, 2)
    with pytest.raises(ErrorDatos):
        parsear_dias_visita("")


def test_cargar_datos_directorio_inexistente(tmp_path):
    with pytest.raises(ErrorDatos, match="No existe el directorio"):
        cargar_datos(tmp_path / "no-existe")


def test_cargar_datos_archivo_faltante(tmp_path):
    m = marcos()
    m["productos"].to_parquet(tmp_path / "productos.parquet")
    with pytest.raises(ErrorDatos, match="Falta el archivo de datos .*sinonimos.parquet"):
        cargar_datos(tmp_path)


def test_cargar_datos_desde_parquet_sintetico(tmp_path):
    m = marcos()
    nombres = {"historial": "historial_pedidos"}
    for clave, df in m.items():
        df.to_parquet(tmp_path / f"{nombres.get(clave, clave)}.parquet")
    datos = cargar_datos(tmp_path)
    assert datos.ultimo_pedido_entregado("C1").pedido_id == "P2"


# --------------------------------------------------------------------------- datos reales


@pytest.mark.requiere_datos
@hay_datos
def test_datos_reales_cifras_verificadas():
    datos = cargar_datos(DATOS_DIR)
    assert len(datos.productos) == 44
    assert len(datos.alias) == 50
    assert len(datos.clientes) == 40
    agotados = {s: p.sustituto_sku for s, p in datos.productos.items() if not p.disponible}
    assert agotados == {
        "B03": "B02",
        "B10": "B09",
        "A07": "A06",
        "L05": "L04",
        "A13": None,
        "S02": None,
        "P04": None,
    }
    assert datos.escalas["bebidas"] == (Escala(10, 3), Escala(20, 5))
    assert set(datos.escalas) == {"bebidas", "abarrotes", "snacks", "limpieza", "cuidado_personal"}
    assert {c.nombres_dias_visita for c in datos.clientes.values()} == {
        ("martes", "viernes"),
        ("lunes", "jueves"),
        ("miércoles", "sábado"),
        ("lunes", "miércoles", "viernes"),
        ("sábado",),
    }
    lineas = sum(len(p.lineas) for ps in datos.pedidos.values() for p in ps)
    assert lineas == 976


@pytest.mark.requiere_datos
@hay_datos
def test_datos_reales_ultimo_pedido_entregado():
    datos = cargar_datos(DATOS_DIR)
    # C009: el pedido más reciente (PED-00052) está cancelado; se usa PED-00051 (caso D012).
    ultimo = datos.ultimo_pedido_entregado("C009")
    assert ultimo.pedido_id == "PED-00051"
    assert [(ln.sku, ln.cantidad) for ln in ultimo.lineas] == [
        ("A07", 1),
        ("A16", 2),
        ("B04", 1),
    ]


@pytest.mark.requiere_datos
@hay_datos
def test_obtener_datos_cacheado(monkeypatch):
    from novapedidos.config import obtener_configuracion

    monkeypatch.setenv("DATOS_DIR", str(DATOS_DIR))
    obtener_configuracion.cache_clear()
    obtener_datos.cache_clear()
    try:
        assert obtener_datos() is obtener_datos()
    finally:
        obtener_configuracion.cache_clear()
        obtener_datos.cache_clear()
