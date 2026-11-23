"""Fecha de entrega y parser de `fecha_hora` (`politicas.md` §7, `DECISIONES.md` D03).

Nunca usa el reloj del sistema: todo parte de la `fecha_hora` de la solicitud.

- Recibido a las 14:00 o después ⇒ se considera recibido al día siguiente.
- Entrega = primer día de visita **estrictamente posterior** a la fecha de recepción.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta

#: Formato exacto de `fecha_hora` en el contrato (hora local de Guatemala, sin zona).
FORMATO_FECHA_HORA = "%Y-%m-%dT%H:%M"
#: Desde esta hora (inclusive) el pedido cuenta como recibido al día siguiente.
HORA_CORTE = time(14, 0)

_PATRON_FECHA_HORA = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


class ErrorFechaHora(ValueError):
    """`fecha_hora` no tiene el formato `AAAA-MM-DDTHH:MM` o no es una fecha válida."""


def parsear_fecha_hora(texto: str) -> datetime:
    """Convierte `"AAAA-MM-DDTHH:MM"` en `datetime` (sin zona). Estricto: sin segundos, sin
    zona, sin espacios y con ceros a la izquierda. Lanza `ErrorFechaHora` con mensaje claro."""
    if not isinstance(texto, str) or not _PATRON_FECHA_HORA.fullmatch(texto):
        raise ErrorFechaHora(
            f"fecha_hora debe tener el formato AAAA-MM-DDTHH:MM (p. ej. 2026-11-28T14:53): "
            f"{texto!r}"
        )
    try:
        return datetime.strptime(texto, FORMATO_FECHA_HORA)
    except ValueError as exc:
        raise ErrorFechaHora(
            f"fecha_hora no es una fecha y hora válidas (formato AAAA-MM-DDTHH:MM): {texto!r}"
        ) from exc


def fecha_efectiva_recepcion(fecha_hora: datetime) -> date:
    """Fecha de recepción: el mismo día antes de las 14:00; el día siguiente desde las 14:00."""
    dia = fecha_hora.date()
    return dia + timedelta(days=1) if fecha_hora.time() >= HORA_CORTE else dia


def fecha_entrega(fecha_hora: datetime, dias_visita: Iterable[int]) -> date:
    """Primer día de `dias_visita` (índices de `date.weekday()`, 0 = lunes) estrictamente
    posterior a la fecha efectiva de recepción. Siempre cae entre 1 y 7 días después."""
    dias = set(dias_visita)
    if not dias:
        raise ValueError("El cliente no tiene días de visita")
    if not dias <= set(range(7)):
        raise ValueError(f"Días de visita inválidos (deben ser 0–6): {sorted(dias)}")
    recepcion = fecha_efectiva_recepcion(fecha_hora)
    for salto in range(1, 8):
        candidata = recepcion + timedelta(days=salto)
        if candidata.weekday() in dias:
            return candidata
    raise AssertionError("inalcanzable: siempre hay una visita en 7 días")  # pragma: no cover
