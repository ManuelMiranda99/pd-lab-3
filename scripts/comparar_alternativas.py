"""Tabla, CSV y gráficas de la comparación de alternativas (Fase 10, `DECISIONES.md` D49).

Lee las corridas de `eval/resultados/f10_*` (las produce `eval/correr.py --marca f10_…`) y
escribe en `evidencia/comparacion/`:

- `comparacion.csv`: una fila por corrida (exactitud global, por conjunto e inyección,
  integridad de precios, latencia p50/p95/máx, costo, errores, filtro de contenido).
- `alternativas.csv`: una fila por alternativa (media de las repeticiones y diferencia).
- `exactitud_vs_costo.png`, `exactitud_vs_p95.png`, `exactitud_por_tipo_finalistas.png`.
- `corridas/<marca>.json`: el `resumen.json` de cada corrida (trazabilidad).

Uso (matplotlib no es dependencia del proyecto):

    uv run --with matplotlib python scripts/comparar_alternativas.py
"""

from __future__ import annotations

import csv
import json
import shutil
import statistics
from pathlib import Path
from typing import Any

RESULTADOS = Path("eval/resultados")
SALIDA = Path("evidencia/comparacion")

#: alternativa → (etiqueta corta, modelo, variante, corridas). Una corrida es una lista de
#: carpetas cuyos casos se unen (Haiku: casos válidos de la corrida con 429 + complemento).
ALTERNATIVAS: list[tuple[str, str, str, str, list[list[str]]]] = [
    ("nano", "nano", "openai/gpt-4.1-nano", "base", [["f10_nano_r1"], ["f10_nano_r2"]]),
    (
        "nano_sinres",
        "nano sin resolución",
        "openai/gpt-4.1-nano",
        "sin_resolucion",
        [["f10_nano_sinres_r1"]],
    ),
    ("mini", "4.1-mini", "openai/gpt-4.1-mini", "base", [["f10_mini_r1"], ["f10_mini_r2"]]),
    (
        "flash",
        "2.5-flash",
        "google/gemini-2.5-flash",
        "sin_razonamiento",
        [["f10_flash_r1"], ["f10_flash_r2"]],
    ),
    (
        "flashlite",
        "2.5-flash-lite",
        "google/gemini-2.5-flash-lite",
        "base",
        [["f10_flashlite_r1"], ["f10_flashlite_r2"]],
    ),
    (
        "mistral",
        "mistral-small-3.2",
        "mistralai/mistral-small-3.2-24b-instruct",
        "base",
        [["f10_mistral_r1"], ["f10_mistral_r2"]],
    ),
    (
        "haiku",
        "haiku-4.5",
        "anthropic/claude-haiku-4.5",
        "base",
        [["f10_invalida_haiku_r1", "f10_haiku_complemento_r1"]],
    ),
    (
        "flashlite_d49",
        "2.5-flash-lite + D49",
        "google/gemini-2.5-flash-lite",
        "resolucion_d49",
        [["f10_flashlite_d49_r1"], ["f10_flashlite_d49_r2"]],
    ),
    ("nano_d49", "nano + D49", "openai/gpt-4.1-nano", "resolucion_d49", [["f10_nano_d49_r1"]]),
]

#: Latencia "de producción" (un pedido a la vez) sobre 40 casos repartidos.
LATENCIA_C1 = [
    ("2.5-flash-lite", "f10_flashlite_c1_r1"),
    ("nano", "f10_nano_c1_r1"),
    ("2.5-flash", "f10_flash_c1_r1"),
]

FINALISTAS = [
    ("2.5-flash-lite + D49", "f10_flashlite_d49_r1", "#2a78d6"),
    ("nano + D49", "f10_nano_d49_r1", "#eb6834"),
    ("2.5-flash (sin D49)", "f10_flash_r1", "#1baf7a"),
]

#: Desplazamiento de etiquetas que se encimarían (gráfica, alternativa) → (x, y) en puntos.
DESPLAZAMIENTOS = {
    ("exactitud_vs_costo.png", "flashlite"): (7, -12),
    ("exactitud_vs_p95.png", "flashlite"): (7, -12),
    ("exactitud_vs_p95.png", "haiku"): (-20, 9),
    ("exactitud_vs_p95.png", "nano_d49"): (7, -12),
}

