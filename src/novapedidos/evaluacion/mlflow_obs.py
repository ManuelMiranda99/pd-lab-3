"""Registro de una corrida de evaluación en MLflow (`eval/correr.py --mlflow`, D44).

`ObservadorMlflow.al_final(resumen, resultados, directorio)` crea **un run** por corrida en el
experimento de evaluación (`MLFLOW_EXPERIMENTO_EVAL`, por defecto `novapedidos-evaluacion`):

- parámetros: modelo, variante, ejecutor, resolución post-LLM, conjuntos, concurrencia,
  umbral de latencia, número de casos, commit de git (y si había cambios sin commit);
- métricas: exactitud global, por conjunto (`exactitud.conjunto.<archivo>`), por tipo
  (`exactitud.tipo.<tipo>`), tasa por calificador (`calificador.<nombre>`), latencia
  p50/p95/máx/media (punta a punta y del LLM), tokens, costo total y por caso, duración;
- artefactos: `resultados.jsonl`, `resumen.json`, `reporte.txt` y las tablas `casos.json`
  (una fila por caso) y `fallos.json` (`mlflow.log_table`);
- **evaluación por caso** con `mlflow.genai.evaluate`: los calificadores de
  `novapedidos.evaluacion` como *scorers* sobre `inputs` (la solicitud), `outputs` (la
  respuesta ya obtenida) y `expectations` (el `esperado`). No vuelve a llamar al modelo
  (costo cero): la UI muestra cada caso con su traza y sus calificaciones.

Si MLflow falla a mitad del registro, la corrida local ya quedó guardada en
`eval/resultados/<marca>/` y el error se informa sin detener nada.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from novapedidos.datos import Datos
from novapedidos.evaluacion.corrida import ORDEN_CALIFICADORES, calificar_ejecucion
from novapedidos.evaluacion.ejecutores import Ejecucion

logger = logging.getLogger("novapedidos.evaluacion.mlflow")

#: Calificadores que se registran como *scorers* de `mlflow.genai.evaluate` (todos menos la
#: latencia, que no está en la respuesta: va como métrica del run).
SCORERS = tuple(n for n in ORDEN_CALIFICADORES if n != "latencia")

#: Timeout/reintentos HTTP de MLflow para la evaluación (más holgados que en la API, pero
#: acotados: por defecto MLflow reintenta 7 veces con 120 s de timeout).
ENTORNO_MLFLOW_EVAL: Mapping[str, str] = {
    "MLFLOW_HTTP_REQUEST_TIMEOUT": "15",
    "MLFLOW_HTTP_REQUEST_MAX_RETRIES": "2",
    "MLFLOW_DISABLE_AGENT_HINT": "1",
}


def _nombre_metrica(texto: str) -> str:
    """MLflow solo acepta letras, números, `_ - . / espacio` en los nombres de métricas."""
    limpio = "".join(c if c.isalnum() or c in "_-./ " else "_" for c in texto)
    return limpio.strip() or "_"


def commit_git(raiz: Path | None = None) -> tuple[str | None, bool | None]:
    """(commit corto, hay cambios sin commit) o (None, None) si no hay git."""
    try:
        commit = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=raiz,
            timeout=5,
        ).stdout.strip()
        sucio = subprocess.run(  # noqa: S603
            ["git", "status", "--porcelain", "--untracked-files=no"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=raiz,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, None
    return commit or None, bool(sucio)


def metricas_de_resumen(resumen: Mapping[str, Any]) -> dict[str, float]:
    """Aplana `resumen.json` en métricas de MLflow (nombres estables entre corridas)."""
    m: dict[str, float] = {
        "exactitud": resumen["exactitud"],
        "exactos": resumen["exactos"],
        "casos": resumen["casos"],
        "etiquetados": resumen["etiquetados"],
        "errores": resumen["errores"],
        "duracion_s": resumen.get("duracion_s", 0.0),
    }
    for origen, g in resumen.get("por_origen", {}).items():
        m[_nombre_metrica(f"exactitud.conjunto.{Path(origen).stem}")] = g["exactitud"]
    for tipo, g in resumen.get("por_tipo", {}).items():
        m[_nombre_metrica(f"exactitud.tipo.{tipo}")] = g["exactitud"]
    for nombre, c in resumen.get("por_calificador", {}).items():
        m[_nombre_metrica(f"calificador.{nombre}")] = c["tasa"]
    for clave, prefijo in (("latencia_s", "latencia"), ("latencia_llm_s", "latencia_llm")):
        lat = resumen.get(clave) or {}
        if lat.get("n"):
            for estadistico in ("p50", "p95", "max", "media"):
                if lat.get(estadistico) is not None:
                    m[f"{prefijo}_{estadistico}_s"] = lat[estadistico]
    tokens = resumen.get("tokens") or {}
    for clave in ("entrada", "salida", "cache"):
        m[f"tokens_{clave}"] = tokens.get(clave, 0)
    costo = resumen.get("costo_usd") or {}
    if costo.get("casos_con_costo"):
        m["costo_total_usd"] = costo["total"]
        m["costo_por_caso_usd"] = costo["por_caso"]
    return {k: float(v) for k, v in m.items() if v is not None}


def filas_casos(resultados: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Una fila por caso para `mlflow.log_table` (calificaciones, latencia, costo, estados)."""
    filas = []
    for r in resultados:
        meta = r.get("meta_llm") or {}
        obtenido = r.get("obtenido") or {}
        esperado = r.get("esperado") or {}
        fila: dict[str, Any] = {
            "id": r["id"],
            "tipo": r["tipo"],
            "conjunto": Path(r.get("origen") or "").stem,
            "exacto": r.get("exacto"),
            "error": r.get("error"),
            "estado_esperado": esperado.get("estado"),
            "estado_obtenido": obtenido.get("estado"),
            "total_esperado": esperado.get("total"),
            "total_obtenido": obtenido.get("total"),
            "latencia_s": r.get("latencia_s"),
            "latencia_llm_s": meta.get("latencia_s"),
            "costo_usd": meta.get("costo_usd"),
            "mensajes": " | ".join(r["entrada"].get("mensajes", [])),
        }
        for nombre in ORDEN_CALIFICADORES:
            c = r["calificaciones"].get(nombre)
            fila[nombre] = c["valor"] if c else None
        fallas = [
            f"{n}: {c['detalle']}"
            for n, c in r["calificaciones"].items()
            if c["valor"] < 1.0 and n != "caso_exacto"
        ]
        fila["detalle_fallas"] = " | ".join(fallas)
        filas.append(fila)
    return filas


