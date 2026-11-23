"""Prueba de resiliencia con Docker Compose: la API sigue respondiendo con MLflow caído o colgado.

Requiere el stack ya levantado (`docker compose up -d`). El script maneja el servicio `mlflow`
con `docker compose stop/start` (caído) y `pause/unpause` (colgado: el contenedor acepta la
conexión TCP pero no responde) y mide en cada fase la latencia de `POST
/v1/pedidos/interpretar` y de `GET /health`, el tiempo hasta que `/health` cambia de estado y si
las trazas nuevas llegan a MLflow sin reiniciar la API. Al final envía solicitudes inválidas
(422) y de un cliente inexistente (404).

    API_PUERTO=8002 MLFLOW_PUERTO=5002 docker compose -p novapedidos-prueba up -d --build
    uv run python scripts/probar_resiliencia_docker.py --proyecto novapedidos-prueba \\
        --api http://127.0.0.1:8002 --mlflow-uri http://127.0.0.1:5002 --salida resiliencia.txt

Fases: A) MLflow arriba · B) `stop mlflow` · C) `start mlflow` · D) `pause mlflow` ·
E) `unpause mlflow` · F) 422/404. Con el intérprete LLM cada solicitud cuesta ~USD 0.0003
(`-n 5` → 25 solicitudes ≈ USD 0.008).
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from probar_resiliencia_mlflow import (  # noqa: E402
    Bitacora,
    describir,
    esperar_salud,
    esperar_trazas,
    rafaga,
    salud,
)

INVALIDAS: list[tuple[str, object]] = [
    ("cuerpo vacío", {}),
    ("sin mensajes", {"cliente_id": "C001", "fecha_hora": "2026-11-23T15:20"}),
    (
        "fecha_hora inválida",
        {"cliente_id": "C001", "fecha_hora": "ayer", "mensajes": ["2 fardos de kola"]},
    ),
    (
        "mensajes no es lista",
        {"cliente_id": "C001", "fecha_hora": "2026-11-23T15:20", "mensajes": "hola"},
    ),
    ("JSON malformado", "{no es json"),
]
INEXISTENTE = {
    "cliente_id": "C999",
    "fecha_hora": "2026-11-23T15:20",
    "mensajes": ["2 fardos de kola 600"],
}


def compose(proyecto: str, *args: str) -> float:
    """Ejecuta `docker compose -p <proyecto> <args>` y devuelve cuánto tardó (s)."""
    inicio = time.perf_counter()
    subprocess.run(  # noqa: S603
        ["docker", "compose", "-p", proyecto, *args],  # noqa: S607
        check=True,
        capture_output=True,
    )
    return time.perf_counter() - inicio


def esperar_trazas_activas(cliente: httpx.Client, limite_s: float) -> float | None:
    inicio = time.perf_counter()
    while time.perf_counter() - inicio < limite_s:
        cuerpo, _ = salud(cliente)
        if cuerpo.get("dependencias", {}).get("trazas") == "activas":
            return time.perf_counter() - inicio
        time.sleep(0.5)
    return None


def fase_caida(log: Bitacora, api: httpx.Client, n: int, que: str) -> None:
    inicio = time.perf_counter()
    t_deg, lats_h = esperar_salud(api, "degradado", 30, inicio)
    t_txt = f"{t_deg:.2f} s" if t_deg is not None else "NO (30 s)"
    log(f"/health → degradado {t_txt} después de {que} · {describir(lats_h)}")
    lat, cod = rafaga(api, n)
    cuerpo, lat_h = salud(api)
    log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
    log(f"/health: {cuerpo['estado']} ({lat_h * 1000:.0f} ms) · {cuerpo.get('motivo', '')}")


def fase_vuelta(
    log: Bitacora, api: httpx.Client, args: argparse.Namespace, que: str, limite_s: float
) -> None:
    inicio = time.perf_counter()
    t_ok, _ = esperar_salud(api, "ok", limite_s, inicio)
    t_txt = f"{t_ok:.2f} s" if t_ok is not None else f"NO ({limite_s:.0f} s)"
    log(f"/health → ok {t_txt} después de {que}")
    marca = int(time.time() * 1000)
    lat, cod = rafaga(api, args.n)
    log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
    n, t = esperar_trazas(args.mlflow_uri, args.experimento, marca, args.n, 60)
    t_txt = f"{t:.1f} s" if t is not None else "no llegaron en 60 s"
    log(f"trazas nuevas en MLflow: {n} (esperadas ≥ {args.n}) · visibles en {t_txt}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--proyecto", default="novapedidos", help="nombre del proyecto de compose")
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--mlflow-uri", default="http://127.0.0.1:5000", help="MLflow desde el host")
    p.add_argument("--experimento", default="novapedidos-api")
    p.add_argument("-n", type=int, default=5, help="solicitudes por fase")
    p.add_argument("--salida", type=Path, help="bitácora de la prueba")
    args = p.parse_args(argv)

    log = Bitacora(args.salida)
    api = httpx.Client(base_url=args.api, timeout=60)
    log(
        f"Resiliencia con Docker Compose · proyecto {args.proyecto} · API {args.api} · "
        f"MLflow {args.mlflow_uri} · {args.n} POST por fase"
    )
    try:
        cuerpo, _ = salud(api)
        log(f"/health inicial: {cuerpo}")

        log()
        log("== A) MLflow arriba")
        t = esperar_trazas_activas(api, 30)
        log(f"trazas activas (registrador conectado) tras {t} s de espera")
        marca = int(time.time() * 1000)
        lat, cod = rafaga(api, args.n)
        log(f"POST: {describir(lat)} · códigos {sorted(set(cod))}")
        n, t = esperar_trazas(args.mlflow_uri, args.experimento, marca, args.n, 60)
        log(f"trazas nuevas en MLflow: {n} (esperadas ≥ {args.n}) · visibles en {t} s")

        log()
        log("== B) docker compose stop mlflow (caído)")
        log(f"stop tardó {compose(args.proyecto, 'stop', 'mlflow'):.1f} s")
        fase_caida(log, api, args.n, "detenerlo")

        log()
        log("== C) docker compose start mlflow, sin reiniciar la API")
        log(f"start tardó {compose(args.proyecto, 'start', 'mlflow'):.1f} s")
        fase_vuelta(log, api, args, "iniciarlo", 90)

        log()
        log("== D) docker compose pause mlflow (colgado: acepta TCP y no responde)")
        log(f"pause tardó {compose(args.proyecto, 'pause', 'mlflow'):.1f} s")
        fase_caida(log, api, args.n, "pausarlo")

        log()
        log("== E) docker compose unpause mlflow, sin reiniciar la API")
        log(f"unpause tardó {compose(args.proyecto, 'unpause', 'mlflow'):.1f} s")
        fase_vuelta(log, api, args, "reanudarlo", 30)

        log()
        log("== F) Solicitudes inválidas y cliente inexistente")
        for nombre, cuerpo_inv in INVALIDAS:
            if isinstance(cuerpo_inv, str):
                r = api.post(
                    "/v1/pedidos/interpretar",
                    content=cuerpo_inv,
                    headers={"Content-Type": "application/json"},
                )
            else:
                r = api.post("/v1/pedidos/interpretar", json=cuerpo_inv)
            log(f"{nombre}: {r.status_code} · {r.text[:160]}")
        r = api.post("/v1/pedidos/interpretar", json=INEXISTENTE)
        log(f"cliente inexistente C999: {r.status_code} · {r.text[:160]}")

        cuerpo, lat_h = salud(api)
        log()
        log(f"/health final: {cuerpo['estado']} ({lat_h * 1000:.0f} ms) · {cuerpo}")
    finally:
        # Nunca dejar MLflow pausado o detenido.
        for accion in ("unpause", "start"):
            with contextlib.suppress(subprocess.CalledProcessError):
                compose(args.proyecto, accion, "mlflow")
        api.close()
        log.guardar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
