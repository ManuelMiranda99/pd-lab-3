"""Tramos (*spans*) de MLflow opcionales y a prueba de fallos (`DECISIONES.md` D42).

El intérprete y la API marcan sus pasos con `tramo(...)`. Un tramo solo se crea si ya hay una
traza de MLflow activa en el hilo (la abre el registrador de la API, D42, o
`mlflow.genai.evaluate`); en cualquier otro caso (pruebas, evaluación sin `--mlflow`, MLflow
sin conectar) `tramo` no hace nada y ni siquiera importa `mlflow`.

Todo error de MLflow dentro de un tramo se registra en el log y se ignora: las trazas nunca
cambian la respuesta (regla 6 de `CLAUDE.md`). Las excepciones del código envuelto sí se
propagan sin cambios (y el tramo queda marcado con error).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger("novapedidos.trazado")


def _mlflow() -> Any | None:
    """El módulo `mlflow` si ya está importado (no lo importa: es lento y opcional)."""
    return sys.modules.get("mlflow")


def hay_traza_activa() -> bool:
    """Indica si hay un tramo de MLflow activo en el contexto actual (sin red)."""
    mlflow = _mlflow()
    if mlflow is None:
        return False
    try:
        return mlflow.get_current_active_span() is not None
    except Exception:  # noqa: BLE001
        return False


class Tramo:
    """Envoltura de un `mlflow.entities.LiveSpan` cuyos métodos nunca lanzan."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def salidas(self, valor: Any) -> None:
        try:
            self._span.set_outputs(valor)
        except Exception:  # noqa: BLE001
            logger.debug("No se pudieron fijar las salidas del tramo", exc_info=True)

    def atributos(self, valores: Mapping[str, Any]) -> None:
        try:
            self._span.set_attributes({k: v for k, v in valores.items() if v is not None})
        except Exception:  # noqa: BLE001
            logger.debug("No se pudieron fijar los atributos del tramo", exc_info=True)


@contextmanager
def tramo(
    nombre: str, tipo: str = "UNKNOWN", entradas: Any = None, *, raiz: bool = False
) -> Iterator[Tramo | None]:
    """Abre un tramo hijo del tramo activo (o una traza nueva si `raiz=True`).

    `entradas` puede ser un invocable sin argumentos: solo se evalúa si el tramo se abre (así
    no se serializa nada cuando no hay traza). Devuelve `None` (y no hace nada) si no hay traza
    activa y no se pidió una raíz, o si MLflow falla al abrirlo.
    """
    mlflow = _mlflow()
    if mlflow is None or (not raiz and not hay_traza_activa()):
        yield None
        return
    try:
        administrador = mlflow.start_span(name=nombre, span_type=tipo)
        span = administrador.__enter__()
    except Exception:  # noqa: BLE001
        logger.warning("No se pudo abrir el tramo %r de MLflow", nombre, exc_info=True)
        yield None
        return
    try:
        if callable(entradas):
            entradas = entradas()
        if entradas is not None:
            span.set_inputs(entradas)
    except Exception:  # noqa: BLE001
        logger.debug("No se pudieron fijar las entradas del tramo %r", nombre, exc_info=True)
    info: tuple[Any, Any, Any] = (None, None, None)
    try:
        yield Tramo(span)
    except BaseException:
        info = sys.exc_info()
        raise
    finally:
        try:
            administrador.__exit__(*info)
        except Exception:  # noqa: BLE001
            logger.warning("No se pudo cerrar el tramo %r de MLflow", nombre, exc_info=True)


def anotar_traza(etiquetas: Mapping[str, Any]) -> None:
    """Agrega etiquetas (texto) a la traza activa, si hay una. Nunca lanza."""
    if not hay_traza_activa():
        return
    mlflow = _mlflow()
    try:
        mlflow.update_current_trace(  # type: ignore[union-attr]
            tags={k: str(v) for k, v in etiquetas.items() if v is not None}
        )
    except Exception:  # noqa: BLE001
        logger.debug("No se pudo etiquetar la traza", exc_info=True)


__all__ = ["Tramo", "anotar_traza", "hay_traza_activa", "tramo"]
