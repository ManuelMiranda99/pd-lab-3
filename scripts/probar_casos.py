"""Corre casos de `ejemplos/mensajes_dev.jsonl` por el intérprete real (LLM) + el motor.

Uso:
    uv run python scripts/probar_casos.py --smoke                 # 1 caso por tipo
    uv run python scripts/probar_casos.py --ids D003 D082         # casos concretos
    uv run python scripts/probar_casos.py --por-tipo 2            # 2 casos por tipo
    uv run python scripts/probar_casos.py --todos --salida reporte.txt
    opciones: --modelo proveedor/modelo · --sin-resolucion · --hilos N · --jsonl detalle.jsonl

Compara con `esperado`: estado, líneas (sku+cantidad, como conjunto), total, fecha, motivos
(conjunto), pendientes (motivo+opciones) y sustituciones. Muestra latencia y costo por caso y
totales (exactitud global y por tipo, p50/p95, costo). No pasa por HTTP. Nunca imprime la clave.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from novapedidos.config import obtener_configuracion
from novapedidos.datos import obtener_datos
from novapedidos.llm.cliente import crear_modelo_chat
from novapedidos.llm.interfaz import ErrorInterprete
from novapedidos.llm.interprete import InterpreteLLM
from novapedidos.motor import armar_pedido, parsear_fecha_hora

RAIZ = Path(__file__).resolve().parent.parent
CASOS_DEV = RAIZ / "ejemplos" / "mensajes_dev.jsonl"


def cargar_casos(ruta: Path) -> list[dict[str, Any]]:
    with ruta.open(encoding="utf-8") as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def comparar(obtenido: dict[str, Any], esperado: dict[str, Any]) -> list[str]:
    """Lista de diferencias (vacía = caso exacto)."""
    fallos = []
    if obtenido["estado"] != esperado["estado"]:
        fallos.append(f"estado {obtenido['estado']} ≠ {esperado['estado']}")

    def lineas(r: dict[str, Any]) -> set[tuple[str, int]]:
        return {(linea["sku"], linea["cantidad"]) for linea in r["lineas"]}

    if lineas(obtenido) != lineas(esperado):
        fallos.append(f"líneas {sorted(lineas(obtenido))} ≠ {sorted(lineas(esperado))}")
    if abs(float(obtenido["total"]) - float(esperado["total"])) > 0.005:
        fallos.append(f"total {obtenido['total']} ≠ {esperado['total']}")
    if obtenido["fecha_entrega"] != esperado["fecha_entrega"]:
        fallos.append(f"fecha {obtenido['fecha_entrega']} ≠ {esperado['fecha_entrega']}")
    if set(obtenido["motivos_revision"]) != set(esperado["motivos_revision"]):
        fallos.append(f"motivos {obtenido['motivos_revision']} ≠ {esperado['motivos_revision']}")

    def pend(r: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
        return sorted((p["motivo"], tuple(p["opciones"])) for p in r["pendientes"])

    if pend(obtenido) != pend(esperado):
        fallos.append(f"pendientes {pend(obtenido)} ≠ {pend(esperado)}")

    def sust(r: dict[str, Any]) -> list[tuple[Any, ...]]:
        return sorted(tuple(sorted(s.items())) for s in r["sustituciones"])

    if sust(obtenido) != sust(esperado):
        fallos.append("sustituciones distintas")
    return fallos


def seleccionar(casos: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.ids:
        pedidos = set(args.ids)
        return [c for c in casos if c["id"] in pedidos]
    if args.todos:
        return casos
    n = 1 if args.smoke else args.por_tipo
    por_tipo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in casos:
        if len(por_tipo[c["tipo"]]) < n:
            por_tipo[c["tipo"]].append(c)
    return [c for grupo in por_tipo.values() for c in grupo]


def percentil(valores: list[float], p: float) -> float:
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = (len(orden) - 1) * p
    i = int(k)
    j = min(i + 1, len(orden) - 1)
    return orden[i] + (orden[j] - orden[i]) * (k - i)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prueba el intérprete LLM contra casos dev.")
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--ids", nargs="+", help="ids de casos (p. ej. D003 D082)")
    grupo.add_argument("--smoke", action="store_true", help="un caso por tipo")
    grupo.add_argument("--por-tipo", type=int, default=1, help="casos por tipo")
    grupo.add_argument("--todos", action="store_true", help="los 84 casos")
    parser.add_argument("--casos", type=Path, default=CASOS_DEV)
    parser.add_argument("--modelo", help="modelo de OpenRouter (por defecto OPENROUTER_MODEL)")
    parser.add_argument("--sin-resolucion", action="store_true", help="sin resolución post-LLM")
    parser.add_argument("--hilos", type=int, default=1, help="llamadas en paralelo")
    parser.add_argument("--salida", type=Path, help="guarda el reporte en este archivo")
    parser.add_argument("--jsonl", type=Path, help="guarda el detalle por caso (jsonl)")
    args = parser.parse_args(argv)

    config = obtener_configuracion()
    if not config.tiene_clave:
        print("Falta OPENROUTER_API_KEY en .env", file=sys.stderr)
        return 2
    modelo = args.modelo or config.openrouter_model
    datos = obtener_datos()
    interprete = InterpreteLLM(
        lambda: datos,
        lambda: crear_modelo_chat(config, modelo=modelo),
        nombre_modelo=modelo,
        resolver=not args.sin_resolucion,
    )
    casos = seleccionar(cargar_casos(args.casos), args)

    def correr(caso: dict[str, Any]) -> dict[str, Any]:
        entrada = caso["entrada"]
        fecha_hora = parsear_fecha_hora(entrada["fecha_hora"])
        try:
            interp, meta = interprete.interpretar_con_metadatos(
                entrada["cliente_id"], entrada["mensajes"], fecha_hora
            )
        except ErrorInterprete as exc:
            return {"caso": caso, "error": str(exc), "fallos": [f"error: {exc}"]}
        respuesta = armar_pedido(entrada["cliente_id"], fecha_hora, interp, datos)
        obtenido = respuesta.model_dump(mode="json")
        return {
            "caso": caso,
            "interpretacion": interp.model_dump(mode="json"),
            "obtenido": obtenido,
            "meta": meta.como_dict(),
            "fallos": comparar(obtenido, caso["esperado"]),
        }

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, args.hilos)) as pool:
        resultados = list(pool.map(correr, casos))
    duracion = time.perf_counter() - inicio

    lineas: list[str] = [
        f"Modelo: {modelo} · resolución post-LLM: {'no' if args.sin_resolucion else 'sí'} · "
        f"casos: {len(resultados)} · hilos: {args.hilos}",
        "",
    ]
    por_tipo: dict[str, list[bool]] = defaultdict(list)
    latencias: list[float] = []
    costo_total = 0.0
    tokens_entrada = tokens_salida = 0
    for r in resultados:
        caso = r["caso"]
        ok = not r["fallos"]
        por_tipo[caso["tipo"]].append(ok)
        meta = r.get("meta") or {}
        lat = meta.get("latencia_s")
        costo = meta.get("costo_usd") or 0.0
        if lat is not None:
            latencias.append(lat)
        costo_total += costo
        tokens_entrada += meta.get("tokens_entrada") or 0
        tokens_salida += meta.get("tokens_salida") or 0
        lat_txt = f"{lat:5.2f}s" if lat is not None else "  -  "
        lineas.append(
            f"{'OK ' if ok else 'XX '} {caso['id']} {caso['tipo']:<17} {lat_txt} USD {costo:.6f}"
        )
        if not ok:
            lineas.append(f"      mensajes: {caso['entrada']['mensajes']}")
            for f in r["fallos"]:
                lineas.append(f"      - {f}")
            if "interpretacion" in r:
                for m in r["interpretacion"]["menciones"]:
                    lineas.append(
                        f"        · {m['texto']!r} {m['resolucion']} {m['sku'] or m['opciones']} "
                        f"{m['cantidad']} {m['unidad']} {m['accion']}"
                    )
                if r["interpretacion"]["lo_de_siempre"]:
                    lineas.append("        · lo_de_siempre")

    total_ok = sum(ok for oks in por_tipo.values() for ok in oks)
    lineas += ["", "Exactitud por tipo:"]
    for tipo in sorted(por_tipo):
        oks = por_tipo[tipo]
        lineas.append(
            f"  {tipo:<17} {sum(oks):>3}/{len(oks):<3} {100 * sum(oks) / len(oks):5.1f} %"
        )
    n = len(resultados)
    lineas += [
        "",
        f"Exactitud global: {total_ok}/{n} = {100 * total_ok / max(n, 1):.1f} %",
        f"Latencia por llamada: p50 {percentil(latencias, 0.5):.2f} s · p95 "
        f"{percentil(latencias, 0.95):.2f} s · media "
        f"{statistics.mean(latencias) if latencias else 0:.2f} s",
        f"Tokens entrada/salida: {tokens_entrada} / {tokens_salida}",
        f"Costo total: USD {costo_total:.6f} (por caso USD {costo_total / max(n, 1):.6f})",
        f"Duración: {duracion:.1f} s",
    ]
    reporte = "\n".join(lineas)
    print(reporte)
    if args.salida:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(reporte + "\n", encoding="utf-8")
    if args.jsonl:
        args.jsonl.parent.mkdir(parents=True, exist_ok=True)
        with args.jsonl.open("w", encoding="utf-8") as f:
            for r in resultados:
                f.write(json.dumps({**r, "caso": r["caso"]["id"]}, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
