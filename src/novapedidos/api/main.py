"""Aplicación FastAPI: `POST /v1/pedidos/interpretar` y `GET /health` (Anexo del enunciado).

Arranque: `uv run uvicorn novapedidos.api.main:app --port 8000`.

Puntos de inyección (sobreescribibles con `app.dependency_overrides` en las pruebas):

- `obtener_interprete` → `Interprete` (por defecto, según `INTERPRETE`; D25).
- `obtener_proveedor_datos` → función sin argumentos que devuelve `Datos` (por defecto, los
  parquet de `DATOS_DIR`, cargados una vez).
- `obtener_registrador` → `RegistradorTrazas` (por defecto no registra; gancho para MLflow).

Códigos (D22–D24): 200 pedido · 404 cliente inexistente · 422 solicitud inválida · 503
intérprete, datos o error interno (nunca un 500 crudo).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from novapedidos.api.errores import manejar_http, manejar_validacion, respuesta_error
from novapedidos.api.esquemas import RespuestaError, RespuestaSalud, SolicitudPedido
from novapedidos.api.interpretes import InterpreteFalso, InterpreteNoDisponible, crear_interprete
from novapedidos.api.salud import MonitorMlflow
from novapedidos.api.trazas import RegistradorNulo, RegistradorTrazas, RegistroSolicitud
from novapedidos.config import Configuracion, obtener_configuracion
from novapedidos.contrato import RespuestaPedido
from novapedidos.datos import Datos, ErrorDatos, cargar_datos
from novapedidos.llm.interfaz import ErrorInterprete, Interprete
from novapedidos.motor import ErrorClienteInexistente, InterpretacionResuelta, armar_pedido

logger = logging.getLogger("novapedidos.api")

ProveedorDatos = Callable[[], Datos]


@lru_cache(maxsize=4)
def _datos_de(directorio: Path) -> Datos:
    """Carga los parquet una vez por directorio (los errores no se cachean: se reintenta)."""
    return cargar_datos(directorio)


# --------------------------------------------------------------------------- dependencias


def obtener_configuracion_api(request: Request) -> Configuracion:
    return request.app.state.configuracion


def obtener_interprete(request: Request) -> Interprete:
    return request.app.state.interprete


def obtener_proveedor_datos(request: Request) -> ProveedorDatos:
    directorio = Path(request.app.state.configuracion.datos_dir)
    return lambda: _datos_de(directorio)


def obtener_registrador(request: Request) -> RegistradorTrazas:
    return request.app.state.registrador


# --------------------------------------------------------------------------- utilidades


def _describir_error_datos(exc: Exception) -> str:
    if isinstance(exc, ErrorDatos):
        return f"datos no disponibles: {exc}"
    return f"datos no disponibles: {type(exc).__name__}"


def _registrar(registrador: RegistradorTrazas, registro: RegistroSolicitud) -> None:
    """Nunca deja que el registro de trazas afecte la respuesta."""
    try:
        registrador.registrar(registro)
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo registrar la traza de la solicitud", exc_info=True)


_RESPUESTAS_ERROR = {
    404: {"model": RespuestaError, "description": "Cliente inexistente."},
    422: {"model": RespuestaError, "description": "Solicitud inválida (con el motivo)."},
    503: {
        "model": RespuestaError,
        "description": "Intérprete o datos no disponibles, o error interno.",
    },
}


# --------------------------------------------------------------------------- aplicación


def crear_app(
    configuracion: Configuracion | None = None,
    *,
    interprete: Interprete | None = None,
    registrador: RegistradorTrazas | None = None,
) -> FastAPI:
    """Crea la aplicación. Sin argumentos usa la configuración del entorno (`.env`)."""
    config = configuracion or obtener_configuracion()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        # Precarga de datos: si falla, la API arranca igual (503 en pedidos, /health degradado).
        try:
            _datos_de(Path(config.datos_dir))
        except Exception as exc:  # noqa: BLE001
            logger.warning("No se pudieron precargar los datos: %s", exc)
        yield

    app = FastAPI(
        title="NovaPedidos",
        description=(
            "Convierte mensajes de WhatsApp de los tenderos de NovaDistribuidora en pedidos "
            "estructurados. Los cálculos (precios, descuentos, fecha, crédito) los hace código "
            "probado; el modelo de lenguaje solo interpreta los mensajes."
        ),
        version="0.1.0",
        lifespan=ciclo_de_vida,
    )
    app.state.configuracion = config
    app.state.interprete = interprete if interprete is not None else crear_interprete(config)
    app.state.registrador = registrador or RegistradorNulo()
    app.state.monitor_mlflow = MonitorMlflow(config.mlflow_tracking_uri)

    app.add_exception_handler(RequestValidationError, manejar_validacion)
    app.add_exception_handler(StarletteHTTPException, manejar_http)

    async def manejar_inesperado(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Error inesperado en %s", request.url.path, exc_info=exc)
        return respuesta_error(
            503, "error_interno", "Error interno inesperado; intente de nuevo más tarde."
        )

    app.add_exception_handler(Exception, manejar_inesperado)

    @app.post(
        "/v1/pedidos/interpretar",
        response_model=RespuestaPedido,
        responses=_RESPUESTAS_ERROR,
        summary="Interpreta los mensajes de un cliente y propone el pedido",
        tags=["pedidos"],
    )
    def interpretar_pedido(
        solicitud: SolicitudPedido,
        interprete: Interprete = Depends(obtener_interprete),  # noqa: B008
        proveedor_datos: ProveedorDatos = Depends(obtener_proveedor_datos),  # noqa: B008
        registrador: RegistradorTrazas = Depends(obtener_registrador),  # noqa: B008
    ) -> RespuestaPedido | JSONResponse:
        inicio = time.perf_counter()

        def terminar(
            codigo: int,
            respuesta: RespuestaPedido | None = None,
            error: str | None = None,
            mensaje: str = "",
        ) -> RespuestaPedido | JSONResponse:
            _registrar(
                registrador,
                RegistroSolicitud(
                    solicitud=solicitud,
                    codigo_http=codigo,
                    latencia_s=time.perf_counter() - inicio,
                    respuesta=respuesta,
                    error=error,
                ),
            )
            if respuesta is not None:
                return respuesta
            assert error is not None
            return respuesta_error(codigo, error, mensaje)

        try:
            datos = proveedor_datos()
        except Exception as exc:  # noqa: BLE001
            logger.error("Datos no disponibles: %s", exc)
            return terminar(
                503,
                error="datos_no_disponibles",
                mensaje="Los datos del catálogo no están disponibles (revise DATOS_DIR).",
            )

        no_encontrado = f"No existe un cliente con cliente_id '{solicitud.cliente_id}'."
        if datos.cliente(solicitud.cliente_id) is None:
            return terminar(404, error="cliente_no_encontrado", mensaje=no_encontrado)

        try:
            interpretacion = interprete.interpretar(
                solicitud.cliente_id, list(solicitud.mensajes), solicitud.fecha_hora
            )
            if not isinstance(interpretacion, InterpretacionResuelta):
                raise ErrorInterprete(
                    f"El intérprete devolvió {type(interpretacion).__name__}, "
                    "no una InterpretacionResuelta."
                )
        except ErrorInterprete as exc:
            logger.warning("Fallo del intérprete: %s", exc)
            return terminar(
                503,
                error="interprete_no_disponible",
                mensaje=f"No se pudieron interpretar los mensajes: {exc}",
            )
        except Exception:  # noqa: BLE001
            logger.exception("Error inesperado del intérprete")
            return terminar(
                503,
                error="interprete_no_disponible",
                mensaje="No se pudieron interpretar los mensajes por un error del intérprete; "
                "intente de nuevo más tarde.",
            )

        try:
            respuesta = armar_pedido(
                solicitud.cliente_id, solicitud.fecha_hora, interpretacion, datos
            )
        except ErrorClienteInexistente:
            return terminar(404, error="cliente_no_encontrado", mensaje=no_encontrado)
        except Exception:  # noqa: BLE001
            logger.exception("Error inesperado al armar el pedido")
            return terminar(
                503,
                error="error_interno",
                mensaje="Error interno al armar el pedido; intente de nuevo más tarde.",
            )
        return terminar(200, respuesta=respuesta)

    @app.get(
        "/health",
        response_model=RespuestaSalud,
        response_model_exclude_none=True,
        summary="Estado del servicio y de sus dependencias",
        tags=["salud"],
    )
    def salud(
        request: Request,
        interprete: Interprete = Depends(obtener_interprete),  # noqa: B008
        proveedor_datos: ProveedorDatos = Depends(obtener_proveedor_datos),  # noqa: B008
        configuracion: Configuracion = Depends(obtener_configuracion_api),  # noqa: B008
    ) -> RespuestaSalud:
        motivos: list[str] = []
        dependencias: dict[str, str] = {}

        mlflow = request.app.state.monitor_mlflow.estado()
        dependencias["mlflow"] = mlflow.detalle
        if not mlflow.disponible:
            motivos.append(mlflow.detalle)

        try:
            proveedor_datos()
            dependencias["datos"] = "ok"
        except Exception as exc:  # noqa: BLE001
            descripcion = _describir_error_datos(exc)
            dependencias["datos"] = descripcion
            motivos.append(descripcion)

        if isinstance(interprete, InterpreteNoDisponible):
            dependencias["interprete"] = interprete.motivo
            motivos.append(f"intérprete no disponible: {interprete.motivo}")
        elif isinstance(interprete, InterpreteFalso):
            dependencias["interprete"] = "falso"
        elif not configuracion.tiene_clave:
            dependencias["interprete"] = "falta OPENROUTER_API_KEY"
            motivos.append("intérprete LLM sin clave: falta OPENROUTER_API_KEY")
        else:
            dependencias["interprete"] = "llm"

        if motivos:
            return RespuestaSalud(
                estado="degradado", motivo="; ".join(motivos), dependencias=dependencias
            )
        return RespuestaSalud(estado="ok", dependencias=dependencias)

    return app


app = crear_app()

__all__ = [
    "app",
    "crear_app",
    "obtener_configuracion_api",
    "obtener_interprete",
    "obtener_proveedor_datos",
    "obtener_registrador",
]
