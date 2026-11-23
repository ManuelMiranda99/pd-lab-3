"""Prueba manual de resiliencia de la API frente a MLflow caído o colgado (`DECISIONES.md` D43).

Requiere la API ya levantada y apuntando a `--mlflow-uri`; el script maneja su propio servidor
de MLflow (lo levanta, lo mata, lo congela con SIGSTOP y lo reanuda) y mide en cada fase la
latencia de `POST /v1/pedidos/interpretar` y de `GET /health`, el tiempo hasta que `/health`
cambia de estado y si las trazas nuevas llegan a MLflow sin reiniciar la API.

    # 1) API (en otra terminal) apuntando al MLflow que manejará el script:
    MLFLOW_TRACKING_URI=http://127.0.0.1:5001 uv run uvicorn novapedidos.api.main:app --port 8001
    # 2) la prueba (bitácora en --salida):
    uv run python scripts/probar_resiliencia_mlflow.py --api http://127.0.0.1:8001 \\
        --mlflow-uri http://127.0.0.1:5001 --mlflow-dir /tmp/mlflow-np --salida resiliencia.txt

Fases: A) MLflow apagado desde el arranque · B) MLflow arriba · C) MLflow muerto (SIGTERM) ·
D) MLflow colgado (SIGSTOP: acepta conexiones pero no responde) · E) MLflow reanudado.
Con el intérprete LLM cada solicitud cuesta ~USD 0.0003 (5 por fase ≈ USD 0.008 en total).
"""

from __future__ import annotations

import argparse
import os
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

SOLICITUD = {
    "cliente_id": "C001",
    "fecha_hora": "2026-11-23T15:20",
    "mensajes": [
        "Buenas, mándeme 2 fardos de kola 600, una caja de aceite",
        "y 48 latas de energía max",
    ],
}


class Bitacora:
    def __init__(self, ruta: Path | None) -> None:
        self.lineas: list[str] = []
        self.ruta = ruta

    def __call__(self, texto: str = "") -> None:
        linea = f"[{datetime.now():%H:%M:%S}] {texto}" if texto else ""
        print(linea, flush=True)
        self.lineas.append(linea)

    def guardar(self) -> None:
        if self.ruta:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            self.ruta.write_text("\n".join(self.lineas) + "\n", encoding="utf-8")


class ServidorMlflow:
    """Un `mlflow server` propio (grupo de procesos aparte para poder matarlo o congelarlo)."""

    def __init__(self, uri: str, directorio: Path) -> None:
        url = urlparse(uri)
        self.host, self.puerto = url.hostname or "127.0.0.1", url.port or 5000
        self.directorio = directorio
        self.proceso: subprocess.Popen[bytes] | None = None

    def levantar(self) -> None:
        self.directorio.mkdir(parents=True, exist_ok=True)
        (self.directorio / "artefactos").mkdir(exist_ok=True)
        comando = [
            "mlflow",
            "server",
            "--host",
            self.host,
            "--port",
            str(self.puerto),
            "--backend-store-uri",
            f"sqlite:///{self.directorio / 'mlflow.db'}",
            "--default-artifact-root",
            str(self.directorio / "artefactos"),
        ]
        registro = (self.directorio / "servidor.log").open("ab")
        self.proceso = subprocess.Popen(  # noqa: S603
            comando,
            stdout=registro,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "MLFLOW_DISABLE_AGENT_HINT": "1"},
        )

    def senal(self, sig: int) -> None:
        if self.proceso is not None:
            os.killpg(self.proceso.pid, sig)

    def matar(self) -> None:
        if self.proceso is None:
            return
        self.senal(signal.SIGCONT)
        self.senal(signal.SIGTERM)
        try:
            self.proceso.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.senal(signal.SIGKILL)
            self.proceso.wait(timeout=5)
        self.proceso = None


def salud(cliente: httpx.Client) -> tuple[dict, float]:
    inicio = time.perf_counter()
    cuerpo = cliente.get("/health").json()
    return cuerpo, time.perf_counter() - inicio


def esperar_salud(
    cliente: httpx.Client, estado: str, limite_s: float, desde: float
) -> tuple[float | None, list[float]]:
    """Consulta `/health` cada 0.1 s hasta ver `estado`; devuelve (segundos desde `desde`,
    latencias de cada consulta)."""
    latencias = []
    while time.perf_counter() - desde < limite_s:
        cuerpo, lat = salud(cliente)
        latencias.append(lat)
        if cuerpo["estado"] == estado:
            return time.perf_counter() - desde, latencias
        time.sleep(0.1)
    return None, latencias


def rafaga(cliente: httpx.Client, n: int) -> tuple[list[float], list[int]]:
    latencias, codigos = [], []
    for _ in range(n):
        inicio = time.perf_counter()
        r = cliente.post("/v1/pedidos/interpretar", json=SOLICITUD)
        latencias.append(time.perf_counter() - inicio)
        codigos.append(r.status_code)
    return latencias, codigos


def describir(latencias: list[float]) -> str:
    if not latencias:
        return "sin datos"
    ordenadas = sorted(latencias)
    p95 = ordenadas[min(len(ordenadas) - 1, round(0.95 * (len(ordenadas) - 1)))]
    return (
        f"n={len(latencias)} p50 {statistics.median(latencias) * 1000:.0f} ms · p95 "
        f"{p95 * 1000:.0f} ms · máx {max(latencias) * 1000:.0f} ms"
    )