TINTA = "#0b0b0b"
TINTA_2 = "#52514e"
REJILLA = "#e4e3df"
SUPERFICIE = "#fcfcfb"
SERIE = "#2a78d6"
ELEGIDA = "#eb6834"


def _filas(carpeta: str) -> list[dict[str, Any]]:
    ruta = RESULTADOS / carpeta / "resultados.jsonl"
    return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea]


def _unir(carpetas: list[str]) -> list[dict[str, Any]]:
    """Une corridas parciales: el primer resultado sin error de cada caso gana."""
    por_id: dict[str, dict[str, Any]] = {}
    for carpeta in carpetas:
        for r in _filas(carpeta):
            previo = por_id.get(r["id"])
            if previo is None or (previo.get("error") and not r.get("error")):
                por_id[r["id"]] = r
    return list(por_id.values())


def _percentil(valores: list[float], q: float) -> float:
    orden = sorted(valores)
    if not orden:
        return float("nan")
    k = (len(orden) - 1) * q
    f = int(k)
    c = min(f + 1, len(orden) - 1)
    return orden[f] + (orden[c] - orden[f]) * (k - f)


def _tasa(filas: list[dict[str, Any]]) -> float:
    return sum(1 for r in filas if r.get("exacto")) / len(filas) if filas else float("nan")


def metricas(filas: list[dict[str, Any]]) -> dict[str, Any]:
    lat = [r["latencia_s"] for r in filas if r.get("latencia_s") is not None]
    costos = [
        (r.get("meta_llm") or {}).get("costo_usd")
        for r in filas
        if (r.get("meta_llm") or {}).get("costo_usd") is not None
    ]
    propios = [r for r in filas if r["origen"] == "casos_propios.jsonl"]
    inyeccion = [r for r in filas if r["tipo"] in ("inyeccion", "manipulacion")]
    integridad = [
        r["calificaciones"]["integridad_precios"]["valor"]
        for r in filas
        if "integridad_precios" in (r.get("calificaciones") or {})
    ]
    costo_caso = sum(costos) / len(costos) if costos else float("nan")
    return {
        "casos": len(filas),
        "exactos": sum(1 for r in filas if r.get("exacto")),
        "exactitud": _tasa(filas),
        "exactitud_dev": _tasa([r for r in filas if r["origen"] == "mensajes_dev.jsonl"]),
        "exactitud_propios": _tasa(propios),
        "exactitud_inyeccion": _tasa(inyeccion),
        "casos_inyeccion": len(inyeccion),
        "integridad_precios": sum(integridad) / len(integridad) if integridad else float("nan"),
        "p50_s": _percentil(lat, 0.5),
        "p95_s": _percentil(lat, 0.95),
        "max_s": max(lat) if lat else float("nan"),
        "costo_total_usd": sum(costos),
        "costo_por_caso_usd": costo_caso,
        "costo_por_1000_usd": costo_caso * 1000,
        "errores": sum(1 for r in filas if r.get("error")),
        "filtro_contenido": sum(
            1 for r in filas if (r.get("meta_llm") or {}).get("filtro_contenido")
        ),
        "fallos": " ".join(sorted(r["id"] for r in filas if not r.get("exacto"))),
    }


def por_tipo(filas: list[dict[str, Any]]) -> dict[str, float]:
    tipos: dict[str, list[dict[str, Any]]] = {}
    for r in filas:
        tipos.setdefault(r["tipo"], []).append(r)
    return {t: _tasa(v) for t, v in sorted(tipos.items())}


def _ejes(ax: Any) -> None:
    ax.set_facecolor(SUPERFICIE)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(REJILLA)
    ax.tick_params(colors=TINTA_2, labelsize=9)
    ax.grid(True, color=REJILLA, linewidth=0.8)
    ax.set_axisbelow(True)


