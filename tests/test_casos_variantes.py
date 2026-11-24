"""`eval/casos_variantes.jsonl` y `eval/casos_control.jsonl` coinciden con su generador.

El `esperado` de cada variante lo calcula el motor desde una interpretación escrita a mano;
esta prueba evita que el archivo y el generador se desincronicen. Necesita los parquet reales.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from novapedidos.datos import cargar_datos

RAIZ = Path(__file__).resolve().parent.parent
DATOS_DIR = RAIZ / "datos"

pytestmark = [
    pytest.mark.requiere_datos,
    pytest.mark.skipif(
        not (DATOS_DIR / "productos.parquet").is_file(), reason="sin datos publicados en datos/"
    ),
]


def _generador():
    spec = importlib.util.spec_from_file_location("variantes", RAIZ / "eval" / "variantes.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize(
    ("lista", "ruta", "minimo"), [("VARIANTES", "RUTA", 20), ("CONTROL", "RUTA_CONTROL", 10)]
)
def test_archivo_coincide_con_el_generador(lista, ruta, minimo):
    generador = _generador()
    casos = generador.construir_variantes(cargar_datos(DATOS_DIR), getattr(generador, lista))
    with getattr(generador, ruta).open(encoding="utf-8") as f:
        archivo = [json.loads(linea) for linea in f if linea.strip()]
    assert archivo == casos
    assert len(casos) >= minimo
    assert len({c["id"] for c in casos}) == len(casos)
