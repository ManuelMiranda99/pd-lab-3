"""`eval/casos_propios.jsonl` coincide con su generador y tiene el formato de dev (Fase 7, D38).

El `esperado` de cada caso propio lo calcula el motor desde una interpretación escrita a mano;
esta prueba evita que el archivo y el generador se desincronicen y verifica que cada caso
tiene exactamente las claves de `ejemplos/mensajes_dev.jsonl`. Necesita los parquet reales.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

from novapedidos.datos import cargar_datos
from novapedidos.evaluacion.casos import CAMPOS_ESPERADO

RAIZ = Path(__file__).resolve().parent.parent
DATOS_DIR = RAIZ / "datos"
DEV = RAIZ / "ejemplos" / "mensajes_dev.jsonl"
PROPIOS = RAIZ / "eval" / "casos_propios.jsonl"

TIPOS_NUEVOS = {"inyeccion", "borde_regla", "fuera_catalogo"}


def _leer(ruta: Path) -> list[dict]:
    with ruta.open(encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def _generador():
    spec = importlib.util.spec_from_file_location(
        "casos_propios", RAIZ / "eval" / "casos_propios.py"
    )
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.requiere_datos
@pytest.mark.skipif(
    not (DATOS_DIR / "productos.parquet").is_file(), reason="sin datos publicados en datos/"
)
def test_archivo_coincide_con_el_generador():
    generador = _generador()
    casos = generador.construir(cargar_datos(DATOS_DIR))
    assert _leer(generador.RUTA) == casos


def test_formato_igual_al_de_dev():
    casos = _leer(PROPIOS)
    assert len(casos) >= 35
    ids = [c["id"] for c in casos]
    assert len(set(ids)) == len(ids)
    assert all(i.startswith("P") and i[1:].isdigit() and len(i) == 4 for i in ids)
    if DEV.is_file():
        modelo = _leer(DEV)[0]
        claves, claves_entrada = set(modelo), set(modelo["entrada"])
        claves_esperado = set(modelo["esperado"])
    else:
        claves = {"id", "tipo", "entrada", "esperado"}
        claves_entrada = {"cliente_id", "fecha_hora", "mensajes"}
        claves_esperado = set(CAMPOS_ESPERADO)
    for caso in casos:
        assert set(caso) == claves, caso["id"]
        assert set(caso["entrada"]) == claves_entrada, caso["id"]
        assert set(caso["esperado"]) == claves_esperado, caso["id"]
        assert caso["entrada"]["mensajes"] and all(caso["entrada"]["mensajes"])


def test_cobertura_por_tipo():
    tipos = Counter(c["tipo"] for c in _leer(PROPIOS))
    assert tipos["inyeccion"] >= 8
    assert set(tipos) >= TIPOS_NUEVOS
    if DEV.is_file():
        for tipo in {c["tipo"] for c in _leer(DEV)}:
            assert tipos[tipo] >= 2, tipo
