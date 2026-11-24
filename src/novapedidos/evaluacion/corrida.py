"""Corrida de evaluación: casos → ejecutor (con concurrencia) → calificadores → resumen (D35).

`correr_evaluacion()` devuelve los resultados por caso y un resumen (exactitud global, por
tipo y por archivo, tasa por calificador, latencia p50/p95/máx, tokens y costo).
`guardar_resultados()` escribe `resultados.jsonl`, `resumen.json` y `reporte.txt`.

**Punto de extensión (Fase 8):** cualquier objeto con `al_caso(resultado)` y/o
`al_final(resumen, resultados, directorio)` puede pasarse en `observadores` (p. ej. uno que
registre cada caso y las métricas en MLflow). Un observador que falla nunca detiene la corrida.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Protocol

from novapedidos.datos import Datos
from novapedidos.evaluacion.calificadores import (
    CALIFICADORES_CASO,
    Calificacion,
    calificar_latencia,
    calificar_respuesta,
    resumen_latencias,
)
from novapedidos.evaluacion.ejecutores import Ejecucion
from novapedidos.evaluacion.mensaje_cliente import calificar_mensaje_cliente

logger = logging.getLogger("novapedidos.evaluacion")

#: Orden de los calificadores en el reporte.
ORDEN_CALIFICADORES = (
    "caso_exacto",
    *CALIFICADORES_CASO,
    "lineas_estricto",
    "integridad_precios",
    "mensaje_cliente",
    "latencia",
)


class Observador(Protocol):
    """Gancho opcional (p. ej. MLflow en la Fase 8). Ambos métodos son opcionales."""

    def al_caso(self, resultado: dict[str, Any]) -> None: ...

    def al_final(
        self, resumen: dict[str, Any], resultados: list[dict[str, Any]], directorio: Path | None
    ) -> None: ...


def _avisar(observadores: Sequence[Any], metodo: str, *args: Any) -> None:
    for obs in observadores:
        funcion = getattr(obs, metodo, None)
        if funcion is None:
            continue
        try:
            funcion(*args)
        except Exception:  # noqa: BLE001 - un observador nunca detiene la evaluación
            logger.warning("El observador %r falló en %s", obs, metodo, exc_info=True)


def calificar_ejecucion(
    caso: dict[str, Any], ejecucion: Ejecucion, datos: Datos, umbral_latencia_s: float
) -> dict[str, Calificacion]:
    """Todas las calificaciones de un caso (si hubo error, todas valen 0 con el error)."""
    esperado = caso.get("esperado")
    latencia = calificar_latencia(ejecucion.latencia_s, umbral_latencia_s)
    if ejecucion.obtenido is None:
        motivo = ejecucion.error or "sin respuesta"
        nombres = [n for n in ORDEN_CALIFICADORES if n != "latencia"]
        if esperado is None:
            nombres = ["integridad_precios", "mensaje_cliente"]
        calificaciones = {n: Calificacion(n, 0.0, motivo) for n in nombres}
        calificaciones["latencia"] = latencia
        return calificaciones
    calificaciones = calificar_respuesta(ejecucion.obtenido, esperado, datos)
    calificaciones["mensaje_cliente"] = calificar_mensaje_cliente(
        ejecucion.obtenido,
        datos,
        fecha_referencia=esperado.get("fecha_entrega") if esperado else None,
    )
    calificaciones["latencia"] = latencia
    return calificaciones


def correr_evaluacion(
    casos: Sequence[dict[str, Any]],
    ejecutor: Callable[[dict[str, Any]], Ejecucion],
    datos: Datos,
    *,
    concurrencia: int = 4,
    umbral_latencia_s: float = 5.0,
    observadores: Sequence[Any] = (),
    descripcion: str = "",
    al_avanzar: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Corre los casos (en paralelo) y devuelve `(resultados, resumen)` en el orden de entrada."""
    hechos = 0
    candado = threading.Lock()

    def correr(caso: dict[str, Any]) -> dict[str, Any]:
        nonlocal hechos
        ejecucion = ejecutor(caso)
        calificaciones = calificar_ejecucion(caso, ejecucion, datos, umbral_latencia_s)
        resultado = {
            "id": caso["id"],
            "tipo": caso["tipo"],
            "origen": caso.get("_origen", ""),
            "entrada": caso["entrada"],
            "esperado": caso.get("esperado"),
            **ejecucion.como_dict(),
            "calificaciones": {n: c.como_dict() for n, c in calificaciones.items()},
            "exacto": calificaciones["caso_exacto"].aprobado
            if "caso_exacto" in calificaciones
            else None,
        }
        _avisar(observadores, "al_caso", resultado)
        with candado:
            hechos += 1
            if al_avanzar is not None:
                al_avanzar(hechos, len(casos), resultado)
        return resultado

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, concurrencia)) as pool:
        resultados = list(pool.map(correr, casos))
    duracion = time.perf_counter() - inicio
    resumen = resumir(resultados, descripcion=descripcion, duracion_s=duracion)
    resumen["concurrencia"] = concurrencia
    resumen["umbral_latencia_s"] = umbral_latencia_s
    return resultados, resumen


