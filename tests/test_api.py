"""Pruebas del contrato de la API (`novapedidos.api`).

Corren siempre (también en la CI, sin `datos/` ni clave): usan un catálogo sintético con los
mismos sku y precios que el ejemplo del Anexo (`construir_datos`) y un `InterpreteFalso`
inyectado con `app.dependency_overrides`. La única prueba con datos reales (`requiere_datos`)
compara la respuesta del ejemplo del Anexo con la del enunciado.
"""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from novapedidos.api.esquemas import MAX_LARGO_MENSAJE, MAX_MENSAJES
from novapedidos.api.interpretes import (
    INTERPRETACION_ANEXO,
    MENSAJES_ANEXO,
    InterpreteFalso,
    InterpreteNoDisponible,
    crear_interprete,
)
from novapedidos.api.main import crear_app, obtener_interprete, obtener_proveedor_datos
from novapedidos.api.salud import EstadoDependencia, MonitorMlflow
from novapedidos.api.trazas import RegistroSolicitud
from novapedidos.config import Configuracion
from novapedidos.datos import ErrorDatos, construir_datos
from novapedidos.llm import ErrorInterprete, Interprete
from novapedidos.motor import InterpretacionResuelta, Mencion, Resolucion

RAIZ = Path(__file__).resolve().parent.parent
DATOS_REALES = RAIZ / "datos"
URL = "/v1/pedidos/interpretar"

