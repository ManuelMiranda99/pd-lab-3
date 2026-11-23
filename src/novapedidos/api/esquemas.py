"""Esquemas de la API: solicitud de `POST /v1/pedidos/interpretar`, errores y `/health`.

La respuesta 200 es `novapedidos.contrato.RespuestaPedido` (D21). Límites de la solicitud
(`DECISIONES.md` D26): `cliente_id` de 1 a 64 caracteres (sin espacios al inicio y al final y
sin distinguir mayúsculas: "c001" = "C001", D50); de 1 a 50 mensajes; cada mensaje de
1 a 2000 caracteres (sin contar espacios al inicio y al final). Los campos extra se ignoran.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    WithJsonSchema,
)

from novapedidos.motor.fechas import parsear_fecha_hora

MAX_LARGO_CLIENTE_ID = 64
MAX_MENSAJES = 50
MAX_LARGO_MENSAJE = 2000

#: Fecha y hora estricta `AAAA-MM-DDTHH:MM` (parser del motor, D14); en Python es `datetime`.
FechaHora = Annotated[
    datetime,
    BeforeValidator(parsear_fecha_hora),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
            "description": "Recepción del último mensaje, AAAA-MM-DDTHH:MM, hora de Guatemala.",
            "examples": ["2026-11-23T15:20"],
        }
    ),
]

ClienteId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, to_upper=True, min_length=1, max_length=MAX_LARGO_CLIENTE_ID
    ),
]
TextoMensaje = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_LARGO_MENSAJE)
]


class SolicitudPedido(BaseModel):
    """Cuerpo de `POST /v1/pedidos/interpretar`."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "examples": [
                {
                    "cliente_id": "C001",
                    "fecha_hora": "2026-11-23T15:20",
                    "mensajes": [
                        "Buenas, mándeme 2 fardos de kola 600, una caja de aceite",
                        "y 48 latas de energía max",
                    ],
                }
            ]
        },
    )

    cliente_id: ClienteId = Field(
        description="Identificador del cliente, p. ej. C001 (sin distinguir mayúsculas)."
    )
    fecha_hora: FechaHora
    mensajes: list[TextoMensaje] = Field(
        min_length=1,
        max_length=MAX_MENSAJES,
        description="Mensajes del cliente en orden (al menos uno, ninguno vacío).",
    )


class DetalleError(BaseModel):
    """Un problema concreto de la solicitud."""

    campo: str = Field(description="Campo con el problema, p. ej. 'mensajes[0]' o 'cuerpo'.")
    mensaje: str = Field(description="Explicación en español.")
    tipo: str = Field(description="Código del error de validación (de Pydantic).")


class RespuestaError(BaseModel):
    """Cuerpo de las respuestas de error (404, 422, 503, ...)."""

    error: str = Field(
        description=(
            "solicitud_invalida | cliente_no_encontrado | interprete_no_disponible | "
            "datos_no_disponibles | error_interno | ruta_no_encontrada | metodo_no_permitido"
        )
    )
    mensaje: str = Field(description="Explicación en español.")
    detalles: list[DetalleError] = Field(default_factory=list)


class RespuestaSalud(BaseModel):
    """Cuerpo de `GET /health`."""

    estado: Literal["ok", "degradado"]
    motivo: str | None = Field(default=None, description="Solo cuando estado = degradado.")
    dependencias: dict[str, str] = Field(
        default_factory=dict, description="Estado de cada dependencia (diagnóstico)."
    )


__all__ = [
    "MAX_LARGO_CLIENTE_ID",
    "MAX_LARGO_MENSAJE",
    "MAX_MENSAJES",
    "DetalleError",
    "RespuestaError",
    "RespuestaSalud",
    "SolicitudPedido",
]