def _tasa(aprobados: int, n: int) -> float:
    return round(aprobados / n, 4) if n else 0.0


def resumir(
    resultados: Sequence[dict[str, Any]], *, descripcion: str = "", duracion_s: float = 0.0
) -> dict[str, Any]:
    """Métricas agregadas de una corrida."""
    etiquetados = [r for r in resultados if r.get("exacto") is not None]
    exactos = sum(1 for r in etiquetados if r["exacto"])

    def agrupar(clave: str) -> dict[str, dict[str, Any]]:
        grupos: dict[str, list[bool]] = defaultdict(list)
        for r in etiquetados:
            grupos[r[clave]].append(bool(r["exacto"]))
        return {
            g: {"n": len(v), "exactos": sum(v), "exactitud": _tasa(sum(v), len(v))}
            for g, v in sorted(grupos.items())
        }

    por_calificador: dict[str, dict[str, Any]] = {}
    for nombre in ORDEN_CALIFICADORES:
        valores = [
            r["calificaciones"][nombre]["valor"]
            for r in resultados
            if nombre in r["calificaciones"]
        ]
        if valores:
            aprobados = sum(1 for v in valores if v >= 1.0)
            por_calificador[nombre] = {
                "n": len(valores),
                "aprobados": aprobados,
                "tasa": _tasa(aprobados, len(valores)),
            }

    metas = [r.get("meta_llm") or {} for r in resultados]
    costos = [m.get("costo_usd") for m in metas if m.get("costo_usd") is not None]
    costo_total = sum(costos)
    n = len(resultados)
    return {
        "descripcion": descripcion,
        "casos": n,
        "etiquetados": len(etiquetados),
        "exactos": exactos,
        "exactitud": _tasa(exactos, len(etiquetados)),
        "errores": sum(1 for r in resultados if r.get("error")),
        "por_tipo": agrupar("tipo"),
        "por_origen": agrupar("origen"),
        "por_calificador": por_calificador,
        "latencia_s": resumen_latencias(r.get("latencia_s") for r in resultados),
        "latencia_llm_s": resumen_latencias(m.get("latencia_s") for m in metas),
        "tokens": {
            "entrada": sum(m.get("tokens_entrada") or 0 for m in metas),
            "salida": sum(m.get("tokens_salida") or 0 for m in metas),
            "cache": sum(m.get("tokens_cache") or 0 for m in metas),
        },
        "costo_usd": {
            "total": round(costo_total, 6),
            "por_caso": round(costo_total / len(costos), 6) if costos else None,
            "casos_con_costo": len(costos),
        },
        "duracion_s": round(duracion_s, 1),
        "fallos": [r["id"] for r in etiquetados if not r["exacto"]],
    }


# --------------------------------------------------------------------------- reporte


def _lineas_txt(respuesta: dict[str, Any] | None) -> str:
    if not respuesta:
        return "-"
    lineas = respuesta.get("lineas") or []
    return ", ".join(f"{ln['sku']}×{ln['cantidad']}" for ln in lineas) or "∅"


def _pendientes_txt(respuesta: dict[str, Any] | None) -> str:
    if not respuesta:
        return "-"
    partes = [
        f"{p['motivo']}«{p.get('texto', '')}»{p.get('opciones') or ''}"
        for p in respuesta.get("pendientes") or []
    ]
    return ", ".join(partes) or "∅"


def _resumen_respuesta(respuesta: dict[str, Any] | None) -> str:
    if not respuesta:
        return "(sin respuesta)"
    sust = ", ".join(
        f"{s['sku_solicitado']}→{s['sku_entregado']}×{s['cantidad']}"
        for s in respuesta.get("sustituciones") or []
    )
    return (
        f"{respuesta.get('estado')} · líneas [{_lineas_txt(respuesta)}] · total "
        f"{respuesta.get('total')} · fecha {respuesta.get('fecha_entrega')} · motivos "
        f"{respuesta.get('motivos_revision')} · pendientes [{_pendientes_txt(respuesta)}]"
        + (f" · sustituciones [{sust}]" if sust else "")
    )


def _menciones_txt(interpretacion: dict[str, Any] | None) -> list[str]:
    if not interpretacion:
        return []
    lineas = []
    if interpretacion.get("lo_de_siempre"):
        lineas.append(f"lo_de_siempre (posición {interpretacion.get('posicion_lo_de_siempre')})")
    if not interpretacion.get("es_pedido"):
        lineas.append("es_pedido = false")
    for m in interpretacion.get("menciones") or []:
        destino = m.get("sku") or m.get("opciones")
        lineas.append(
            f"{m['texto']!r} → {m['resolucion']} {destino} · {m['cantidad']} {m['unidad']} "
            f"· {m['accion']}"
        )
    return lineas


