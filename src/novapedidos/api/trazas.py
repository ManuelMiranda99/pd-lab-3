"""Trazas de cada solicitud en MLflow (`DECISIONES.md` D25 y D42–D43).

La API, por cada `POST /v1/pedidos/interpretar`:

1. abre la traza con `registrador.abrir_traza(solicitud)` (tramo raíz `interpretar_pedido`
   con la solicitud como entrada); dentro quedan los tramos `interprete_llm` → llamada de
   LangChain (autolog) y `resolucion_productos`, y `motor_armar_pedido`;
2. al terminar (200, 404 o 503) llama a `registrador.registrar(RegistroSolicitud)`, que fija
   la salida (estado, total, motivos o el error), el código HTTP, la latencia y las
   etiquetas de la traza.

Ambas llamadas van dentro de `try/except` en la API: un registrador que falla **nunca**
afecta la respuesta (regla 6 de `CLAUDE.md`).

`RegistradorMlflow` está pensado para que MLflow caído o colgado no se note en la latencia:

- No hace **ninguna** llamada de red en el hilo de la solicitud. El experimento se busca o
  crea en un hilo de fondo que reintenta cada `reintento_s` segundos hasta que MLflow
  responde; mientras tanto las solicitudes no se trazan (la API arranca aunque MLflow no
  esté y se conecta sola cuando aparece).
- El destino de las trazas se fija con el id del experimento (`set_destination`), así abrir
  un tramo no consulta al servidor.
- MLflow exporta las trazas en segundo plano (`MLFLOW_ENABLE_ASYNC_TRACE_LOGGING`, cola
  acotada); con MLflow caído la exportación falla o descarta trazas en esos hilos, y al
  volver las trazas nuevas se registran sin reiniciar la API. Timeout y reintentos HTTP de
  MLflow se acotan con `ENTORNO_MLFLOW_API` (valores por defecto; el entorno manda).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Protocol

from novapedidos.api.esquemas import SolicitudPedido
from novapedidos.contrato import RespuestaPedido
from novapedidos.trazado import tramo

logger = logging.getLogger("novapedidos.api.trazas")

#: Nombre del tramo raíz de cada solicitud.
TRAMO_SOLICITUD = "interpretar_pedido"

#: Configuración de MLflow para el proceso de la API (D43). Se aplica con `setdefault`: una
#: variable ya definida en el entorno (p. ej. en Docker) tiene prioridad.
ENTORNO_MLFLOW_API: Mapping[str, str] = {
    # Exportación de trazas en hilos de fondo (es el valor por defecto; se fija explícito).
    "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING": "true",
    # Cola acotada: con MLflow caído se descartan trazas en vez de acumular memoria.
    "MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE": "200",
    "MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS": "4",
    # Cada llamada HTTP a MLflow: 3 s como máximo y 1 reintento (por defecto 120 s y 7).
    "MLFLOW_HTTP_REQUEST_TIMEOUT": "3",
    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "1",
    "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR": "1",
    "MLFLOW_HTTP_REQUEST_BACKOFF_JITTER": "0.5",
    # Sin el aviso para agentes que imprime `import mlflow` en 3.18.
    "MLFLOW_DISABLE_AGENT_HINT": "1",
}


@dataclass(frozen=True, slots=True)
class RegistroSolicitud:
    """Lo que se traza de una solicitud."""

    solicitud: SolicitudPedido
    codigo_http: int
    latencia_s: float
    respuesta: RespuestaPedido | None = None
    error: str | None = None  # código de error de la API (p. ej. "interprete_no_disponible")


class RegistradorTrazas(Protocol):
    """Destino de las trazas (MLflow u otro).

    Opcionalmente puede tener `abrir_traza(solicitud) -> AbstractContextManager` (la API lo
    usa para envolver la solicitud completa; si no existe, no se abre nada).
    """

    def registrar(self, registro: RegistroSolicitud) -> None: ...


class RegistradorNulo:
    """No registra nada (MLflow desactivado con `MLFLOW_TRACKING_URI` vacía, y en pruebas)."""

    def abrir_traza(self, solicitud: SolicitudPedido) -> AbstractContextManager[Any]:
        return nullcontext()

    def registrar(self, registro: RegistroSolicitud) -> None:
        return None


def aplicar_entorno_mlflow(valores: Mapping[str, str] = ENTORNO_MLFLOW_API) -> None:
    """Fija los valores por defecto de MLflow del proceso sin pisar los ya definidos."""
    for clave, valor in valores.items():
        os.environ.setdefault(clave, valor)


def _resumen_salida(registro: RegistroSolicitud) -> dict[str, Any]:
    if registro.respuesta is not None:
        return registro.respuesta.model_dump(mode="json")
    return {"codigo_http": registro.codigo_http, "error": registro.error}


class RegistradorMlflow:
    """Traza cada solicitud en un experimento de MLflow sin bloquear la respuesta (D42–D43).

    - `tracking_uri`: servidor de MLflow (`http://…`) o almacén local (`sqlite:`/`file:`).
    - `experimento`: nombre del experimento de las trazas (se crea si no existe).
    - `modelo`: modelo del intérprete (etiqueta de la traza).
    - `autolog_langchain`: activar `mlflow.langchain.autolog()` (tramo de la llamada al LLM
      con el prompt y la salida estructurada).
    - `reintento_s`: espera entre intentos de conexión mientras MLflow no responde.
    - `conectar`: iniciar el hilo de conexión al construirse (las pruebas lo apagan).
    """

    def __init__(
        self,
        tracking_uri: str,
        experimento: str,
        *,
        modelo: str = "",
        autolog_langchain: bool = True,
        reintento_s: float = 5.0,
        conectar: bool = True,
        entorno: Mapping[str, str] | None = ENTORNO_MLFLOW_API,
    ) -> None:
        self.tracking_uri = tracking_uri.strip()
        self.experimento = experimento
        self.modelo = modelo
        self.autolog_langchain = autolog_langchain
        self.reintento_s = reintento_s
        self.experiment_id: str | None = None
        self.ultimo_error: str | None = None
        self.intentos = 0
        self.trazadas = 0
        self.sin_trazar = 0
        self._listo = threading.Event()
        self._parar = threading.Event()
        self._hilo: threading.Thread | None = None
        if entorno is not None:
            aplicar_entorno_mlflow(entorno)
        if conectar:
            self.iniciar()

    # ------------------------------------------------------------------ conexión

    @property
    def listo(self) -> bool:
        """MLflow respondió y el experimento está resuelto: las solicitudes se trazan."""
        return self._listo.is_set()

    def iniciar(self) -> None:
        """Lanza el hilo que se conecta a MLflow (idempotente)."""
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._hilo = threading.Thread(
            target=self._bucle_conexion, name="novapedidos-mlflow-conexion", daemon=True
        )
        self._hilo.start()

    def esperar_conexion(self, timeout: float) -> bool:
        """Espera (como máximo `timeout` s) a que la conexión quede lista. Para pruebas."""
        return self._listo.wait(timeout)

    def detener(self) -> None:
        """Detiene los reintentos de conexión (no toca las trazas ya en cola)."""
        self._parar.set()

    def _bucle_conexion(self) -> None:
        while not self._parar.is_set():
            if self.conectar_una_vez():
                return
            self._parar.wait(self.reintento_s)

    def conectar_una_vez(self) -> bool:
        """Un intento: fija la URI, busca o crea el experimento y activa el trazado.

        Se ejecuta en el hilo de fondo; nunca lanza. Devuelve si quedó listo.
        """
        self.intentos += 1
        try:
            import mlflow
            from mlflow.entities.trace_location import MlflowExperimentLocation

            # Primero se consulta con un cliente propio (sin tocar el estado global de
            # mlflow); la URI global se fija solo cuando el servidor respondió.
            cliente = mlflow.MlflowClient(self.tracking_uri)
            experimento = cliente.get_experiment_by_name(self.experimento)
            if experimento is None:
                experiment_id = cliente.create_experiment(self.experimento)
            else:
                experiment_id = experimento.experiment_id
                if experimento.lifecycle_stage == "deleted":
                    cliente.restore_experiment(experiment_id)
            mlflow.set_tracking_uri(self.tracking_uri)
            mlflow.tracing.set_destination(MlflowExperimentLocation(experiment_id=experiment_id))
            if self.autolog_langchain:
                try:
                    import mlflow.langchain

                    mlflow.langchain.autolog(silent=True)
                except Exception:  # noqa: BLE001 - sin autolog igual hay tramos propios
                    logger.warning("No se pudo activar mlflow.langchain.autolog", exc_info=True)
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {str(exc)[:200]}"
            if error != self.ultimo_error:
                logger.warning(
                    "MLflow no disponible para trazas (%s); se reintenta cada %.0f s",
                    error,
                    self.reintento_s,
                )
            self.ultimo_error = error
            return False
        self.experiment_id = experiment_id
        self.ultimo_error = None
        self._listo.set()
        logger.info(
            "Trazas de MLflow activas: experimento %r (id %s) en %s",
            self.experimento,
            experiment_id,
            self.tracking_uri,
        )
        return True

    # ------------------------------------------------------------------ por solicitud

    def abrir_traza(self, solicitud: SolicitudPedido) -> AbstractContextManager[Any]:
        """Tramo raíz de la solicitud (no hace nada si MLflow aún no está conectado)."""
        if not self.listo:
            self.sin_trazar += 1
            return nullcontext()
        return tramo(TRAMO_SOLICITUD, "CHAIN", lambda: solicitud.model_dump(mode="json"), raiz=True)

    def registrar(self, registro: RegistroSolicitud) -> None:
        """Salida, atributos y etiquetas del tramo raíz activo (sin red; nunca lanza)."""
        if not self.listo:
            return
        mlflow = sys.modules.get("mlflow")
        if mlflow is None:
            return
        try:
            span = mlflow.get_current_active_span()
            if span is None or span.name != TRAMO_SOLICITUD:
                return
            respuesta = registro.respuesta
            atributos: dict[str, Any] = {
                "http.codigo": registro.codigo_http,
                "latencia_total_s": round(registro.latencia_s, 4),
                "cliente_id": registro.solicitud.cliente_id,
                "modelo": self.modelo or None,
            }
            etiquetas: dict[str, str] = {
                "servicio": "novapedidos-api",
                "http.codigo": str(registro.codigo_http),
                "latencia_total_s": f"{registro.latencia_s:.3f}",
                "cliente_id": registro.solicitud.cliente_id,
            }
            if self.modelo:
                etiquetas["modelo"] = self.modelo
            if respuesta is not None:
                atributos |= {
                    "pedido.estado": respuesta.estado.value,
                    "pedido.total": float(respuesta.total),
                    "pedido.lineas": len(respuesta.lineas),
                    "pedido.pendientes": len(respuesta.pendientes),
                    "pedido.motivos_revision": [m.value for m in respuesta.motivos_revision],
                }
                etiquetas["pedido.estado"] = respuesta.estado.value
                etiquetas["pedido.total"] = f"{float(respuesta.total):.2f}"
                if respuesta.motivos_revision:
                    etiquetas["pedido.motivos_revision"] = ",".join(
                        m.value for m in respuesta.motivos_revision
                    )
            if registro.error:
                atributos["error"] = registro.error
                etiquetas["error"] = registro.error
            span.set_outputs(_resumen_salida(registro))
            span.set_attributes({k: v for k, v in atributos.items() if v is not None})
            mlflow.update_current_trace(
                tags=etiquetas,
                state="ERROR" if registro.codigo_http >= 500 else None,
            )
            self.trazadas += 1
        except Exception:  # noqa: BLE001
            logger.warning("No se pudo completar la traza de la solicitud", exc_info=True)


def crear_registrador(
    tracking_uri: str, experimento: str, *, modelo: str = "", activo: bool = True
) -> RegistradorTrazas:
    """`RegistradorMlflow` si hay `MLFLOW_TRACKING_URI` y las trazas están activas; si no, nulo.

    Nunca lanza: la API arranca aunque MLflow no exista (D43).
    """
    if not activo or not tracking_uri.strip():
        return RegistradorNulo()
    try:
        return RegistradorMlflow(tracking_uri, experimento, modelo=modelo)
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo crear el registrador de MLflow", exc_info=True)
        return RegistradorNulo()


__all__ = [
    "ENTORNO_MLFLOW_API",
    "TRAMO_SOLICITUD",
    "RegistradorMlflow",
    "RegistradorNulo",
    "RegistradorTrazas",
    "RegistroSolicitud",
    "aplicar_entorno_mlflow",
    "crear_registrador",
]
