"""Trazas de MLflow y tolerancia a fallos (`DECISIONES.md` D42–D44).

Sin LLM y sin servidor MLflow real: MLflow "caído" es un puerto cerrado, "colgado" es un socket
que acepta la conexión y nunca responde, "colgado después de conectar" es un servidor falso que
responde la búsqueda del experimento y luego se cuelga, y el almacén "bueno" es un sqlite en una
carpeta temporal.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from novapedidos.api.main import crear_app, obtener_interprete, obtener_proveedor_datos
from novapedidos.api.trazas import (
    TRAMO_SOLICITUD,
    RegistradorMlflow,
    RegistradorNulo,
    crear_registrador,
)
from novapedidos.trazado import anotar_traza, hay_traza_activa, tramo
from test_api import (
    SOLICITUD_ANEXO,
    URL,
    _config,
    _interprete_pruebas,
    _puerto_cerrado,
    construir_datos_prueba,
)

mlflow = pytest.importorskip("mlflow")


@pytest.fixture(scope="module")
def datos():
    return construir_datos_prueba()


#: Latencia de POST con el intérprete falso es de milisegundos; con MLflow caído o colgado no
#: debe crecer de forma apreciable.
MAX_POST_S = 0.5


def _app(datos, registrador=None, **config):
    app = crear_app(_config(**config), registrador=registrador)
    app.dependency_overrides[obtener_proveedor_datos] = lambda: lambda: datos
    app.dependency_overrides[obtener_interprete] = _interprete_pruebas
    return app


def _medir_posts(cliente: TestClient, n: int = 5) -> list[float]:
    latencias = []
    for _ in range(n):
        inicio = time.perf_counter()
        r = cliente.post(URL, json=SOLICITUD_ANEXO)
        latencias.append(time.perf_counter() - inicio)
        assert r.status_code == 200
    return latencias


@pytest.fixture
def socket_colgado():
    """Acepta conexiones TCP (backlog) pero nunca responde: un MLflow colgado."""
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen(16)
    yield f"http://127.0.0.1:{servidor.getsockname()[1]}"
    servidor.close()


@pytest.fixture(autouse=True)
def _restaurar_mlflow():
    yield
    try:
        mlflow.tracing.reset()
        mlflow.set_tracking_uri(None)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- tramos


def test_tramo_sin_traza_activa_no_hace_nada():
    assert not hay_traza_activa()
    evaluadas = []
    with tramo("x", entradas=lambda: evaluadas.append(1)) as t:
        assert t is None
    assert evaluadas == []  # no serializa entradas si no hay traza
    anotar_traza({"a": 1})  # no lanza


def test_tramo_propaga_las_excepciones_del_codigo():
    with pytest.raises(ValueError), tramo("x"):
        raise ValueError("del código, no de MLflow")


# --------------------------------------------------------------------------- selección


def test_registrador_segun_configuracion():
    assert isinstance(crear_registrador("", "e"), RegistradorNulo)
    assert isinstance(crear_registrador("http://x:5000", "e", activo=False), RegistradorNulo)
    registrador = crear_registrador(f"http://127.0.0.1:{_puerto_cerrado()}", "e")
    assert isinstance(registrador, RegistradorMlflow)
    registrador.detener()


# --------------------------------------------------------------------------- MLflow caído


def test_api_con_mlflow_en_puerto_cerrado_responde_rapido_y_health_degradado(datos):
    app = _app(datos, mlflow_tracking_uri=f"http://127.0.0.1:{_puerto_cerrado()}")
    registrador = app.state.registrador
    assert isinstance(registrador, RegistradorMlflow)
    try:
        cliente = TestClient(app)
        assert max(_medir_posts(cliente)) < MAX_POST_S
        inicio = time.perf_counter()
        salud = cliente.get("/health").json()
        assert time.perf_counter() - inicio < 1.5
        assert salud["estado"] == "degradado"
        assert salud["dependencias"]["trazas"] == "conectando"
        assert not registrador.listo
    finally:
        registrador.detener()


def test_api_con_mlflow_colgado_responde_rapido_y_health_en_1s(datos, socket_colgado):
    app = _app(datos, mlflow_tracking_uri=socket_colgado)
    registrador = app.state.registrador
    try:
        cliente = TestClient(app)
        assert max(_medir_posts(cliente)) < MAX_POST_S
        inicio = time.perf_counter()
        salud = cliente.get("/health").json()
        assert time.perf_counter() - inicio < 1.5
        assert salud["estado"] == "degradado"
        assert "timed out" in salud["motivo"]
    finally:
        registrador.detener()


class _MlflowQueSeCuelga(BaseHTTPRequestHandler):
    """Responde la búsqueda del experimento; cualquier otra llamada (exportar trazas) se
    cuelga hasta que la prueba termina."""

    liberar = threading.Event()
    exportaciones = 0

    def _responder(self, cuerpo: dict) -> None:
        cuerpo_json = json.dumps(cuerpo).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo_json)))
        self.end_headers()
        self.wfile.write(cuerpo_json)

    def do_GET(self):  # noqa: N802
        if "experiments/get-by-name" in self.path:
            self._responder(
                {
                    "experiment": {
                        "experiment_id": "7",
                        "name": "novapedidos-api",
                        "lifecycle_stage": "active",
                        "artifact_location": "/tmp/no-usado",
                    }
                }
            )
            return
        self._colgar()

    def do_POST(self):  # noqa: N802
        self._colgar()

    def _colgar(self) -> None:
        type(self).exportaciones += 1
        self.liberar.wait(30)
        self.send_response(503)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_mlflow_que_se_cuelga_despues_de_conectar_no_frena_la_api(datos):
    """El caso más exigente: las trazas sí se crean y su exportación se cuelga."""
    _MlflowQueSeCuelga.liberar.clear()
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _MlflowQueSeCuelga)
    servidor.daemon_threads = True
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    uri = f"http://127.0.0.1:{servidor.server_address[1]}"
    registrador = RegistradorMlflow(uri, "novapedidos-api", autolog_langchain=False)
    try:
        assert registrador.esperar_conexion(15), registrador.ultimo_error
        cliente = TestClient(_app(datos, registrador))
        latencias = _medir_posts(cliente, 10)
        assert max(latencias) < MAX_POST_S, latencias
        assert registrador.trazadas == 10
        # La exportación de fondo sí intentó hablar con el servidor colgado.
        limite = time.perf_counter() + 10
        while _MlflowQueSeCuelga.exportaciones == 0 and time.perf_counter() < limite:
            time.sleep(0.1)
        assert _MlflowQueSeCuelga.exportaciones > 0
    finally:
        registrador.detener()
        _MlflowQueSeCuelga.liberar.set()
        servidor.shutdown()
        servidor.server_close()


def test_registrador_que_falla_al_abrir_la_traza_no_afecta_la_respuesta(datos):
    class _Roto:
        def abrir_traza(self, solicitud):
            raise RuntimeError("MLflow roto")

        def registrar(self, registro):
            raise RuntimeError("MLflow roto")

    assert TestClient(_app(datos, _Roto())).post(URL, json=SOLICITUD_ANEXO).status_code == 200


# --------------------------------------------------------------------------- traza completa


def test_traza_de_la_solicitud_en_un_almacen_local(datos, tmp_path):
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    registrador = RegistradorMlflow(uri, "novapedidos-api", autolog_langchain=False)
    try:
        assert registrador.esperar_conexion(60), registrador.ultimo_error
        cliente = TestClient(_app(datos, registrador))
        assert cliente.post(URL, json=SOLICITUD_ANEXO).status_code == 200
        assert cliente.post(URL, json={**SOLICITUD_ANEXO, "cliente_id": "C999"}).status_code == 404
        mlflow.flush_trace_async_logging()
        trazas = mlflow.MlflowClient(uri).search_traces(
            locations=[registrador.experiment_id], max_results=10
        )
        assert len(trazas) == 2
        por_codigo = {t.info.tags["http.codigo"]: t for t in trazas}
        ok = mlflow.MlflowClient(uri).get_trace(por_codigo["200"].info.trace_id)
        nombres = [s.name for s in ok.data.spans]
        assert nombres[0] == TRAMO_SOLICITUD
        assert "motor_armar_pedido" in nombres
        assert ok.info.tags["pedido.estado"] == "requiere_revision"
        assert ok.info.tags["cliente_id"] == "C001"
        raiz = ok.data.spans[0]
        assert raiz.inputs["cliente_id"] == "C001"
        assert raiz.outputs["estado"] == "requiere_revision"
        assert por_codigo["404"].info.tags["error"] == "cliente_no_encontrado"
    finally:
        registrador.detener()
