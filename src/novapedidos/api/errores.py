"""Respuestas de error de la API en español (`DECISIONES.md` D22, D23).

Todas tienen la forma `{"error": <código>, "mensaje": <explicación>, "detalles": [...]}`.
Los errores de validación (422) traducen cada error de Pydantic a un mensaje claro que nombra
el campo; `detalles` conserva campo, mensaje y tipo de cada problema.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from novapedidos.api.esquemas import DetalleError, RespuestaError

CAMPOS_ESPERADOS = "cliente_id, fecha_hora y mensajes"


def respuesta_error(
    codigo_http: int,
    error: str,
    mensaje: str,
    detalles: Sequence[DetalleError] = (),
    encabezados: dict[str, str] | None = None,
) -> JSONResponse:
    """Arma una respuesta JSON de error con el formato común."""
    cuerpo = RespuestaError(error=error, mensaje=mensaje, detalles=list(detalles))
    return JSONResponse(
        status_code=codigo_http, content=cuerpo.model_dump(mode="json"), headers=encabezados
    )


def _nombre_campo(loc: Sequence[Any]) -> str:
    """`("body", "mensajes", 0)` → `"mensajes[0]"`; `("body",)` → `"cuerpo"`."""
    partes = [p for p in loc if p != "body"]
    if not partes:
        return "cuerpo"
    texto = ""
    for parte in partes:
        texto += f"[{parte}]" if isinstance(parte, int) else (f".{parte}" if texto else str(parte))
    return texto


def _es_mensaje(loc: Sequence[Any]) -> int | None:
    """Índice del mensaje si el error es de un elemento de `mensajes`."""
    partes = [p for p in loc if p != "body"]
    if len(partes) == 2 and partes[0] == "mensajes" and isinstance(partes[1], int):
        return partes[1]
    return None


def traducir_error(error: dict[str, Any]) -> DetalleError:
    """Traduce un error de validación de Pydantic/FastAPI a un `DetalleError` en español."""
    loc = error.get("loc", ())
    tipo = str(error.get("type", "desconocido"))
    ctx = error.get("ctx") or {}
    campo = "cuerpo" if tipo == "json_invalid" else _nombre_campo(loc)
    indice = _es_mensaje(loc)
    sujeto = f"El mensaje {indice + 1} de 'mensajes'" if indice is not None else f"'{campo}'"

    if tipo == "json_invalid":
        mensaje = "El cuerpo de la solicitud no es JSON válido."
    elif tipo == "missing" and campo == "cuerpo":
        mensaje = f"Falta el cuerpo de la solicitud: envíe un objeto JSON con {CAMPOS_ESPERADOS}."
    elif tipo == "missing":
        mensaje = f"Falta el campo obligatorio '{campo}'."
    elif tipo in ("model_attributes_type", "dict_type", "model_type") and campo == "cuerpo":
        mensaje = f"El cuerpo debe ser un objeto JSON con {CAMPOS_ESPERADOS}."
    elif tipo == "string_type":
        mensaje = f"{sujeto} debe ser texto."
    elif tipo == "string_too_short":
        mensaje = (
            f"{sujeto} está vacío." if indice is not None else f"{sujeto} no puede estar vacío."
        )
    elif tipo == "string_too_long":
        mensaje = f"{sujeto} supera el máximo de {ctx.get('max_length')} caracteres."
    elif tipo == "list_type":
        mensaje = f"'{campo}' debe ser una lista de textos."
    elif tipo == "too_short" and campo == "mensajes":
        mensaje = "El campo 'mensajes' no puede estar vacío: envíe al menos un mensaje."
    elif tipo == "too_long" and campo == "mensajes":
        mensaje = f"El campo 'mensajes' admite como máximo {ctx.get('max_length')} mensajes."
    elif tipo == "value_error" and "error" in ctx:
        mensaje = str(ctx["error"])
    else:
        mensaje = f"{sujeto} no es válido: {error.get('msg', 'valor inválido')}."
    return DetalleError(campo=campo, mensaje=mensaje, tipo=tipo)


async def manejar_validacion(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 con mensaje en español; `mensaje` resume todos los problemas."""
    detalles = [traducir_error(e) for e in exc.errors()]
    mensajes: list[str] = []
    for d in detalles:
        texto = d.mensaje if d.mensaje.endswith(".") else f"{d.mensaje}."
        if texto not in mensajes:
            mensajes.append(texto)
    resumen = " ".join(mensajes) if mensajes else "La solicitud no es válida."
    return respuesta_error(422, "solicitud_invalida", resumen, detalles)


async def manejar_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Errores HTTP del enrutador (404 de ruta, 405, ...) en el mismo formato."""
    if exc.status_code == 404:
        error, mensaje = "ruta_no_encontrada", f"No existe la ruta {request.url.path}."
    elif exc.status_code == 405:
        error, mensaje = "metodo_no_permitido", f"Método {request.method} no permitido aquí."
    else:
        error, mensaje = "error_http", str(exc.detail)
    return respuesta_error(exc.status_code, error, mensaje, encabezados=exc.headers)


__all__ = ["manejar_http", "manejar_validacion", "respuesta_error", "traducir_error"]