def datos_genai(resultados: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Filas para `mlflow.genai.evaluate` con salidas ya calculadas (sin `predict_fn`)."""
    filas = []
    for r in resultados:
        salida = r.get("obtenido")
        if salida is None:
            salida = {"error": r.get("error") or "sin respuesta"}
        filas.append(
            {
                "inputs": dict(r["entrada"]),
                "outputs": salida,
                "expectations": {"esperado": r.get("esperado")},
                "tags": {
                    "caso_id": r["id"],
                    "tipo": r["tipo"],
                    "conjunto": Path(r.get("origen") or "").stem,
                },
            }
        )
    return filas


def crear_scorers(datos: Datos, umbral_latencia_s: float = 5.0) -> list[Any]:
    """Los calificadores de la evaluación local como *scorers* de MLflow (mismo código, D33)."""
    from mlflow.entities import Feedback
    from mlflow.genai.scorers import scorer

    def calificar(outputs: Any, expectations: Any) -> dict[str, Any]:
        esperado = (expectations or {}).get("esperado")
        if isinstance(outputs, dict) and "error" in outputs and "estado" not in outputs:
            ejecucion = Ejecucion(error=str(outputs["error"]))
        else:
            ejecucion = Ejecucion(obtenido=outputs)
        return calificar_ejecucion({"esperado": esperado}, ejecucion, datos, umbral_latencia_s)

    def fabricar(nombre: str) -> Callable[..., Any]:
        def funcion(outputs: Any, expectations: Any = None) -> Any:
            calificacion = calificar(outputs, expectations).get(nombre)
            if calificacion is None:
                return None  # p. ej. caso sin `esperado`
            return Feedback(value=calificacion.valor >= 1.0, rationale=calificacion.detalle)

        return scorer(funcion, name=nombre)

    return [fabricar(n) for n in SCORERS]


class ObservadorMlflow:
    """Observador de `correr_evaluacion` que registra la corrida en MLflow (ver módulo).

    - `tracking_uri`, `experimento`: dónde registrar.
    - `datos`: catálogo (lo necesitan los scorers de integridad y del mensaje al cliente).
    - `parametros`: parámetros extra del run (variante, ejecutor, conjuntos…).
    - `genai`: además, `mlflow.genai.evaluate` con los calificadores como scorers.
    """

    def __init__(
        self,
        tracking_uri: str,
        experimento: str,
        datos: Datos,
        *,
        parametros: Mapping[str, Any] | None = None,
        genai: bool = True,
        etiquetas: Mapping[str, str] | None = None,
    ) -> None:
        for clave, valor in ENTORNO_MLFLOW_EVAL.items():
            os.environ.setdefault(clave, valor)
        self.tracking_uri = tracking_uri
        self.experimento = experimento
        self.datos = datos
        self.parametros = dict(parametros or {})
        self.genai = genai
        self.etiquetas = dict(etiquetas or {})
        self.run_id: str | None = None
        self.experiment_id: str | None = None
        self.error_genai: str | None = None

    def verificar(self) -> None:
        """Comprueba que MLflow responde y crea el experimento (lanza si no se puede)."""
        import mlflow

        mlflow.set_tracking_uri(self.tracking_uri)
        experimento = mlflow.set_experiment(self.experimento)
        self.experiment_id = experimento.experiment_id

    def al_final(
        self,
        resumen: dict[str, Any],
        resultados: list[dict[str, Any]],
        directorio: Path | None,
    ) -> None:
        import mlflow

        mlflow.set_tracking_uri(self.tracking_uri)
        experimento = mlflow.set_experiment(self.experimento)
        self.experiment_id = experimento.experiment_id
        commit, sucio = commit_git()
        archivos = [Path(a).stem for a in resumen.get("archivos", [])]
        parametros = {
            "modelo": resumen.get("modelo") or "(API por HTTP)",
            "descripcion": resumen.get("descripcion", ""),
            "resolucion_post_llm": resumen.get("resolucion_post_llm"),
            "conjuntos": ",".join(archivos),
            "concurrencia": resumen.get("concurrencia"),
            "umbral_latencia_s": resumen.get("umbral_latencia_s"),
            "casos": resumen.get("casos"),
            "marca": resumen.get("marca"),
            "git_commit": commit or "desconocido",
            "git_cambios_sin_commit": sucio,
            **self.parametros,
        }
        etiquetas = {
            "tipo_run": "evaluacion",
            "modelo": str(parametros["modelo"]),
            "variante": str(parametros.get("variante", "base")),
            **self.etiquetas,
        }
        with mlflow.start_run(run_name=resumen.get("marca"), tags=etiquetas) as run:
            self.run_id = run.info.run_id
            mlflow.log_params({k: str(v)[:500] for k, v in parametros.items()})
            mlflow.log_metrics(metricas_de_resumen(resumen))
            if directorio is not None:
                for nombre in ("resultados.jsonl", "resumen.json", "reporte.txt"):
                    ruta = Path(directorio) / nombre
                    if ruta.exists():
                        mlflow.log_artifact(str(ruta))
            filas = filas_casos(resultados)
            mlflow.log_table(_columnas(filas), artifact_file="casos.json")
            fallos = [f for f in filas if f["exacto"] is False or f["error"]]
            if fallos:
                mlflow.log_table(_columnas(fallos), artifact_file="fallos.json")
            if self.genai:
                try:
                    mlflow.genai.evaluate(
                        data=datos_genai(resultados),
                        scorers=crear_scorers(
                            self.datos, float(resumen.get("umbral_latencia_s") or 5.0)
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - el run ya tiene métricas y tablas
                    self.error_genai = f"{type(exc).__name__}: {exc}"
                    logger.warning("mlflow.genai.evaluate falló: %s", self.error_genai)
                    mlflow.set_tag("error_genai", self.error_genai[:500])

    def url_run(self) -> str | None:
        if not (self.run_id and self.experiment_id and self.tracking_uri.startswith("http")):
            return None
        base = self.tracking_uri.rstrip("/")
        return f"{base}/#/experiments/{self.experiment_id}/runs/{self.run_id}"


def _columnas(filas: list[dict[str, Any]]) -> dict[str, list[Any]]:
    claves = list(filas[0]) if filas else []
    return {k: [f.get(k) for f in filas] for k in claves}


__all__ = [
    "ENTORNO_MLFLOW_EVAL",
    "SCORERS",
    "ObservadorMlflow",
    "commit_git",
    "crear_scorers",
    "datos_genai",
    "filas_casos",
    "metricas_de_resumen",
]