def graficas(alternativas: list[dict[str, Any]], tipos_finalistas: dict[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    modelos = [a for a in alternativas if a["alternativa"] != "nano_sinres"]
    sin_res = next(a for a in alternativas if a["alternativa"] == "nano_sinres")["exactitud"]
    for eje_x, archivo, titulo, etiqueta_x in (
        (
            "costo_por_1000_usd",
            "exactitud_vs_costo.png",
            "Exactitud vs costo (177 casos, media de repeticiones)",
            "Costo por 1000 pedidos (USD, escala log)",
        ),
        (
            "p95_s",
            "exactitud_vs_p95.png",
            "Exactitud vs latencia p95 (177 casos, 4 en paralelo)",
            "Latencia p95 por pedido (s)",
        ),
    ):
        fig, ax = plt.subplots(figsize=(8, 5), dpi=150)
        fig.patch.set_facecolor(SUPERFICIE)
        _ejes(ax)
        for a in modelos:
            elegida = a["alternativa"] == "flashlite_d49"
            color = ELEGIDA if elegida else SERIE
            ax.scatter(
                a[eje_x],
                a["exactitud"] * 100,
                s=70 if elegida else 45,
                color=color,
                edgecolor=SUPERFICIE,
                linewidth=2,
                zorder=3,
            )
            ax.annotate(
                a["etiqueta"],
                (a[eje_x], a["exactitud"] * 100),
                xytext=DESPLAZAMIENTOS.get((archivo, a["alternativa"]), (7, -3)),
                textcoords="offset points",
                fontsize=8.5,
                color=TINTA if elegida else TINTA_2,
                fontweight="bold" if elegida else "normal",
            )
        if eje_x == "costo_por_1000_usd":
            ax.set_xscale("log")
        ax.set_xlabel(etiqueta_x, color=TINTA_2, fontsize=9.5)
        ax.set_ylabel("Exactitud global (caso exacto, %)", color=TINTA_2, fontsize=9.5)
        ax.set_title(titulo, color=TINTA, fontsize=11.5, loc="left")
        ax.text(
            0.0,
            -0.17,
            "Naranja: configuración entregada. Fuera del rango: nano sin resolución en código "
            f"({sin_res:.1%} de exactitud).",
            transform=ax.transAxes,
            fontsize=8,
            color=TINTA_2,
        )
        ax.set_ylim(97.5, 100.4)
        fig.tight_layout()
        fig.savefig(SALIDA / archivo, facecolor=SUPERFICIE)
        plt.close(fig)

    tipos = sorted({t for v in tipos_finalistas.values() for t in v})
    fig, ax = plt.subplots(figsize=(11, 5.6), dpi=150)
    fig.patch.set_facecolor(SUPERFICIE)
    _ejes(ax)
    ancho = 0.26
    for i, (nombre, _, color) in enumerate(FINALISTAS):
        valores = [tipos_finalistas[nombre].get(t, float("nan")) * 100 for t in tipos]
        xs = [j + (i - 1) * (ancho + 0.02) for j in range(len(tipos))]
        ax.bar(xs, valores, width=ancho, color=color, label=nombre, edgecolor=SUPERFICIE)
    ax.set_xticks(range(len(tipos)))
    ax.set_xticklabels(tipos, rotation=35, ha="right")
    ax.set_ylim(80, 101)
    ax.set_ylabel("Exactitud (%)", color=TINTA_2, fontsize=9.5)
    ax.set_title(
        "Exactitud por tipo de caso: finalistas (177 casos, una repetición)",
        color=TINTA,
        fontsize=11.5,
        loc="left",
    )
    ax.legend(frameon=False, fontsize=9, loc="upper left", bbox_to_anchor=(0, -0.3), ncol=3)
    fig.tight_layout()
    fig.savefig(SALIDA / "exactitud_por_tipo_finalistas.png", facecolor=SUPERFICIE)
    plt.close(fig)


def _fmt(v: Any) -> Any:
    return round(v, 6) if isinstance(v, float) else v


def main() -> None:
    SALIDA.mkdir(parents=True, exist_ok=True)
    (SALIDA / "corridas").mkdir(exist_ok=True)
    corridas: list[dict[str, Any]] = []
    alternativas: list[dict[str, Any]] = []
    for clave, etiqueta, modelo, variante, reps in ALTERNATIVAS:
        por_rep = []
        for n, carpetas in enumerate(reps, start=1):
            m = metricas(_unir(carpetas))
            fila = {
                "alternativa": clave,
                "modelo": modelo,
                "variante": variante,
                "repeticion": n,
                "corridas": "+".join(carpetas),
                "concurrencia": 4,
                **m,
            }
            corridas.append(fila)
            por_rep.append(m)
            for c in carpetas:
                shutil.copy(RESULTADOS / c / "resumen.json", SALIDA / "corridas" / f"{c}.json")
        resumen: dict[str, Any] = {
            "alternativa": clave,
            "etiqueta": etiqueta,
            "modelo": modelo,
            "variante": variante,
            "repeticiones": len(por_rep),
        }
        for k in (
            "exactitud",
            "exactitud_dev",
            "exactitud_propios",
            "exactitud_inyeccion",
            "integridad_precios",
            "p50_s",
            "p95_s",
            "max_s",
            "costo_por_caso_usd",
            "costo_por_1000_usd",
        ):
            vals = [r[k] for r in por_rep]
            resumen[k] = statistics.fmean(vals)
            resumen[f"{k}_dif"] = max(vals) - min(vals) if len(vals) > 1 else None
        resumen["exactos"] = "/".join(str(r["exactos"]) for r in por_rep)
        resumen["errores"] = sum(r["errores"] for r in por_rep)
        resumen["filtro_contenido"] = sum(r["filtro_contenido"] for r in por_rep)
        resumen["costo_fase_usd"] = sum(r["costo_total_usd"] for r in por_rep)
        resumen["fallos"] = " | ".join(r["fallos"] or "—" for r in por_rep)
        alternativas.append(resumen)

    for etiqueta, carpeta in LATENCIA_C1:
        m = metricas(_filas(carpeta))
        corridas.append(
            {
                "alternativa": f"latencia_c1_{etiqueta}",
                "modelo": etiqueta,
                "variante": "latencia_c1",
                "repeticion": 1,
                "corridas": carpeta,
                "concurrencia": 1,
                **m,
            }
        )
        shutil.copy(RESULTADOS / carpeta / "resumen.json", SALIDA / "corridas" / f"{carpeta}.json")

    for nombre, filas in (("comparacion.csv", corridas), ("alternativas.csv", alternativas)):
        with (SALIDA / nombre).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(filas[0]))
            w.writeheader()
            for fila in filas:
                w.writerow({k: _fmt(v) for k, v in fila.items()})

    tipos_finalistas = {n: por_tipo(_filas(c)) for n, c, _ in FINALISTAS}
    (SALIDA / "por_tipo_finalistas.json").write_text(
        json.dumps(tipos_finalistas, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    graficas(alternativas, tipos_finalistas)
    for a in alternativas:
        print(
            f"{a['etiqueta']:<22} {a['exactos']:<9} {a['exactitud']:.4f} "
            f"±{(a['exactitud_dif'] or 0):.4f} prop {a['exactitud_propios']:.4f} "
            f"iny {a['exactitud_inyeccion']:.3f} int {a['integridad_precios']:.3f} "
            f"p50 {a['p50_s']:.2f} p95 {a['p95_s']:.2f} (±{(a['p95_s_dif'] or 0):.2f}) "
            f"máx {a['max_s']:.2f} $/caso {a['costo_por_caso_usd']:.6f} "
            f"err {a['errores']} filtro {a['filtro_contenido']} costo {a['costo_fase_usd']:.4f}"
        )
        print(f"    fallos: {a['fallos']}")
    for c in corridas[-len(LATENCIA_C1) :]:
        print(
            f"c1 {c['modelo']:<16} {c['exactos']}/{c['casos']} p50 {c['p50_s']:.2f} "
            f"p95 {c['p95_s']:.2f} máx {c['max_s']:.2f} $/caso {c['costo_por_caso_usd']:.6f}"
        )


if __name__ == "__main__":
    main()
