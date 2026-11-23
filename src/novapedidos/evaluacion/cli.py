"""Línea de comandos de la evaluación local (`eval/correr.py` la invoca).

Ejemplos (desde la raíz del repo):

    uv run python eval/correr.py                                   # dev, en proceso, 4 hilos
    uv run python eval/correr.py ejemplos/mensajes_dev.jsonl eval/casos_variantes.jsonl
    uv run python eval/correr.py --url http://127.0.0.1:8001       # contra la API (o Docker)
    uv run python eval/correr.py --modelo openai/gpt-4.1-mini --marca mini_dev
    uv run python eval/correr.py --ids D003 D082 --concurrencia 1
    uv run python eval/correr.py --tipos ambiguo correccion --sin-resolucion

Guarda `eval/resultados/<marca>/{resultados.jsonl, resumen.json, reporte.txt}` e imprime el
reporte. Nunca imprime la clave de OpenRouter. Código de salida 0 si la corrida terminó (aunque
haya fallos), 2 si falta configuración.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from novapedidos.config import obtener_configuracion
from novapedidos.datos import cargar_datos
from novapedidos.evaluacion.casos import cargar_casos
from novapedidos.evaluacion.corrida import correr_evaluacion, guardar_resultados
from novapedidos.evaluacion.ejecutores import EjecutorHTTP, EjecutorLocal
from novapedidos.llm.interfaz import ErrorInterprete

CASOS_DEV = Path("ejemplos/mensajes_dev.jsonl")
DIR_RESULTADOS = Path("eval/resultados")


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eval/correr.py",
        description="Evalúa el servicio de pedidos contra uno o varios .jsonl con `esperado`.",
    )
    p.add_argument(
        "archivos", nargs="*", type=Path, default=[CASOS_DEV], help="casos .jsonl (por defecto dev)"
    )
    p.add_argument("--url", help="evaluar la API por HTTP (p. ej. http://127.0.0.1:8001)")
    p.add_argument("--modelo", help="modelo de OpenRouter (solo en proceso; por defecto .env)")
    p.add_argument(
        "--sin-resolucion", action="store_true", help="sin la resolución post-LLM (en proceso)"
    )
    p.add_argument("--concurrencia", type=int, default=4, help="casos en paralelo (def. 4)")
    p.add_argument("--ids", nargs="+", help="solo estos ids")
    p.add_argument("--tipos", nargs="+", help="solo estos tipos")
    p.add_argument("--limite", type=int, help="como máximo N casos (los primeros)")
    p.add_argument("--marca", help="nombre de la carpeta de resultados (def. fecha_modelo)")
    p.add_argument("--dir-resultados", type=Path, default=DIR_RESULTADOS)
    p.add_argument("--datos-dir", type=Path, help="carpeta de los parquet (def. DATOS_DIR)")
    p.add_argument(
        "--umbral-latencia", type=float, default=5.0, help="segundos para el calificador latencia"
    )
    p.add_argument("--timeout", type=float, default=60.0, help="timeout HTTP por caso (s)")
    p.add_argument("--silencioso", action="store_true", help="sin progreso por caso")
    return p


def seleccionar(casos: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.ids:
        pedidos = set(args.ids)
        casos = [c for c in casos if c["id"] in pedidos]
    if args.tipos:
        tipos = set(args.tipos)
        casos = [c for c in casos if c["tipo"] in tipos]
    if args.limite:
        casos = casos[: args.limite]
    return casos


def marca_por_defecto(etiqueta: str) -> str:
    limpia = re.sub(r"[^A-Za-z0-9._-]+", "-", etiqueta).strip("-")
    return f"{datetime.now():%Y%m%d-%H%M%S}_{limpia}"


def main(argv: list[str] | None = None, observadores: tuple[Any, ...] = ()) -> int:
    args = construir_parser().parse_args(argv)
    config = obtener_configuracion()
    datos = cargar_datos(args.datos_dir or config.datos_dir)
    casos = seleccionar(cargar_casos(args.archivos), args)
    if not casos:
        print("No hay casos que correr con esos filtros.", file=sys.stderr)
        return 2

    if args.url:
        ejecutor: Any = EjecutorHTTP(args.url, timeout_s=args.timeout)
        etiqueta = "http"
    else:
        try:
            ejecutor = EjecutorLocal(datos, modelo=args.modelo, resolver=not args.sin_resolucion)
        except ErrorInterprete as exc:
            print(f"No se puede evaluar en proceso: {exc}", file=sys.stderr)
            return 2
        etiqueta = ejecutor.modelo.split("/")[-1] + ("_sinres" if args.sin_resolucion else "")
    marca = args.marca or marca_por_defecto(etiqueta)

    def avanzar(hechos: int, total: int, r: dict[str, Any]) -> None:
        if args.silencioso:
            return
        estado = "OK" if r.get("exacto") else ("ER" if r.get("error") else "XX")
        lat = r.get("latencia_s") or 0.0
        print(
            f"[{hechos:>3}/{total}] {estado} {r['id']:<6} {r['tipo']:<18} {lat:5.2f} s",
            file=sys.stderr,
        )

    resultados, resumen = correr_evaluacion(
        casos,
        ejecutor,
        datos,
        concurrencia=args.concurrencia,
        umbral_latencia_s=args.umbral_latencia,
        observadores=observadores,
        descripcion=ejecutor.descripcion,
        al_avanzar=avanzar,
    )
    resumen["marca"] = marca
    resumen["archivos"] = [str(a) for a in args.archivos]
    resumen["modelo"] = getattr(ejecutor, "modelo", None)
    resumen["resolucion_post_llm"] = None if args.url else not args.sin_resolucion
    directorio = args.dir_resultados / marca
    reporte = guardar_resultados(directorio, resultados, resumen)
    for obs in observadores:
        final = getattr(obs, "al_final", None)
        if final is not None:
            final(resumen, resultados, directorio)
    if isinstance(ejecutor, EjecutorHTTP):
        ejecutor.cerrar()
    print(reporte)
    print(f"Resultados en {directorio}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