def reporte_texto(resumen: dict[str, Any], resultados: Sequence[dict[str, Any]]) -> str:
    """Reporte legible: métricas agregadas y fallos con el diff esperado vs obtenido."""
    r = resumen
    salida = [
        f"Evaluación · {r.get('marca', '')} · {r['descripcion']}",
        f"Archivos: {', '.join(r.get('archivos', []))} · casos: {r['casos']} · concurrencia: "
        f"{r.get('concurrencia', '-')} · errores: {r['errores']}",
        "",
        f"Exactitud global (caso exacto): {r['exactos']}/{r['etiquetados']} = "
        f"{100 * r['exactitud']:.1f} %",
        "",
        "Exactitud por tipo:",
    ]
    for tipo, g in r["por_tipo"].items():
        salida.append(f"  {tipo:<20} {g['exactos']:>3}/{g['n']:<3} {100 * g['exactitud']:5.1f} %")
    if len(r["por_origen"]) > 1:
        salida += ["", "Exactitud por archivo:"]
        for origen, g in r["por_origen"].items():
            salida.append(
                f"  {origen:<28} {g['exactos']:>3}/{g['n']:<3} {100 * g['exactitud']:5.1f} %"
            )
    salida += ["", "Por calificador:"]
    for nombre, c in r["por_calificador"].items():
        salida.append(f"  {nombre:<20} {c['aprobados']:>3}/{c['n']:<3} {100 * c['tasa']:5.1f} %")
    lat, lat_llm, tok, costo = r["latencia_s"], r["latencia_llm_s"], r["tokens"], r["costo_usd"]
    salida += [
        "",
        f"Latencia por pedido (punta a punta): p50 {lat['p50']:.2f} s · p95 {lat['p95']:.2f} s "
        f"· máx {lat['max']:.2f} s · media {lat['media']:.2f} s",
    ]
    if lat_llm["n"]:
        salida.append(
            f"Latencia de la llamada al LLM: p50 {lat_llm['p50']:.2f} s · p95 "
            f"{lat_llm['p95']:.2f} s · máx {lat_llm['max']:.2f} s"
        )
    salida.append(
        f"Tokens entrada/salida (en caché): {tok['entrada']} / {tok['salida']} ({tok['cache']})"
    )
    if costo["casos_con_costo"]:
        salida.append(
            f"Costo total: USD {costo['total']:.6f} · por caso USD {costo['por_caso']:.6f}"
        )
    else:
        salida.append("Costo: no disponible (la API no expone el costo)")
    salida.append(f"Duración de la corrida: {r['duracion_s']:.1f} s")

    fallidos = [x for x in resultados if x.get("exacto") is False or x.get("error")]
    otros = [
        x
        for x in resultados
        if x not in fallidos and any(c["valor"] < 1.0 for c in x["calificaciones"].values())
    ]
    salida += ["", f"Fallos ({len(fallidos)}):"]
    for x in fallidos:
        salida.append(
            f"✗ {x['id']} [{x['tipo']}] {x['entrada']['cliente_id']} {x['entrada']['fecha_hora']}"
        )
        salida.append(f"    mensajes: {x['entrada']['mensajes']}")
        if x.get("error"):
            salida.append(f"    error: {x['error']}")
        for nombre, c in x["calificaciones"].items():
            if c["valor"] < 1.0 and nombre != "caso_exacto":
                salida.append(f"    - {nombre}: {c['detalle']}")
        salida.append(f"    esperado: {_resumen_respuesta(x.get('esperado'))}")
        salida.append(f"    obtenido: {_resumen_respuesta(x.get('obtenido'))}")
        for m in _menciones_txt(x.get("interpretacion")):
            salida.append(f"      · {m}")
    if otros:
        salida += ["", f"Casos exactos con otros calificadores en 0 ({len(otros)}):"]
        for x in otros:
            malos = [
                f"{n}: {c['detalle']}" for n, c in x["calificaciones"].items() if c["valor"] < 1.0
            ]
            salida.append(f"  {x['id']} [{x['tipo']}] " + " | ".join(malos))
    return "\n".join(salida) + "\n"


def guardar_resultados(
    directorio: Path, resultados: Sequence[dict[str, Any]], resumen: dict[str, Any]
) -> str:
    """Escribe `resultados.jsonl`, `resumen.json` y `reporte.txt`; devuelve el reporte."""
    directorio.mkdir(parents=True, exist_ok=True)
    with (directorio / "resultados.jsonl").open("w", encoding="utf-8") as f:
        for r in resultados:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (directorio / "resumen.json").write_text(
        json.dumps(resumen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    reporte = reporte_texto(resumen, resultados)
    (directorio / "reporte.txt").write_text(reporte, encoding="utf-8")
    return reporte


__all__ = [
    "ORDEN_CALIFICADORES",
    "Observador",
    "calificar_ejecucion",
    "correr_evaluacion",
    "guardar_resultados",
    "reporte_texto",
    "resumir",
]
