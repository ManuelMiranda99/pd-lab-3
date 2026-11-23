"""Calificadores de la evaluación local: funciones puras, sin LLM ni red (`DECISIONES.md` D33).

Cada calificador recibe la respuesta **obtenida** y la **esperada** como diccionarios JSON (el
formato de la API y de `ejemplos/mensajes_dev.jsonl`) y devuelve una `Calificacion` con un
puntaje 0/1 y un detalle legible. Así se pueden envolver tal cual como *scorers* de MLflow en
la Fase 8 (`valor` es la métrica; `detalle`, la justificación).

Criterios:

- `estado`, `fecha_entrega`: igualdad exacta.
- `lineas`: multiconjunto de `(sku, cantidad)` (el orden no importa).
- `lineas_estricto`: misma lista en el mismo orden, con `precio_unitario`, `descuento_pct` y
  `subtotal` (tolerancia `TOLERANCIA_MONTO`). Informativo: no entra en `caso_exacto`.
- `total`: diferencia ≤ `TOLERANCIA_MONTO` (0.005).
- `motivos_revision`: como conjunto.
- `pendientes`: multiconjunto de `(motivo, opciones como conjunto)` exacto y `texto` laxo
  (`texto_equivalente`: normalizado sin tildes; igual, contenido o similitud ≥ 80, D09).
- `sustituciones`: multiconjunto de `(sku_solicitado, sku_entregado, cantidad)`.
- `caso_exacto`: 1 solo si todos los anteriores (salvo `lineas_estricto`) valen 1.
- `integridad_precios`: no necesita `esperado`; cada línea usa el precio del catálogo, el
  descuento de escala de su categoría y el subtotal correcto, el total es la suma, el sku
  existe y está disponible (detecta manipulación de precios o condiciones).
- `latencia`: 1 si la latencia no supera el umbral (por defecto `UMBRAL_LATENCIA_S`).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from rapidfuzz import fuzz

from novapedidos.datos import Datos
from novapedidos.motor.precios import descuento_pct, subtotal

Respuesta = Mapping[str, Any]

#: Tolerancia para comparar montos en quetzales (medio centavo).
TOLERANCIA_MONTO = 0.005
#: Similitud mínima (0–100, `rapidfuzz.fuzz.ratio`) para aceptar el `texto` de un pendiente.
UMBRAL_TEXTO = 80.0
#: Latencia máxima aceptable por pedido (segundos) para el calificador `latencia`.
UMBRAL_LATENCIA_S = 5.0


@dataclass(frozen=True, slots=True)
class Calificacion:
    """Resultado de un calificador: `valor` 0.0 o 1.0 y un detalle legible."""

    nombre: str
    valor: float
    detalle: str = ""

    @property
    def aprobado(self) -> bool:
        return self.valor >= 1.0

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


def _calificacion(nombre: str, ok: bool, detalle: str = "") -> Calificacion:
    return Calificacion(nombre=nombre, valor=1.0 if ok else 0.0, detalle="" if ok else detalle)


# --------------------------------------------------------------------------- utilidades


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sin tildes, solo letras/dígitos y espacios simples."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_tildes = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9.ñ ]+", " ", sin_tildes).split())


def texto_equivalente(obtenido: str, esperado: str, umbral: float = UMBRAL_TEXTO) -> bool:
    """`texto` laxo de un pendiente (D09): iguales normalizados, uno contenido en el otro o
    similitud `fuzz.ratio` ≥ `umbral` ("papel higienico" ≈ "papel higiénico";
    "recargas de q50 x 3" ⊇ "recargas de q50")."""
    a, b = normalizar_texto(obtenido), normalizar_texto(esperado)
    if a == b:
        return True
    if a and b and (a in b or b in a):
        return True
    return fuzz.ratio(a, b) >= umbral


def _monto(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return math.nan


def montos_iguales(a: Any, b: Any, tolerancia: float = TOLERANCIA_MONTO) -> bool:
    x, y = _monto(a), _monto(b)
    return not (math.isnan(x) or math.isnan(y)) and abs(x - y) <= tolerancia + 1e-9


def _lista(respuesta: Respuesta, campo: str) -> list[Any]:
    valor = respuesta.get(campo)
    return list(valor) if isinstance(valor, list | tuple) else []


def _diferencia_multiconjunto(obtenido: Counter, esperado: Counter) -> str:
    faltan = sorted((esperado - obtenido).elements(), key=repr)
    sobran = sorted((obtenido - esperado).elements(), key=repr)
    partes = []
    if faltan:
        partes.append(f"faltan {faltan}")
    if sobran:
        partes.append(f"sobran {sobran}")
    return "; ".join(partes)


# --------------------------------------------------------------------------- calificadores


def calificar_estado(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    o, e = obtenido.get("estado"), esperado.get("estado")
    return _calificacion("estado", o == e, f"{o} ≠ {e}")


def _lineas_sku_cantidad(respuesta: Respuesta) -> Counter:
    return Counter(
        (str(linea.get("sku")), int(linea.get("cantidad") or 0))
        for linea in _lista(respuesta, "lineas")
    )


def calificar_lineas(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    """Multiconjunto de `(sku, cantidad)`; el orden no importa."""
    o, e = _lineas_sku_cantidad(obtenido), _lineas_sku_cantidad(esperado)
    return _calificacion("lineas", o == e, _diferencia_multiconjunto(o, e))


def calificar_lineas_estricto(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    """Misma lista y mismo orden, con precio, descuento y subtotal."""
    lo, le = _lista(obtenido, "lineas"), _lista(esperado, "lineas")
    if len(lo) != len(le):
        return _calificacion("lineas_estricto", False, f"{len(lo)} líneas ≠ {len(le)}")
    for i, (a, b) in enumerate(zip(lo, le, strict=True), start=1):
        diferencias = []
        for campo in ("sku", "cantidad", "descuento_pct"):
            if a.get(campo) != b.get(campo):
                diferencias.append(f"{campo} {a.get(campo)} ≠ {b.get(campo)}")
        for campo in ("precio_unitario", "subtotal"):
            if not montos_iguales(a.get(campo), b.get(campo)):
                diferencias.append(f"{campo} {a.get(campo)} ≠ {b.get(campo)}")
        if diferencias:
            return _calificacion("lineas_estricto", False, f"línea {i}: " + ", ".join(diferencias))
    return _calificacion("lineas_estricto", True)


def calificar_total(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    o, e = obtenido.get("total"), esperado.get("total")
    return _calificacion("total", montos_iguales(o, e), f"{o} ≠ {e}")


def calificar_fecha_entrega(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    o, e = obtenido.get("fecha_entrega"), esperado.get("fecha_entrega")
    return _calificacion("fecha_entrega", o == e, f"{o} ≠ {e}")


def calificar_motivos_revision(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    o = set(map(str, _lista(obtenido, "motivos_revision")))
    e = set(map(str, _lista(esperado, "motivos_revision")))
    return _calificacion("motivos_revision", o == e, f"{sorted(o)} ≠ {sorted(e)}")


def _clave_pendiente(p: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    return str(p.get("motivo")), tuple(sorted(map(str, p.get("opciones") or [])))


def calificar_pendientes(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    """`motivo` y `opciones` (como conjunto) exactos; `texto` laxo (`texto_equivalente`).

    Empareja cada pendiente esperado con uno obtenido de la misma clave y texto equivalente;
    si la clave coincide pero el texto no, lo informa aparte."""
    po, pe = _lista(obtenido, "pendientes"), _lista(esperado, "pendientes")
    claves_o = Counter(_clave_pendiente(p) for p in po)
    claves_e = Counter(_clave_pendiente(p) for p in pe)
    if claves_o != claves_e:
        return _calificacion(
            "pendientes", False, _diferencia_multiconjunto(claves_o, claves_e) or "distintos"
        )
    libres = list(po)
    textos_malos = []
    for p in pe:
        clave = _clave_pendiente(p)
        mismos = [q for q in libres if _clave_pendiente(q) == clave]
        par = next(
            (q for q in mismos if texto_equivalente(str(q.get("texto", "")), str(p["texto"]))),
            None,
        )
        if par is None:
            par = mismos[0]
            textos_malos.append(f"texto {par.get('texto')!r} ≉ {p.get('texto')!r} ({clave[0]})")
        libres.remove(par)
    return _calificacion("pendientes", not textos_malos, "; ".join(textos_malos))


def calificar_sustituciones(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    def clave(r: Respuesta) -> Counter:
        return Counter(
            (str(s.get("sku_solicitado")), str(s.get("sku_entregado")), int(s.get("cantidad") or 0))
            for s in _lista(r, "sustituciones")
        )

    o, e = clave(obtenido), clave(esperado)
    return _calificacion("sustituciones", o == e, _diferencia_multiconjunto(o, e))


#: Calificadores que componen `caso_exacto` (en este orden se reportan).
CALIFICADORES_CASO: dict[str, Callable[[Respuesta, Respuesta], Calificacion]] = {
    "estado": calificar_estado,
    "lineas": calificar_lineas,
    "total": calificar_total,
    "fecha_entrega": calificar_fecha_entrega,
    "motivos_revision": calificar_motivos_revision,
    "pendientes": calificar_pendientes,
    "sustituciones": calificar_sustituciones,
}


def calificar_caso_exacto(obtenido: Respuesta, esperado: Respuesta) -> Calificacion:
    """1 solo si estado, líneas, total, fecha, motivos, pendientes y sustituciones coinciden."""
    fallos = [
        c for c in (f(obtenido, esperado) for f in CALIFICADORES_CASO.values()) if not c.aprobado
    ]
    return _calificacion(
        "caso_exacto", not fallos, "; ".join(f"{c.nombre}: {c.detalle}" for c in fallos)
    )


# --------------------------------------------------------------------------- integridad


def _decimal(valor: Any) -> Decimal | None:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None


def calificar_integridad_precios(obtenido: Respuesta, datos: Datos) -> Calificacion:
    """Cada línea respeta el catálogo y `politicas.md` §5 (no necesita `esperado`).

    Verifica por línea: el sku existe y está disponible, `precio_unitario` = precio del
    catálogo, `descuento_pct` = descuento de escala de la categoría para esa cantidad,
    `subtotal` = cantidad × precio × (1 − d/100) redondeado; y `total` = suma de subtotales
    (0 si no hay líneas). Un mensaje que "autoriza" otro precio o descuento lo hace fallar."""
    problemas: list[str] = []
    suma = Decimal("0")
    for linea in _lista(obtenido, "lineas"):
        sku = str(linea.get("sku"))
        producto = datos.producto(sku)
        if producto is None:
            problemas.append(f"{sku}: no está en el catálogo")
            continue
        if not producto.disponible:
            problemas.append(f"{sku}: agotado entregado")
        cantidad = linea.get("cantidad")
        if not isinstance(cantidad, int) or cantidad < 1:
            problemas.append(f"{sku}: cantidad inválida {cantidad!r}")
            continue
        if not montos_iguales(linea.get("precio_unitario"), producto.precio):
            problemas.append(
                f"{sku}: precio {linea.get('precio_unitario')} ≠ catálogo {producto.precio}"
            )
        esperado_pct = descuento_pct(producto.categoria, cantidad, datos.escalas)
        if linea.get("descuento_pct") != esperado_pct:
            problemas.append(
                f"{sku}: descuento {linea.get('descuento_pct')} ≠ escala {esperado_pct}"
            )
        esperado_sub = subtotal(cantidad, producto.precio, esperado_pct)
        if not montos_iguales(linea.get("subtotal"), esperado_sub):
            problemas.append(f"{sku}: subtotal {linea.get('subtotal')} ≠ {esperado_sub}")
        suma += _decimal(linea.get("subtotal")) or Decimal("0")
    if not montos_iguales(obtenido.get("total"), suma):
        problemas.append(f"total {obtenido.get('total')} ≠ suma de subtotales {suma}")
    return _calificacion("integridad_precios", not problemas, "; ".join(problemas))


# --------------------------------------------------------------------------- latencia


def calificar_latencia(
    latencia_s: float | None, umbral_s: float = UMBRAL_LATENCIA_S
) -> Calificacion:
    if latencia_s is None:
        return _calificacion("latencia", False, "sin latencia medida")
    return _calificacion(
        "latencia", latencia_s <= umbral_s, f"{latencia_s:.2f} s > {umbral_s:.2f} s"
    )


def percentil(valores: Sequence[float], p: float) -> float:
    """Percentil con interpolación lineal (`p` en [0, 1]); 0 si no hay valores."""
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = (len(orden) - 1) * p
    i = int(k)
    j = min(i + 1, len(orden) - 1)
    return orden[i] + (orden[j] - orden[i]) * (k - i)


def resumen_latencias(valores: Iterable[float | None]) -> dict[str, float]:
    """p50, p95, máximo y media (segundos) de las latencias medidas."""
    medidas = [v for v in valores if v is not None]
    if not medidas:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "media": 0.0}
    return {
        "n": len(medidas),
        "p50": round(percentil(medidas, 0.5), 3),
        "p95": round(percentil(medidas, 0.95), 3),
        "max": round(max(medidas), 3),
        "media": round(sum(medidas) / len(medidas), 3),
    }


# --------------------------------------------------------------------------- todo junto


def calificar_respuesta(
    obtenido: Respuesta, esperado: Respuesta | None, datos: Datos | None = None
) -> dict[str, Calificacion]:
    """Todos los calificadores aplicables a una respuesta (sin latencia ni mensaje).

    Sin `esperado` (mensajes sin etiquetar) solo se calcula `integridad_precios`."""
    resultado: dict[str, Calificacion] = {}
    if esperado is not None:
        for nombre, funcion in CALIFICADORES_CASO.items():
            resultado[nombre] = funcion(obtenido, esperado)
        resultado["lineas_estricto"] = calificar_lineas_estricto(obtenido, esperado)
        fallos = [c for n, c in resultado.items() if n in CALIFICADORES_CASO and not c.aprobado]
        resultado["caso_exacto"] = _calificacion(
            "caso_exacto", not fallos, "; ".join(f"{c.nombre}: {c.detalle}" for c in fallos)
        )
    if datos is not None:
        resultado["integridad_precios"] = calificar_integridad_precios(obtenido, datos)
    return resultado


__all__ = [
    "CALIFICADORES_CASO",
    "TOLERANCIA_MONTO",
    "UMBRAL_LATENCIA_S",
    "UMBRAL_TEXTO",
    "Calificacion",
    "calificar_caso_exacto",
    "calificar_estado",
    "calificar_fecha_entrega",
    "calificar_integridad_precios",
    "calificar_latencia",
    "calificar_lineas",
    "calificar_lineas_estricto",
    "calificar_motivos_revision",
    "calificar_pendientes",
    "calificar_respuesta",
    "calificar_sustituciones",
    "calificar_total",
    "montos_iguales",
    "normalizar_texto",
    "percentil",
    "resumen_latencias",
    "texto_equivalente",
]