CLAVES_RESPUESTA = [
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
CLAVES_LINEA = ["sku", "cantidad", "precio_unitario", "descuento_pct", "subtotal"]
CLAVES_PENDIENTE = ["motivo", "texto", "opciones"]

SOLICITUD_ANEXO = {
    "cliente_id": "C001",
    "fecha_hora": "2026-11-23T15:20",
    "mensajes": list(MENSAJES_ANEXO),
}
MENSAJES_CONSULTA = ["¿A qué hora pasa el camión?"]
MENSAJES_DESCUENTO = ["mándeme 3 cajas de aceite dorado, por favor"]


# --------------------------------------------------------------------------- mundo sintético


@pytest.fixture(scope="module")
def datos():
    return construir_datos_prueba()


def construir_datos_prueba():
    """Mismos sku/precios que el Anexo: B01 (fardo 24, Q120), B11 (fardo 12, Q150), A06 (caja,
    Q228), A07 (agotado → A06). C001 visita lunes/jueves; C002 con crédito casi agotado."""
    productos = pd.DataFrame(
        {
            "sku": ["B01", "B11", "A06", "A07"],
            "nombre": [
                "Kola Norte 600 ml",
                "Energía Max 473 ml",
                "Aceite Girasol Dorado 750 ml",
                "Aceite Palma Real 750 ml",
            ],
            "marca": ["Kola Norte", "Energía Max", "Dorado", "Palma Real"],
            "categoria": ["bebidas", "bebidas", "abarrotes", "abarrotes"],
            "presentacion": ["600 ml", "473 ml", "750 ml", "750 ml"],
            "unidad_venta": ["fardo", "fardo", "caja", "caja"],
            "unidades_por_empaque": [24, 12, 12, 12],
            "precio": [120.0, 150.0, 228.0, 192.0],
            "disponible": [True, True, True, False],
            "sustituto_sku": [None, None, None, "A06"],
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


def _config(**cambios) -> Configuracion:
    """Configuración aislada del `.env` local."""
    base = {
        "interprete": "falso",
        "mlflow_tracking_uri": "",
        "openrouter_api_key": "",
        "datos_dir": Path("/no/existe"),
    }
    base.update(cambios)
    return Configuracion(_env_file=None, **base)


def _interprete_pruebas() -> InterpreteFalso:
    return InterpreteFalso(
        {
            MENSAJES_ANEXO: INTERPRETACION_ANEXO,
            tuple(MENSAJES_CONSULTA): InterpretacionResuelta(es_pedido=False),
            tuple(MENSAJES_DESCUENTO): InterpretacionResuelta(
                es_pedido=True,
                menciones=[
                    Mencion(texto="aceite dorado", resolucion=Resolucion.SKU, sku="A06", cantidad=3)
                ],
            ),
        }
    )


def _cliente(datos, interprete: Interprete | None = None, **config) -> TestClient:
    app = crear_app(_config(**config))
    app.dependency_overrides[obtener_proveedor_datos] = lambda: lambda: datos
    app.dependency_overrides[obtener_interprete] = lambda: interprete or _interprete_pruebas()
    return TestClient(app)


@pytest.fixture
def cliente(datos) -> TestClient:
    return _cliente(datos)


def _puerto_cerrado() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- 200


def test_respuesta_200_tiene_la_forma_exacta_del_contrato(cliente):
    r = cliente.post(URL, json=SOLICITUD_ANEXO)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    cuerpo = r.json()
    assert list(cuerpo) == CLAVES_RESPUESTA
    assert isinstance(cuerpo["cliente_id"], str)
    assert cuerpo["estado"] in ("confirmado", "requiere_revision", "fuera_de_alcance")
    assert isinstance(cuerpo["total"], int | float)
    assert round(cuerpo["total"], 2) == cuerpo["total"]
    assert isinstance(cuerpo["fecha_entrega"], str)
    datetime.strptime(cuerpo["fecha_entrega"], "%Y-%m-%d")
    for linea in cuerpo["lineas"]:
        assert list(linea) == CLAVES_LINEA
        assert isinstance(linea["sku"], str)
        assert isinstance(linea["cantidad"], int) and linea["cantidad"] >= 1
        assert isinstance(linea["descuento_pct"], int)
        for monto in ("precio_unitario", "subtotal"):
            assert isinstance(linea[monto], int | float)
            assert round(linea[monto], 2) == linea[monto]
    for pendiente in cuerpo["pendientes"]:
        assert list(pendiente) == CLAVES_PENDIENTE
        assert isinstance(pendiente["opciones"], list)
    assert isinstance(cuerpo["sustituciones"], list)
    assert all(isinstance(m, str) for m in cuerpo["motivos_revision"])
    assert isinstance(cuerpo["mensaje_cliente"], str) and cuerpo["mensaje_cliente"]


def test_ejemplo_del_anexo_con_catalogo_sintetico(cliente):
    cuerpo = cliente.post(URL, json=SOLICITUD_ANEXO).json()
    assert cuerpo["cliente_id"] == "C001"
    assert cuerpo["estado"] == "requiere_revision"
    assert cuerpo["lineas"] == [
        {
            "sku": "B01",
            "cantidad": 2,
            "precio_unitario": 120.0,
            "descuento_pct": 0,
            "subtotal": 240.0,
        },
        {
            "sku": "B11",
            "cantidad": 4,
            "precio_unitario": 150.0,
            "descuento_pct": 0,
            "subtotal": 600.0,
        },
    ]
    assert cuerpo["total"] == 840.0
    assert cuerpo["fecha_entrega"] == "2026-11-26"
    assert cuerpo["sustituciones"] == []
    assert len(cuerpo["pendientes"]) == 1
    pendiente = cuerpo["pendientes"][0]
    assert pendiente["motivo"] == "producto_ambiguo"
    assert "aceite" in pendiente["texto"]
    assert pendiente["opciones"] == ["A06", "A07"]
    assert cuerpo["motivos_revision"] == ["producto_ambiguo"]


def test_interprete_recibe_cliente_mensajes_y_fecha_hora_parseada(datos):
    interprete = _interprete_pruebas()
    _cliente(datos, interprete).post(URL, json=SOLICITUD_ANEXO)
    assert interprete.llamadas == [("C001", list(MENSAJES_ANEXO), datetime(2026, 11, 23, 15, 20))]


@pytest.mark.requiere_datos
@pytest.mark.skipif(
    not (DATOS_REALES / "productos.parquet").is_file(), reason="sin datos publicados en datos/"
)
def test_ejemplo_del_anexo_con_datos_reales_igual_al_enunciado():
    """Con los parquet reales e `INTERPRETE=falso` (sin overrides), igual al PDF salvo el
    `texto` del pendiente (D09) y el `mensaje_cliente` (plantilla, D20)."""
    app = crear_app(_config(datos_dir=DATOS_REALES, interprete="falso"))
    cuerpo = TestClient(app).post(URL, json=SOLICITUD_ANEXO).json()
    esperado = {
        "cliente_id": "C001",
        "estado": "requiere_revision",
        "lineas": [
            {"sku": "B01", "cantidad": 2, "precio_unitario": 120.00, "descuento_pct": 0,
             "subtotal": 240.00},
            {"sku": "B11", "cantidad": 4, "precio_unitario": 150.00, "descuento_pct": 0,
             "subtotal": 600.00},
        ],
        "total": 840.00,
        "fecha_entrega": "2026-11-26",
        "sustituciones": [],
        "motivos_revision": ["producto_ambiguo"],
    }  # fmt: skip
    assert {k: cuerpo[k] for k in esperado} == esperado
    assert [(p["motivo"], p["opciones"]) for p in cuerpo["pendientes"]] == [
        ("producto_ambiguo", ["A06", "A07"])
    ]
    assert "aceite" in cuerpo["pendientes"][0]["texto"]
    assert "jueves 26 de noviembre" in cuerpo["mensaje_cliente"]


def test_fuera_de_alcance_con_fecha_entrega_null(cliente):
    r = cliente.post(
        URL,
        json={
            "cliente_id": "C001",
            "fecha_hora": "2026-11-23T09:00",
            "mensajes": MENSAJES_CONSULTA,
        },
    )
    assert r.status_code == 200
    cuerpo = r.json()
    assert list(cuerpo) == CLAVES_RESPUESTA
    assert cuerpo["estado"] == "fuera_de_alcance"
    assert cuerpo["fecha_entrega"] is None
    assert cuerpo["lineas"] == [] and cuerpo["pendientes"] == []
    assert cuerpo["total"] == 0


def test_campos_extra_se_ignoran_y_cliente_id_se_recorta(cliente):
    solicitud = {**SOLICITUD_ANEXO, "cliente_id": " C001 ", "canal": "whatsapp", "x": [1, 2]}
    r = cliente.post(URL, json=solicitud)
    assert r.status_code == 200
    assert r.json()["cliente_id"] == "C001"


def test_mensajes_de_precio_no_alteran_montos(cliente):
    """Montos con 2 decimales: 3 cajas × 228 = 684.00 (sin descuento de abarrotes)."""
    r = cliente.post(
        URL,
        json={
            "cliente_id": "C001",
            "fecha_hora": "2026-11-23T10:00",
            "mensajes": MENSAJES_DESCUENTO,
        },
    )
    assert r.status_code == 200
    assert r.json()["total"] == 684.0
    assert '"total":684.0' in r.text


# --------------------------------------------------------------------------- 404


def test_cliente_inexistente_404_sin_llamar_al_interprete(datos):
    interprete = _interprete_pruebas()
    r = _cliente(datos, interprete).post(URL, json={**SOLICITUD_ANEXO, "cliente_id": "C999"})
    assert r.status_code == 404
    cuerpo = r.json()
    assert cuerpo["error"] == "cliente_no_encontrado"
    assert "C999" in cuerpo["mensaje"]
    assert interprete.llamadas == []


def test_ruta_inexistente_404_en_espanol(cliente):
    r = cliente.get("/v1/nada")
    assert r.status_code == 404
    assert r.json()["error"] == "ruta_no_encontrada"


# --------------------------------------------------------------------------- 422


def _sin(campo: str) -> dict:
    return {k: v for k, v in SOLICITUD_ANEXO.items() if k != campo}


CASOS_422 = {
    "sin_cliente_id": (_sin("cliente_id"), "cliente_id", "Falta el campo obligatorio 'cliente_id'"),
    "sin_mensajes": (_sin("mensajes"), "mensajes", "Falta el campo obligatorio 'mensajes'"),
    "sin_fecha_hora": (_sin("fecha_hora"), "fecha_hora", "Falta el campo obligatorio 'fecha_hora'"),
    "cuerpo_vacio": ({}, "cliente_id", "Falta el campo obligatorio"),
    "mensajes_lista_vacia": (
        {**SOLICITUD_ANEXO, "mensajes": []},
        "mensajes",
        "El campo 'mensajes' no puede estar vacío",
    ),
    "mensaje_vacio": ({**SOLICITUD_ANEXO, "mensajes": [""]}, "mensajes[0]", "está vacío"),
    "mensaje_solo_espacios": (
        {**SOLICITUD_ANEXO, "mensajes": ["hola", "   "]},
        "mensajes[1]",
        "El mensaje 2 de 'mensajes' está vacío",
    ),
    "cliente_id_vacio": (
        {**SOLICITUD_ANEXO, "cliente_id": "  "},
        "cliente_id",
        "no puede estar vacío",
    ),
    "cliente_id_numero": ({**SOLICITUD_ANEXO, "cliente_id": 1}, "cliente_id", "debe ser texto"),
    "mensajes_texto": ({**SOLICITUD_ANEXO, "mensajes": "hola"}, "mensajes", "lista de textos"),
    "mensaje_numero": ({**SOLICITUD_ANEXO, "mensajes": [5]}, "mensajes[0]", "debe ser texto"),
    "fecha_hora_numero": (
        {**SOLICITUD_ANEXO, "fecha_hora": 20261123},
        "fecha_hora",
        "AAAA-MM-DDTHH:MM",
    ),
    "fecha_hora_null": ({**SOLICITUD_ANEXO, "fecha_hora": None}, "fecha_hora", "AAAA-MM-DDTHH:MM"),
    "demasiados_mensajes": (
        {**SOLICITUD_ANEXO, "mensajes": ["hola"] * (MAX_MENSAJES + 1)},
        "mensajes",
        f"como máximo {MAX_MENSAJES} mensajes",
    ),
    "mensaje_muy_largo": (
        {**SOLICITUD_ANEXO, "mensajes": ["a" * (MAX_LARGO_MENSAJE + 1)]},
        "mensajes[0]",
        f"máximo de {MAX_LARGO_MENSAJE} caracteres",
    ),
    "cuerpo_lista": ([SOLICITUD_ANEXO], "cuerpo", "objeto JSON"),
}


@pytest.mark.parametrize("solicitud, campo, fragmento", CASOS_422.values(), ids=CASOS_422.keys())
def test_solicitud_invalida_422_con_motivo(cliente, solicitud, campo, fragmento):
    r = cliente.post(URL, json=solicitud)
    assert r.status_code == 422
    cuerpo = r.json()
    assert cuerpo["error"] == "solicitud_invalida"
    assert fragmento in cuerpo["mensaje"]
    assert campo in [d["campo"] for d in cuerpo["detalles"]]
    assert all(set(d) == {"campo", "mensaje", "tipo"} for d in cuerpo["detalles"])


@pytest.mark.parametrize(
    "fecha_hora",
    [
        "2026-11-23 15:20",
        "2026-11-23T15:20:00",
        "2026-11-23T15:20-06:00",
        "2026-11-23T15:20Z",
        "23/11/2026 15:20",
        "2026-11-23",
        "2026-2-3T1:05",
        "2026-13-01T10:00",
        "2026-02-30T10:00",
        "2026-11-23T25:00",
        "",
        "mañana",
    ],
)
def test_fecha_hora_mal_formada_422(cliente, fecha_hora):
    r = cliente.post(URL, json={**SOLICITUD_ANEXO, "fecha_hora": fecha_hora})
    assert r.status_code == 422
    cuerpo = r.json()
    assert cuerpo["error"] == "solicitud_invalida"
    assert "AAAA-MM-DDTHH:MM" in cuerpo["mensaje"]
    assert [d["campo"] for d in cuerpo["detalles"]] == ["fecha_hora"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": "esto no es json", "headers": {"content-type": "application/json"}},
        {"content": '{"cliente_id": "C001",', "headers": {"content-type": "application/json"}},
        {"content": "cliente_id=C001", "headers": {"content-type": "text/plain"}},
        {},
    ],
    ids=["texto", "json_truncado", "texto_plano", "sin_cuerpo"],
)
def test_cuerpo_no_json_422(cliente, kwargs):
    r = cliente.post(URL, **kwargs)
    assert r.status_code == 422
    cuerpo = r.json()
    assert cuerpo["error"] == "solicitud_invalida"
    assert cuerpo["mensaje"]
    assert [d["campo"] for d in cuerpo["detalles"]] == ["cuerpo"]


def test_varios_errores_se_reportan_juntos(cliente):
    r = cliente.post(URL, json={"fecha_hora": "ayer", "mensajes": []})
    assert r.status_code == 422
    campos = {d["campo"] for d in r.json()["detalles"]}
    assert campos == {"cliente_id", "fecha_hora", "mensajes"}


# --------------------------------------------------------------------------- fallos (503)


class _InterpreteQueFalla:
    def __init__(self, excepcion: Exception) -> None:
        self.excepcion = excepcion

    def interpretar(self, cliente_id, mensajes, fecha_hora):
        raise self.excepcion


class _InterpreteQueDevuelveOtraCosa:
    def interpretar(self, cliente_id, mensajes, fecha_hora):
        return {"es_pedido": True}


@pytest.mark.parametrize(
    "interprete",
    [
        _InterpreteQueFalla(ErrorInterprete("timeout del modelo")),
        _InterpreteQueFalla(RuntimeError("se cayó la conexión")),
        _InterpreteQueDevuelveOtraCosa(),
        InterpreteNoDisponible("sin configurar"),
    ],
    ids=["error_interprete", "excepcion_inesperada", "tipo_incorrecto", "no_disponible"],
)
def test_fallo_del_interprete_503_claro(datos, interprete):
    r = _cliente(datos, interprete).post(URL, json=SOLICITUD_ANEXO)
    assert r.status_code == 503
    cuerpo = r.json()
    assert cuerpo["error"] == "interprete_no_disponible"
    assert cuerpo["mensaje"].startswith("No se pudieron interpretar los mensajes")
    assert "se cayó la conexión" not in cuerpo["mensaje"]  # no se filtran excepciones internas


def test_interprete_falso_sin_entrada_en_la_tabla_no_inventa(cliente):
    r = cliente.post(URL, json={**SOLICITUD_ANEXO, "mensajes": ["algo que no está en la tabla"]})
    assert r.status_code == 503
    assert r.json()["error"] == "interprete_no_disponible"


def test_interprete_falso_por_defecto_y_funcion():
    fuera = InterpretacionResuelta(es_pedido=False)
    assert (
        InterpreteFalso(por_defecto=fuera).interpretar("C1", ["x"], datetime(2026, 1, 1)) == fuera
    )
    falso = InterpreteFalso(funcion=lambda c, m, f: fuera)
    assert falso.interpretar("C1", ["y"], datetime(2026, 1, 1)) == fuera
    assert isinstance(falso, Interprete)
    # La tabla ignora mayúsculas y espacios sobrantes.
    tabla = InterpreteFalso({"Hola  Mundo": fuera})
    assert tabla.interpretar("C1", [" hola mundo "], datetime(2026, 1, 1)) == fuera


def test_datos_no_disponibles_503_y_health_degradado(datos):
    app = crear_app(_config())  # datos_dir inexistente, sin override de datos
    app.dependency_overrides[obtener_interprete] = _interprete_pruebas
    cliente = TestClient(app)
    r = cliente.post(URL, json=SOLICITUD_ANEXO)
    assert r.status_code == 503
    assert r.json()["error"] == "datos_no_disponibles"
    salud = cliente.get("/health").json()
    assert salud["estado"] == "degradado"
    assert "datos no disponibles" in salud["motivo"]


def test_error_en_el_motor_no_es_500(datos, monkeypatch):
    import novapedidos.api.main as modulo

    def explota(*args, **kwargs):
        raise ZeroDivisionError("bug")

    monkeypatch.setattr(modulo, "armar_pedido", explota)
    r = _cliente(datos).post(URL, json=SOLICITUD_ANEXO)
    assert r.status_code == 503
    assert r.json()["error"] == "error_interno"


# ------------------------------------------------------------------- elección del intérprete


def test_interprete_por_variable_de_entorno():
    assert isinstance(crear_interprete(_config(interprete="falso")), InterpreteFalso)
    assert isinstance(crear_interprete(_config(interprete="FALSO ")), InterpreteFalso)
    invalido = crear_interprete(_config(interprete="gpt"))
    assert isinstance(invalido, InterpreteNoDisponible)
    assert "INTERPRETE='gpt'" in invalido.motivo


def test_interprete_llm_sin_clave_responde_503_y_health_degradado(datos):
    app = crear_app(_config(interprete="llm"))
    app.dependency_overrides[obtener_proveedor_datos] = lambda: lambda: datos
    cliente = TestClient(app)
    r = cliente.post(URL, json=SOLICITUD_ANEXO)
    assert r.status_code == 503
    assert r.json()["error"] == "interprete_no_disponible"
    assert "OPENROUTER_API_KEY" in r.json()["mensaje"]
    salud = cliente.get("/health").json()
    assert salud["estado"] == "degradado"
    assert "falta OPENROUTER_API_KEY" in salud["motivo"]


# --------------------------------------------------------------------------- gancho de trazas


class _Registrador:
    def __init__(self, falla: bool = False) -> None:
        self.registros: list[RegistroSolicitud] = []
        self.falla = falla

    def registrar(self, registro: RegistroSolicitud) -> None:
        self.registros.append(registro)
        if self.falla:
            raise ConnectionError("MLflow caído")


def test_gancho_de_trazas_recibe_cada_solicitud(datos):
    registrador = _Registrador()
    app = crear_app(_config(), registrador=registrador)
    app.dependency_overrides[obtener_proveedor_datos] = lambda: lambda: datos
    app.dependency_overrides[obtener_interprete] = _interprete_pruebas
    cliente = TestClient(app)
    cliente.post(URL, json=SOLICITUD_ANEXO)
    cliente.post(URL, json={**SOLICITUD_ANEXO, "cliente_id": "C999"})
    assert [r.codigo_http for r in registrador.registros] == [200, 404]
    assert registrador.registros[0].respuesta is not None
    assert registrador.registros[1].error == "cliente_no_encontrado"
    assert all(r.latencia_s >= 0 for r in registrador.registros)


def test_registrador_que_falla_no_afecta_la_respuesta(datos):
    app = crear_app(_config(), registrador=_Registrador(falla=True))
    app.dependency_overrides[obtener_proveedor_datos] = lambda: lambda: datos
    app.dependency_overrides[obtener_interprete] = _interprete_pruebas
    assert TestClient(app).post(URL, json=SOLICITUD_ANEXO).status_code == 200


# --------------------------------------------------------------------------- /health


class _MlflowFalso(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        codigo = 200 if self.path == "/health" else 404
        self.send_response(codigo)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass


@pytest.fixture
def mlflow_arriba():
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _MlflowFalso)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield f"http://127.0.0.1:{servidor.server_address[1]}"
    servidor.shutdown()
    servidor.server_close()


def test_health_ok_con_mlflow_arriba(datos, mlflow_arriba):
    r = _cliente(datos, mlflow_tracking_uri=mlflow_arriba).get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    # El MLflow falso solo responde /health: las trazas quedan "conectando" (no degrada).
    assert cuerpo == {
        "estado": "ok",
        "dependencias": {
            "mlflow": "ok",
            "trazas": "conectando",
            "datos": "ok",
            "interprete": "falso",
        },
    }


def test_health_degradado_con_mlflow_caido_y_post_sigue_respondiendo(datos):
    cliente = _cliente(datos, mlflow_tracking_uri=f"http://127.0.0.1:{_puerto_cerrado()}")
    inicio = time.perf_counter()
    r = cliente.get("/health")
    assert time.perf_counter() - inicio < 1.5
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["estado"] == "degradado"
    assert "MLflow no responde" in cuerpo["motivo"]
    assert cliente.post(URL, json=SOLICITUD_ANEXO).status_code == 200


def test_health_mlflow_desactivado_o_local_no_degrada(datos):
    assert _cliente(datos, mlflow_tracking_uri="").get("/health").json()["estado"] == "ok"
    salud = _cliente(datos, mlflow_tracking_uri="file:./mlruns").get("/health").json()
    assert salud["estado"] == "ok"
    assert salud["dependencias"]["mlflow"] == "local"


def test_health_degradado_sin_clave_con_interprete_llm(datos):
    class _InterpreteLlmFalso:
        def interpretar(self, cliente_id, mensajes, fecha_hora):
            return InterpretacionResuelta(es_pedido=False)

    salud = _cliente(datos, _InterpreteLlmFalso(), openrouter_api_key="").get("/health").json()
    assert salud["estado"] == "degradado"
    assert "OPENROUTER_API_KEY" in salud["motivo"]
    salud = _cliente(datos, _InterpreteLlmFalso(), openrouter_api_key="x").get("/health").json()
    assert salud == {
        "estado": "ok",
        "dependencias": {"mlflow": "desactivado", "datos": "ok", "interprete": "llm"},
    }


def test_health_datos_que_fallan(datos):
    app = crear_app(_config())

    def falla():
        raise ErrorDatos("Falta el archivo de datos productos.parquet")

    app.dependency_overrides[obtener_proveedor_datos] = lambda: falla
    app.dependency_overrides[obtener_interprete] = _interprete_pruebas
    salud = TestClient(app).get("/health").json()
    assert salud["estado"] == "degradado"
    assert "productos.parquet" in salud["motivo"]


def test_monitor_mlflow_cachea_el_resultado():
    reloj = [0.0]
    monitor = MonitorMlflow("http://mlflow:5000", ttl=5.0, reloj=lambda: reloj[0])
    consultas = []

    def consultar():
        consultas.append(reloj[0])
        return EstadoDependencia(False, "caído")

    monitor._consultar = consultar
    monitor.estado()
    reloj[0] = 4.9
    monitor.estado()
    assert len(consultas) == 1
    reloj[0] = 5.1
    monitor.estado()
    assert len(consultas) == 2


def test_monitor_mlflow_respeta_el_timeout():
    """Un servidor que acepta la conexión pero nunca responde: el chequeo corta en ~timeout."""
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(1)
    try:
        monitor = MonitorMlflow(f"http://127.0.0.1:{servidor.getsockname()[1]}", timeout=0.3)
        inicio = time.perf_counter()
        estado = monitor.estado()
        assert time.perf_counter() - inicio < 1.0
        assert not estado.disponible
    finally:
        servidor.close()


# --------------------------------------------------------------------------- QA del evaluador (D50)


def test_cliente_id_sin_distinguir_mayusculas(cliente):
    r = cliente.post(URL, json={**SOLICITUD_ANEXO, "cliente_id": "c001"})
    assert r.status_code == 200
    assert r.json()["cliente_id"] == "C001"


@pytest.mark.parametrize("fecha_hora", ["9999-12-31T15:00", "0001-01-01T10:00", "1999-12-31T23:59"])
def test_fecha_hora_fuera_de_rango_422_no_503(cliente, fecha_hora):
    r = cliente.post(URL, json={**SOLICITUD_ANEXO, "fecha_hora": fecha_hora})
    assert r.status_code == 422
    cuerpo = r.json()
    assert "fuera de rango" in cuerpo["mensaje"]
    assert [d["campo"] for d in cuerpo["detalles"]] == ["fecha_hora"]


def test_monitor_mlflow_no_guarda_en_cache_un_ok():
    """Una caída se ve en la siguiente consulta: el "ok" no se guarda en caché."""
    reloj = [0.0]
    monitor = MonitorMlflow("http://mlflow:5000", ttl=5.0, reloj=lambda: reloj[0])
    respuestas = [EstadoDependencia(True, "ok"), EstadoDependencia(False, "caído")]
    monitor._consultar = lambda: respuestas.pop(0)
    assert monitor.estado().disponible
    reloj[0] = 0.1
    assert not monitor.estado().disponible