def contar_trazas(uri: str, experimento: str, desde_ms: int) -> int | None:
    """Trazas del experimento con timestamp ≥ `desde_ms` (None si MLflow no responde)."""
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "3")
    os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    try:
        cliente = mlflow.MlflowClient(uri)
        exp = cliente.get_experiment_by_name(experimento)
        if exp is None:
            return 0
        trazas = cliente.search_traces(
            locations=[exp.experiment_id],
            filter_string=f"trace.timestamp_ms >= {desde_ms}",
            max_results=500,
        )
        return len(trazas)
    except Exception:  # noqa: BLE001
        return None


def esperar_trazas(uri: str, experimento: str, desde_ms: int, minimo: int, limite_s: float):
    inicio = time.perf_counter()
    n = None
    while time.perf_counter() - inicio < limite_s:
        n = contar_trazas(uri, experimento, desde_ms)
        if n is not None and n >= minimo:
            return n, time.perf_counter() - inicio
        time.sleep(1)
    return n, None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--api", default="http://127.0.0.1:8001")
    p.add_argument("--mlflow-uri", default="http://127.0.0.1:5001")
    p.add_argument("--mlflow-dir", type=Path, required=True, help="backend sqlite del servidor")
    p.add_argument("--experimento", default="novapedidos-api")
    p.add_argument("-n", type=int, default=5, help="solicitudes por fase")
    p.add_argument("--salida", type=Path, help="bitácora de la prueba")
    p.add_argument("--dejar-arriba", action="store_true", help="no apagar MLflow al terminar")
    args = p.parse_args(argv)

    log = Bitacora(args.salida)
    servidor = ServidorMlflow(args.mlflow_uri, args.mlflow_dir)
    api = httpx.Client(base_url=args.api, timeout=60)
    log(f"Prueba de resiliencia · API {args.api} · MLflow {args.mlflow_uri} · {args.n} por fase")
    try:
        cuerpo, _ = salud(api)
        log(f"/health inicial: {cuerpo}")
        if cuerpo.get("dependencias", {}).get("mlflow") == "ok":
            log("ERROR: ya hay un MLflow en esa URI; apáguelo (el script maneja el suyo).")
            return 2

        log()
        log("== A) MLflow apagado (la API arrancó sin él)")
        lat, cod = rafaga(api, args.n)
        cuerpo, lat_h = salud(api)
        log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
        log(f"/health: {cuerpo['estado']} ({lat_h * 1000:.0f} ms) · {cuerpo.get('motivo', '')}")

        log()
        log("== B) MLflow arriba")
        inicio = time.perf_counter()
        servidor.levantar()
        t_ok, _ = esperar_salud(api, "ok", 60, inicio)
        log(f"/health → ok {t_ok:.1f} s después de lanzar el servidor")
        marca_b = int(time.time() * 1000)
        time.sleep(6)  # el registrador reintenta la conexión cada 5 s
        lat, cod = rafaga(api, args.n)
        log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
        n, t = esperar_trazas(args.mlflow_uri, args.experimento, marca_b, args.n, 30)
        log(f"trazas nuevas en MLflow: {n} (esperadas ≥ {args.n}) · visibles en {t} s")

        log()
        log("== C) MLflow muerto (SIGTERM)")
        servidor.matar()
        inicio = time.perf_counter()
        t_deg, lats_h = esperar_salud(api, "degradado", 30, inicio)
        log(f"/health → degradado {t_deg:.2f} s después de morir · {describir(lats_h)}")
        lat, cod = rafaga(api, args.n)
        cuerpo, lat_h = salud(api)
        log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
        log(f"/health: {cuerpo['estado']} ({lat_h * 1000:.0f} ms) · {cuerpo.get('motivo', '')}")

        log()
        log("== D) MLflow colgado (SIGSTOP: acepta la conexión TCP pero no responde)")
        servidor.levantar()
        esperar_salud(api, "ok", 60, time.perf_counter())
        time.sleep(1)
        servidor.senal(signal.SIGSTOP)
        inicio = time.perf_counter()
        t_deg, lats_h = esperar_salud(api, "degradado", 30, inicio)
        log(f"/health → degradado {t_deg:.2f} s después de congelarlo · {describir(lats_h)}")
        lat, cod = rafaga(api, args.n)
        cuerpo, lat_h = salud(api)
        log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
        log(f"/health: {cuerpo['estado']} ({lat_h * 1000:.0f} ms) · {cuerpo.get('motivo', '')}")

        log()
        log("== E) MLflow reanudado (SIGCONT), sin reiniciar la API")
        servidor.senal(signal.SIGCONT)
        inicio = time.perf_counter()
        t_ok, _ = esperar_salud(api, "ok", 30, inicio)
        log(f"/health → ok {t_ok:.2f} s después de reanudarlo")
        marca_e = int(time.time() * 1000)
        lat, cod = rafaga(api, args.n)
        log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
        n, t = esperar_trazas(args.mlflow_uri, args.experimento, marca_e, args.n, 60)
        log(f"trazas nuevas en MLflow: {n} (esperadas ≥ {args.n}) · visibles en {t} s")
        cuerpo, lat_h = salud(api)
        log(f"/health final: {cuerpo['estado']} ({lat_h * 1000:.0f} ms) · {cuerpo}")
    finally:
        if not args.dejar_arriba:
            servidor.matar()
        api.close()
        log.guardar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
