"""Chequeo de dependencias para `GET /health` (`DECISIONES.md` D24).

MLflow se consulta con `GET <MLFLOW_TRACKING_URI>/health`, timeout de 1 s y caché de 1 s
(D24, ajustado en D43), para que `/health` responda en ≤ 1 s aunque MLflow esté colgado, refleje
una caída en ~1 s y no se golpee MLflow más de una vez por segundo.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

TIMEOUT_MLFLOW_S = 1.0
CACHE_MLFLOW_S = 1.0


@dataclass(frozen=True, slots=True)
class EstadoDependencia:
    """Resultado del chequeo de una dependencia."""

    disponible: bool
    detalle: str  # "ok", "desactivado", "local" o el motivo de la falla


def _ping_http(url: str, timeout: float) -> EstadoDependencia:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as respuesta:  # noqa: S310
            codigo = respuesta.status
    except urllib.error.HTTPError as exc:
        return EstadoDependencia(False, f"MLflow respondió HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        razon = getattr(exc, "reason", exc)
        return EstadoDependencia(False, f"MLflow no responde ({razon})")
    if 200 <= codigo < 300:
        return EstadoDependencia(True, "ok")
    return EstadoDependencia(False, f"MLflow respondió HTTP {codigo}")


class MonitorMlflow:
    """Chequea MLflow con timeout corto y guarda el resultado unos segundos.

    - URI vacía → `desactivado` (no es una dependencia; no degrada).
    - URI no HTTP (`file:`, `sqlite:`, ruta local) → `local` (no hay servidor que consultar).
    - URI HTTP(S) → `GET <uri>/health` con `timeout` segundos.
    """

    def __init__(
        self,
        tracking_uri: str,
        *,
        timeout: float = TIMEOUT_MLFLOW_S,
        ttl: float = CACHE_MLFLOW_S,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tracking_uri = tracking_uri.strip()
        self.timeout = timeout
        self.ttl = ttl
        self._reloj = reloj
        self._candado = threading.Lock()
        self._ultimo: tuple[float, EstadoDependencia] | None = None

    def _consultar(self) -> EstadoDependencia:
        if not self.tracking_uri:
            return EstadoDependencia(True, "desactivado")
        esquema = urlparse(self.tracking_uri).scheme.lower()
        if esquema not in ("http", "https"):
            return EstadoDependencia(True, "local")
        return _ping_http(self.tracking_uri.rstrip("/") + "/health", self.timeout)

    def estado(self) -> EstadoDependencia:
        with self._candado:
            ahora = self._reloj()
            if self._ultimo is not None and ahora - self._ultimo[0] < self.ttl:
                return self._ultimo[1]
            resultado = self._consultar()
            self._ultimo = (self._reloj(), resultado)
            return resultado


__all__ = ["CACHE_MLFLOW_S", "TIMEOUT_MLFLOW_S", "EstadoDependencia", "MonitorMlflow"]
