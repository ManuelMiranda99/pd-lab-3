"""Evaluación local: corre uno o varios `.jsonl` contra el intérprete+motor o la API.

    uv run python eval/correr.py [archivos.jsonl ...] [--url URL] [--modelo M] [--concurrencia N]

La lógica vive en `novapedidos.evaluacion` (importable también por MLflow en la Fase 8);
este archivo es solo el punto de entrada. Ver `uv run python eval/correr.py --help`.
"""

import sys

from novapedidos.evaluacion.cli import main

if __name__ == "__main__":
    sys.exit(main())
