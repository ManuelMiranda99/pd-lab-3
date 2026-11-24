"""Atajo de la Fase 5, reemplazado por la evaluación de la Fase 6 (`eval/correr.py`).

Se conserva para no romper los comandos de la bitácora; traduce las opciones viejas y delega
en `novapedidos.evaluacion.cli` (sin lógica propia):

    uv run python scripts/probar_casos.py --smoke            # 1 caso por tipo
    uv run python scripts/probar_casos.py --ids D003 D082
    uv run python scripts/probar_casos.py --todos --hilos 4  # = eval/correr.py --concurrencia 4
"""

from __future__ import annotations

import argparse
import sys

from novapedidos.evaluacion.casos import cargar_casos
from novapedidos.evaluacion.cli import CASOS_DEV
from novapedidos.evaluacion.cli import main as main_evaluacion


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Atajo viejo de eval/correr.py")
    p.add_argument("--ids", nargs="+")
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--por-tipo", type=int)
    p.add_argument("--todos", action="store_true")
    p.add_argument("--hilos", type=int, default=1)
    args, resto = p.parse_known_args(argv)
    nuevo = [*resto, "--concurrencia", str(args.hilos)]
    n = 1 if args.smoke else args.por_tipo
    if args.ids:
        nuevo += ["--ids", *args.ids]
    elif n and not args.todos:
        vistos: dict[str, int] = {}
        ids = []
        for caso in cargar_casos([CASOS_DEV]):
            if vistos.get(caso["tipo"], 0) < n:
                vistos[caso["tipo"]] = vistos.get(caso["tipo"], 0) + 1
                ids.append(caso["id"])
        nuevo += ["--ids", *ids]
    return main_evaluacion(nuevo)


if __name__ == "__main__":
    sys.exit(main())
