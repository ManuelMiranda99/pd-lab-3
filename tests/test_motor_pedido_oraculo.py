"""Prueba "oráculo" del armado del pedido contra los casos de `ejemplos/mensajes_dev.jsonl`.

Para cada caso se escribe **a mano** la `InterpretacionResuelta` que debería producir la
Fase 5 (LLM + resolvedor) y se verifica que `armar_pedido` reproduce el `esperado`: estado,
líneas (orden incluido), total, fecha, sustituciones, motivos y pendientes (motivo y opciones
exactos; `texto` normalizado y laxo según D09).

Necesita los parquet reales: se salta si `datos/` no existe (la CI no tiene datos).
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from novapedidos.datos import cargar_datos
from novapedidos.motor import (
    Accion,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
    armar_pedido,
    parsear_fecha_hora,
)

RAIZ = Path(__file__).resolve().parent.parent
DATOS_DIR = RAIZ / "datos"
CASOS_DEV = RAIZ / "ejemplos" / "mensajes_dev.jsonl"

pytestmark = [
    pytest.mark.requiere_datos,
    pytest.mark.skipif(
        not (DATOS_DIR / "productos.parquet").is_file(), reason="sin datos publicados en datos/"
    ),
]

SUELTA = Unidad.UNIDAD_SUELTA
DOCENA = Unidad.DOCENA
CORREGIR = Accion.CORREGIR
QUITAR = Accion.QUITAR


# --------------------------------------------------------------------------- mini DSL


def s(
    texto: str,
    sku: str,
    cantidad: int | None = None,
    unidad: Unidad = Unidad.VENTA,
    accion: Accion = Accion.AGREGAR,
) -> Mencion:
    """Mención resuelta a un sku."""
    return Mencion(
        texto=texto,
        resolucion=Resolucion.SKU,
        sku=sku,
        cantidad=cantidad,
        unidad=unidad,
        accion=accion,
    )


def amb(texto: str, opciones: list[str], cantidad: int | None = None) -> Mencion:
    """Mención ambigua."""
    return Mencion(texto=texto, resolucion=Resolucion.AMBIGUO, opciones=opciones, cantidad=cantidad)


def no(texto: str, cantidad: int | None = None) -> Mencion:
    """Mención de un producto que no se vende."""
    return Mencion(texto=texto, resolucion=Resolucion.NO_EXISTE, cantidad=cantidad)


def P(*menciones: Mencion, siempre: bool = False, pedido: bool = True) -> InterpretacionResuelta:
    return InterpretacionResuelta(
        es_pedido=pedido, lo_de_siempre=siempre, menciones=list(menciones)
    )


FUERA = P(pedido=False)

# --------------------------------------------------------------------------- interpretaciones

INTERPRETACIONES: dict[str, InterpretacionResuelta] = {
    # unidades
    "D001": P(
        s("agua pura cristal 600 ml", "B07", 72, SUELTA),
        s("jugo huerto naranja", "B09", 3),
        s("shampoo brisa sobre 10 ml", "P02", 5),
    ),
    "D024": P(s("cafe altura molido", "A12", 36, SUELTA)),
    "D050": P(s("kola de litro y medio", "B02", 36, SUELTA)),
    "D051": P(s("naranjita 1.5", "B05", 12, SUELTA), s("sopa rapida", "A15", 4)),
    "D056": P(s("kola norte 600", "B01", 48, SUELTA), s("pasta bella espagueti 200 g", "A10", 5)),
    "D065": P(
        s("jugo huerto naranja", "B09", 10),
        s("te quetzal", "B12", 36, SUELTA),
        s("arroz quintal", "A02", 5),
    ),
    "D074": P(
        s("sal pacifico", "A11", 5),
        s("detergente espumoso", "L01", 48, SUELTA),
        s("jabon lavandero", "L02", 4),
    ),
    "D084": P(s("cloro blancura", "L03", 1, DOCENA), s("coditos bella", "A09", 2)),
    # fuera de alcance
    "D002": FUERA,
    "D038": FUERA,
    "D066": FUERA,
    "D069": FUERA,
    "D083": FUERA,
    # ambiguo
    "D003": P(s("arroz ceiba de libra", "A01", 3), amb("naranjitas", ["B04", "B05"], 3)),
    "D005": P(s("nutrimezcla", "A14", 10), amb("papel higiénico", ["L04", "L05"])),
    "D008": P(
        amb("pasta", ["A09", "A10", "P03"], 2),
        s("espagueti bella", "A10", 6),
        s("pasta dental sonrisa 75 ml", "P03", 12),
    ),
    "D014": P(s("galletas dulcitas vainilla", "S01", 4), amb("pasta", ["A09", "A10", "P03"], 3)),
    "D028": P(s("harina mazorca", "A08", 5), amb("naranjitas", ["B04", "B05"])),
    "D064": P(s("agua pura cristal 1.5 l", "B08", 4), amb("cocas", ["B01", "B02", "B03"], 4)),
    "D082": P(amb("aceite", ["A06", "A07"], 2), s("café altura molido", "A12", 1)),
    # audio
    "D004": P(s("chicharritos picantes 30 g", "S04", 2), s("galleta crac", "S03", 1)),
    "D053": P(
        amb("juguitos", ["B09", "B10"], 4),
        s("jabón de barra lavandero 350 g", "L02", 3),
        s("leche vaquita", "A16", 10),
    ),
    "D059": P(
        s("arroz la ceiba quintal", "A02", 2),
        s("galletas dulcitas vainilla", "S01", 1),
        s("kola norte 600 ml", "B01", 5),
    ),
    # crédito
    "D010": P(s("jabon rosa", "P01", 4)),
    "D023": P(
        s("te frio quetzal limon 500 ml", "B12", 2),
        s("lavaplatos brillo", "L06", 3),
        s("galletas saladas crac", "S03", 4),
    ),
    "D029": P(s("espagueti bella", "A10", 5), s("sopa rapida pollo 64 g", "A15", 3)),
    "D071": P(s("naranjita 1.5", "B05", 4), s("arroz la ceiba quintal", "A02", 2)),
    "D075": P(s("cloro blancura", "L03", 2), s("frijol negro monte verde 1 lb", "A03", 4)),
    # sin cantidad
    "D015": P(s("te frio quetzal limon 500 ml", "B12", 6), s("chicharritos", "S04")),
    "D026": P(s("energía max", "B11", 4), s("kola norte 1.5 l", "B02")),
    "D068": P(s("jabon rosa", "P01", 3), s("mani la finca", "S06", 4), s("sopa rapida", "A15")),
    "D078": P(s("papel suave 4", "L04", 4), s("naranjita 600 ml", "B04")),
    # no existe
    "D036": P(no("cerveza", 5), s("mani la finca", "S06", 1), s("nutrimezcla", "A14", 1)),
    "D039": P(no("velas", 2), s("lavaplatos brillo", "L06", 5), s("sal pacifico", "A11", 6)),
    "D045": P(s("naranjita 600 ml", "B04", 3), no("recargas de Q50", 3)),
    "D048": P(s("naranjita 1.5 l", "B05", 3), no("gas propano", 5)),
    # agotado
    "D027": P(s("aceite girasol dorado 750 ml", "A06", 2), s("dulcitas chocolate", "S02", 24)),
    "D032": P(
        s("kola 600", "B01", 24),
        s("pasta dental sonrisa 75 ml", "P03", 10),
        s("papel suave 12", "L05", 6),
    ),
    "D037": P(s("café altura molido", "A12", 3), s("papel higiénico suave 12 rollos", "L05", 6)),
    "D042": P(
        s("sopa rápida pollo 64 g", "A15", 1),
        s("nutrimezcla", "A14", 5),
        s("jugo huerto manzana", "B10", 3),
    ),
    "D052": P(s("jabon de tocador rosa 100 g", "P01", 1), s("jugo huerto manzana", "B10", 2)),
    "D067": P(
        s("dulcitas vainilla", "S01", 1),
        s("jabon de barra lavandero 350 g", "L02", 5),
        s("cafe altura instantaneo 50 g", "A13", 6),
    ),
    # habitual
    "D012": P(s("papel suave 4", "L04", 2), siempre=True),
    "D017": P(siempre=True),
    "D025": P(s("sal pacifico", "A11", accion=QUITAR), siempre=True),
    "D030": P(siempre=True),
    "D031": P(s("galletas saladas crac", "S03", 5), siempre=True),
    "D033": P(s("detergente espumoso", "L01", 5), siempre=True),
    "D040": P(
        s("lavaplatos brillo", "L06", 2),
        s("desodorante fresco", "P04", accion=QUITAR),
        siempre=True,
    ),
    "D044": P(s("kola norte 600", "B01", 1), siempre=True),
    # corrección
    "D020": P(
        s("frijol rojo", "A04", 4),
        s("azúcar dulzura 5 lb", "A05", 2),
        s("azúcar dulzura 5 lb", "A05", 5, accion=CORREGIR),
    ),
    "D021": P(
        s("detergente espumoso 500 g", "L01", 3),
        s("pasta bella espagueti 200 g", "A10", 2),
        s("maní salado la finca 50 g", "S06", 10),
        s("detergente espumoso 500 g", "L01", 5, accion=CORREGIR),
    ),
    "D041": P(
        s("galleta crac", "S03", 6),
        s("te quetzal", "B12", 6),
        s("galleta crac", "S03", 8, accion=CORREGIR),
    ),
    "D054": P(
        s("agua pura 1.5", "B08", 20),
        s("papitas doradas", "S05", 1),
        s("papitas doradas 40 g", "S05", 4, accion=CORREGIR),
    ),
    "D062": P(
        s("jabon lavandero", "L02", 15),
        s("nutrimezcla 450 g", "A14", 3),
        s("aceite dorado", "A06", 2),
        s("jabón de barra lavandero 350 g", "L02", 16, accion=CORREGIR),
    ),
    "D070": P(
        s("nutrimezcla 450 g", "A14", 1),
        s("aceite girasol dorado 750 ml", "A06", 12),
        s("papitas doradas", "S05", 3),
        s("papitas doradas", "S05", 6, accion=CORREGIR),
    ),
    "D081": P(
        s("arroz ceiba de libra", "A01", 5),
        s("arroz ceiba de libra", "A01", 6, accion=CORREGIR),
        s("frijol negro", "A03", 2),
    ),
    # varios mensajes
    "D043": P(s("energía max 473 ml", "B11", 1), siempre=True),
    "D047": P(s("harina mazorca", "A08", 1), s("lavaplatos brillo 450 g", "L06", 2), siempre=True),
    "D058": P(s("harina mazorca", "A08", 3), siempre=True),
    "D073": P(
        s("agua pura cristal 600 ml", "B07", 2),
        s("jabón rosa", "P01", 6),
        s("naranjita 600", "B04", 3),
        s("jabón de tocador rosa 100 g", "P01", 9, accion=CORREGIR),
    ),
    "D076": P(
        s("arroz la ceiba quintal", "A02", 4),
        s("naranjita 1.5 l", "B05", 1),
        s("arroz quintal", "A02", 6, accion=CORREGIR),
    ),
    "D077": P(
        s("té quetzal", "B12", 4),
        s("sal pacifico", "A11", 4),
        s("galletas dulcitas vainilla", "S01", 5),
        s("kola norte 1.5", "B02", 1),
        s("sal pacifico 1 lb", "A11", 6, accion=CORREGIR),
    ),
    "D080": P(s("aceite dorado", "A06", 1), siempre=True),
    # simple
    "D006": P(s("sopa rápida pollo 64 g", "A15", 15)),
    "D007": P(
        s("sopa rapida", "A15", 10),
        s("energia max", "B11", 15),
        s("galleta crac", "S03", 3),
        s("detergente espumoso", "L01", 5),
    ),
    "D009": P(
        s("naranjita 600 ml", "B04", 5),
        s("papitas doradas", "S05", 2),
        s("cloro blancura 1 l", "L03", 2),
        s("sopa rapida", "A15", 4),
    ),
    "D013": P(
        s("kola de litro y medio", "B02", 3),
        s("azucar dulzura", "A05", 4),
        s("arroz quintal", "A02", 2),
    ),
    "D019": P(
        s("naranjita 1.5", "B05", 3),
        s("limonada volcan 600 ml", "B06", 12),
        s("pasta bella coditos 200 g", "A09", 5),
    ),
    "D035": P(
        s("naranjita 600", "B04", 5),
        s("kola norte 600 ml", "B01", 5),
        s("jabon de tocador rosa 100 g", "P01", 6),
    ),
    "D049": P(s("arroz la ceiba quintal", "A02", 4), s("arroz ceiba de libra", "A01", 4)),
    "D057": P(
        s("limonada volcan 600 ml", "B06", 5),
        s("shampoo brisa", "P02", 6),
        s("aceite girasol dorado 750 ml", "A06", 6),
        s("mani la finca", "S06", 3),
    ),
    "D072": P(
        s("papel suave 4", "L04", 3),
        s("limonada volcán 600 ml", "B06", 6),
        s("naranjita 1.5", "B05", 20),
        s("azúcar dulzura", "A05", 2),
    ),
}


def _casos() -> dict[str, dict]:
    with CASOS_DEV.open(encoding="utf-8") as f:
        casos = [json.loads(linea) for linea in f if linea.strip()]
    return {c["id"]: c for c in casos}


CASOS = _casos()
IDS = sorted(INTERPRETACIONES)


@pytest.fixture(scope="module")
def datos():
    return cargar_datos(DATOS_DIR)


def _norm(texto: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.lower().split())


def _texto_compatible(obtenido: str, esperado: str) -> bool:
    a, b = _norm(obtenido), _norm(esperado)
    return a == b or a in b or b in a


def test_cubre_todos_los_tipos():
    tipos = {CASOS[i]["tipo"] for i in IDS}
    assert tipos == {c["tipo"] for c in CASOS.values()}
    assert len(IDS) >= 20


@pytest.mark.parametrize("caso_id", IDS)
def test_oraculo(caso_id, datos):
    caso = CASOS[caso_id]
    entrada, esperado = caso["entrada"], caso["esperado"]
    respuesta = armar_pedido(
        entrada["cliente_id"],
        parsear_fecha_hora(entrada["fecha_hora"]),
        INTERPRETACIONES[caso_id],
        datos,
    )
    obtenido = respuesta.model_dump(mode="json")

    assert obtenido["cliente_id"] == entrada["cliente_id"]
    assert obtenido["estado"] == esperado["estado"]
    assert obtenido["lineas"] == esperado["lineas"]
    assert obtenido["total"] == esperado["total"]
    assert obtenido["fecha_entrega"] == esperado["fecha_entrega"]
    assert obtenido["sustituciones"] == esperado["sustituciones"]
    assert obtenido["motivos_revision"] == esperado["motivos_revision"]
    assert [(p["motivo"], p["opciones"]) for p in obtenido["pendientes"]] == [
        (p["motivo"], p["opciones"]) for p in esperado["pendientes"]
    ]
    for p_obt, p_esp in zip(obtenido["pendientes"], esperado["pendientes"], strict=True):
        assert _texto_compatible(p_obt["texto"], p_esp["texto"]), (p_obt, p_esp)
    assert obtenido["mensaje_cliente"].strip()
