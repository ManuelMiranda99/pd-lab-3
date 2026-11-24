"""Exploración de `ejemplos/mensajes_dev.jsonl` y verificación de políticas contra el esperado.

Uso:
    uv run python scripts/explorar_casos.py \
        [--casos ejemplos/mensajes_dev.jsonl] \
        [--salida evidencia/sesiones/2026-11-22_F1/exploracion.txt]

Por cada `tipo` muestra el conteo y 1–2 ejemplos; luego verifica automáticamente reglas de
`politicas.md` (precio, descuento, redondeo, total, fecha, crédito, "lo de siempre",
sustituciones y coherencia del estado) y lista los casos que no las cumplen.

Los cálculos de aquí son **una referencia para explorar** (no el motor): la implementación
probada vive en `novapedidos.motor` (Fases 2 y 3). No usa el LLM.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from novapedidos.datos import Datos, obtener_datos

RAIZ = Path(__file__).resolve().parent.parent
CASOS = RAIZ / "ejemplos" / "mensajes_dev.jsonl"
SALIDA = RAIZ / "evidencia" / "sesiones" / "2026-11-22_F1" / "exploracion.txt"
CENTAVO = Decimal("0.01")
HORA_CORTE = 14

# --------------------------------------------------------------------------- referencia


def descuento_ref(datos: Datos, categoria: str, cantidad: int) -> int:
    """Mayor porcentaje cuyo mínimo se alcanza (0 si ninguno)."""
    pct = 0
    for escala in datos.escalas.get(categoria, ()):
        if cantidad >= escala.cantidad_minima:
            pct = max(pct, escala.descuento_pct)
    return pct


def subtotal_ref(cantidad: int, precio: Decimal, pct: int) -> Decimal:
    bruto = Decimal(cantidad) * precio * (Decimal(100) - Decimal(pct)) / Decimal(100)
    return bruto.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def fecha_recepcion_ref(fecha_hora: datetime) -> date:
    """14:00 o después ⇒ se considera recibido al día siguiente."""
    if fecha_hora.hour >= HORA_CORTE:
        return fecha_hora.date() + timedelta(days=1)
    return fecha_hora.date()


def fecha_entrega_ref(fecha_hora: datetime, dias_visita: tuple[int, ...]) -> date:
    """Primera visita estrictamente posterior a la fecha de recepción."""
    dia = fecha_recepcion_ref(fecha_hora)
    for _ in range(7):
        dia += timedelta(days=1)
        if dia.weekday() in dias_visita:
            return dia
    raise ValueError("cliente sin días de visita")


# --------------------------------------------------------------------------- reglas
# Cada regla recibe (caso, datos) y devuelve: None si no aplica, [] si cumple,
# o una lista de problemas (texto) si no cumple.

Resultado = list[str] | None


def _dec(valor: float) -> Decimal:
    return Decimal(str(valor))


def regla_a_precio(caso: dict, datos: Datos) -> Resultado:
    lineas = caso["esperado"]["lineas"]
    if not lineas:
        return None
    problemas = []
    for ln in lineas:
        prod = datos.producto(ln["sku"])
        if prod is None:
            problemas.append(f"sku {ln['sku']} no existe")
        elif _dec(ln["precio_unitario"]) != prod.precio:
            problemas.append(f"{ln['sku']}: {ln['precio_unitario']} ≠ catálogo {prod.precio}")
    return problemas


def regla_b_descuento(caso: dict, datos: Datos) -> Resultado:
    lineas = caso["esperado"]["lineas"]
    if not lineas:
        return None
    problemas = []
    for ln in lineas:
        prod = datos.producto(ln["sku"])
        esperado = descuento_ref(datos, prod.categoria, ln["cantidad"])
        if ln["descuento_pct"] != esperado:
            problemas.append(f"{ln['sku']} x{ln['cantidad']}: {ln['descuento_pct']}% ≠ {esperado}%")
    return problemas


def regla_c_subtotal(caso: dict, datos: Datos) -> Resultado:
    lineas = caso["esperado"]["lineas"]
    if not lineas:
        return None
    problemas = []
    for ln in lineas:
        calc = subtotal_ref(ln["cantidad"], _dec(ln["precio_unitario"]), ln["descuento_pct"])
        if _dec(ln["subtotal"]) != calc:
            problemas.append(f"{ln['sku']}: subtotal {ln['subtotal']} ≠ {calc}")
    return problemas


def regla_d_total(caso: dict, datos: Datos) -> Resultado:
    esp = caso["esperado"]
    suma = sum((_dec(ln["subtotal"]) for ln in esp["lineas"]), Decimal("0"))
    if _dec(esp["total"]) != suma:
        return [f"total {esp['total']} ≠ suma {suma}"]
    return []


def regla_e_fecha(caso: dict, datos: Datos) -> Resultado:
    esp = caso["esperado"]
    if esp["estado"] == "fuera_de_alcance":
        return [] if esp["fecha_entrega"] is None else ["fuera_de_alcance con fecha"]
    cliente = datos.cliente(caso["entrada"]["cliente_id"])
    fh = datetime.fromisoformat(caso["entrada"]["fecha_hora"])
    calc = fecha_entrega_ref(fh, cliente.dias_visita).isoformat()
    if esp["fecha_entrega"] != calc:
        return [f"fecha {esp['fecha_entrega']} ≠ {calc}"]
    return []


def regla_f_credito(caso: dict, datos: Datos) -> Resultado:
    esp = caso["esperado"]
    if esp["estado"] == "fuera_de_alcance":
        return None
    cliente = datos.cliente(caso["entrada"]["cliente_id"])
    excede = cliente.saldo_pendiente + _dec(esp["total"]) > cliente.limite_credito
    marcado = "credito_excedido" in esp["motivos_revision"]
    if excede != marcado:
        return [
            f"saldo {cliente.saldo_pendiente} + total {esp['total']} vs límite "
            f"{cliente.limite_credito}: excede={excede}, marcado={marcado}"
        ]
    return []


def lineas_habituales(datos: Datos, cliente_id: str) -> list[tuple[str, str, int]]:
    """(sku_original, efecto, cantidad) del último pedido entregado.

    efecto: "linea:<sku>" (disponible o sustituto) o "pendiente" (agotado sin sustituto).
    """
    pedido = datos.ultimo_pedido_entregado(cliente_id)
    salida = []
    for ln in pedido.lineas if pedido else ():
        prod = datos.producto(ln.sku)
        if prod.disponible:
            salida.append((ln.sku, f"linea:{ln.sku}", ln.cantidad))
        elif prod.sustituto_sku:
            salida.append((ln.sku, f"linea:{prod.sustituto_sku}", ln.cantidad))
        else:
            salida.append((ln.sku, "pendiente", ln.cantidad))
    return salida


def regla_g_habitual(caso: dict, datos: Datos) -> Resultado:
    if caso["tipo"] != "habitual" and not _menciona_habitual(caso):
        return None
    esp = caso["esperado"]
    lineas = {ln["sku"]: ln["cantidad"] for ln in esp["lineas"]}
    pend = {
        tuple(p["opciones"]) for p in esp["pendientes"] if p["motivo"] == "agotado_sin_sustituto"
    }
    texto = " ".join(caso["entrada"]["mensajes"]).lower()
    problemas = []
    habituales = lineas_habituales(datos, caso["entrada"]["cliente_id"])
    # Si un agotado se sustituye por un sku que ya estaba en el pedido, las cantidades se suman
    # en una sola línea (posición de la primera aparición).
    acumulado: dict[str, int] = {}
    for sku, efecto, cant in habituales:
        nombre = datos.producto(sku).nombre
        if efecto == "pendiente":
            if (sku,) not in pend:
                problemas.append(f"{sku} ({nombre}) agotado sin sustituto: falta pendiente")
            continue
        destino = efecto.split(":")[1]
        acumulado[destino] = acumulado.get(destino, 0) + cant
    orden_esperado = list(acumulado)
    for destino, cant in acumulado.items():
        if destino not in lineas:
            problemas.append(f"{destino} ({datos.producto(destino).nombre}) x{cant} no aparece")
        elif lineas[destino] != cant:
            problemas.append(f"{destino}: cantidad {lineas[destino]} ≠ habitual {cant}")
    presentes = [s for s in orden_esperado if s in lineas]
    prefijo = [ln["sku"] for ln in esp["lineas"]][: len(presentes)]
    if presentes and prefijo != presentes:
        problemas.append(f"orden: líneas {prefijo} ≠ orden del historial {presentes}")
    if problemas and ("sin " in texto or "quit" in texto):
        problemas = [f"{p} [¿exclusión pedida en el mensaje?]" for p in problemas]
    return problemas


def _menciona_habitual(caso: dict) -> bool:
    texto = " ".join(caso["entrada"]["mensajes"]).lower()
    claves = ("de siempre", "lo mismo", "de cada semana", "la vez pasada", "último pedido")
    return any(c in texto for c in claves)


def regla_h_sustituciones(caso: dict, datos: Datos) -> Resultado:
    esp = caso["esperado"]
    agotadas_pedidas = esp["sustituciones"] or [
        p for p in esp["pendientes"] if p["motivo"] == "agotado_sin_sustituto"
    ]
    if not agotadas_pedidas:
        # Aún así: ninguna línea puede ser un producto agotado.
        malas = [ln["sku"] for ln in esp["lineas"] if not datos.producto(ln["sku"]).disponible]
        return [f"línea con producto agotado {malas}"] if malas else None
    problemas = []
    lineas = {ln["sku"]: ln["cantidad"] for ln in esp["lineas"]}
    for s in esp["sustituciones"]:
        pedido = datos.producto(s["sku_solicitado"])
        if pedido.disponible:
            problemas.append(f"{pedido.sku} está disponible y se sustituyó")
        if pedido.sustituto_sku != s["sku_entregado"]:
            problemas.append(
                f"{pedido.sku}: sustituto {s['sku_entregado']} ≠ {pedido.sustituto_sku}"
            )
        if lineas.get(s["sku_entregado"], 0) < s["cantidad"]:
            problemas.append(
                f"{s['sku_entregado']}: línea x{lineas.get(s['sku_entregado'])} ≠ "
                f"sustitución x{s['cantidad']}"
            )
    for p in esp["pendientes"]:
        if p["motivo"] != "agotado_sin_sustituto":
            continue
        (sku,) = p["opciones"]
        prod = datos.producto(sku)
        if prod.disponible or prod.sustituto_sku:
            problemas.append(f"{sku} marcado sin sustituto pero disponible={prod.disponible}")
        if p["texto"] != prod.nombre:
            problemas.append(f"texto del pendiente {p['texto']!r} ≠ nombre {prod.nombre!r}")
    for ln in esp["lineas"]:
        if not datos.producto(ln["sku"]).disponible:
            problemas.append(f"línea con producto agotado {ln['sku']}")
    return problemas


def regla_i_estado(caso: dict, datos: Datos) -> Resultado:
    """Coherencia: estado ↔ motivos ↔ pendientes; fuera_de_alcance vacío."""
    esp = caso["esperado"]
    problemas = []
    motivos = esp["motivos_revision"]
    if len(motivos) != len(set(motivos)):
        problemas.append(f"motivos duplicados {motivos}")
    de_pendientes = {p["motivo"] for p in esp["pendientes"]}
    esperado_set = de_pendientes | ({"credito_excedido"} & set(motivos))
    if set(motivos) != esperado_set:
        problemas.append(f"motivos {motivos} ≠ motivos de pendientes {sorted(de_pendientes)}")
    if esp["estado"] == "fuera_de_alcance":
        if esp["lineas"] or esp["total"] != 0 or esp["pendientes"] or motivos:
            problemas.append("fuera_de_alcance con contenido")
    elif esp["estado"] == "confirmado":
        if motivos or not esp["lineas"]:
            problemas.append("confirmado con motivos o sin líneas")
    elif esp["estado"] == "requiere_revision":
        if not motivos:
            problemas.append("requiere_revision sin motivos")
    else:
        problemas.append(f"estado desconocido {esp['estado']}")
    for p in esp["pendientes"]:
        for sku in p["opciones"]:
            if datos.producto(sku) is None:
                problemas.append(f"opción inexistente {sku}")
    return problemas


REGLAS: list[tuple[str, str, Callable[[dict, Datos], Resultado]]] = [
    ("a", "precio_unitario = precio del catálogo", regla_a_precio),
    ("b", "descuento = mayor escala alcanzada por categoría", regla_b_descuento),
    ("c", "subtotal = cant × precio × (1 − d/100), mitad hacia arriba", regla_c_subtotal),
    ("d", "total = suma de subtotales", regla_d_total),
    ("e", "fecha_entrega = 1.ª visita posterior (corte 14:00)", regla_e_fecha),
    ("f", "credito_excedido ⇔ saldo + total > límite", regla_f_credito),
    ("g", "'lo de siempre' = último pedido entregado", regla_g_habitual),
    ("h", "sustituciones = sustituto_sku; sin sustituto → pendiente", regla_h_sustituciones),
    ("i", "coherencia estado / motivos / pendientes", regla_i_estado),
]


# --------------------------------------------------------------------------- salida


def resumir_esperado(esp: dict) -> str:
    partes = [f"estado={esp['estado']}"]
    if esp["lineas"]:
        lns = ", ".join(
            f"{ln['sku']}×{ln['cantidad']}"
            + (f" (-{ln['descuento_pct']}%)" if ln["descuento_pct"] else "")
            + f" = {ln['subtotal']}"
            for ln in esp["lineas"]
        )
        partes.append(f"líneas=[{lns}]")
    partes.append(f"total={esp['total']}")
    partes.append(f"fecha={esp['fecha_entrega']}")
    if esp["sustituciones"]:
        sus = ", ".join(
            f"{s['sku_solicitado']}→{s['sku_entregado']}×{s['cantidad']}"
            for s in esp["sustituciones"]
        )
        partes.append(f"sustituciones=[{sus}]")
    if esp["pendientes"]:
        pen = ", ".join(f"{p['motivo']}:{p['texto']!r}{p['opciones']}" for p in esp["pendientes"])
        partes.append(f"pendientes=[{pen}]")
    if esp["motivos_revision"]:
        partes.append(f"motivos={esp['motivos_revision']}")
    return " · ".join(partes)


def seccion_tipos(casos: list[dict], out: io.StringIO) -> None:
    por_tipo: dict[str, list[dict]] = defaultdict(list)
    for c in casos:
        por_tipo[c["tipo"]].append(c)
    out.write(f"Casos: {len(casos)} · tipos: {len(por_tipo)}\n\n")
    for tipo, lista in sorted(por_tipo.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        estados = Counter(c["esperado"]["estado"] for c in lista)
        out.write(f"## {tipo} ({len(lista)} casos) · estados: {dict(estados)}\n")
        for c in lista[:2]:
            e = c["entrada"]
            out.write(f"  [{c['id']}] {e['cliente_id']} {e['fecha_hora']}\n")
            for m in e["mensajes"]:
                out.write(f"     > {m!r}\n")
            out.write(f"     = {resumir_esperado(c['esperado'])}\n")
        out.write("\n")


def seccion_reglas(casos: list[dict], datos: Datos, out: io.StringIO) -> None:
    out.write(
        "=" * 78 + "\nVERIFICACIÓN AUTOMÁTICA DE REGLAS CONTRA EL ESPERADO\n" + "=" * 78 + "\n"
    )
    for clave, nombre, regla in REGLAS:
        aplica = cumple = 0
        fallos: list[tuple[str, list[str]]] = []
        for c in casos:
            res = regla(c, datos)
            if res is None:
                continue
            aplica += 1
            if res:
                fallos.append((c["id"], res))
            else:
                cumple += 1
        out.write(f"({clave}) {nombre}: {cumple}/{aplica} cumplen\n")
        for cid, problemas in fallos:
            for p in problemas:
                out.write(f"      ✗ {cid}: {p}\n")
    out.write("\n")


def seccion_observaciones(casos: list[dict], datos: Datos, out: io.StringIO) -> None:
    """Datos que resuelven dudas de política (ver DECISIONES.md)."""
    out.write("=" * 78 + "\nOBSERVACIONES PARA DECISIONES.md\n" + "=" * 78 + "\n")

    # Fecha: recepción en día de visita y horas cercanas al corte.
    out.write("* Recepción (tras corte) que cae en día de visita del cliente:\n")
    for c in casos:
        if c["esperado"]["fecha_entrega"] is None:
            continue
        fh = datetime.fromisoformat(c["entrada"]["fecha_hora"])
        cli = datos.cliente(c["entrada"]["cliente_id"])
        rec = fecha_recepcion_ref(fh)
        if rec.weekday() in cli.dias_visita:
            out.write(
                f"    {c['id']} {fh:%a %Y-%m-%d %H:%M} visita={cli.nombres_dias_visita} "
                f"→ recepción {rec} (día de visita) → esperado {c['esperado']['fecha_entrega']}\n"
            )
    out.write("* Mensajes entre 13:00 y 14:59 (cerca del corte):\n")
    for c in casos:
        fh = datetime.fromisoformat(c["entrada"]["fecha_hora"])
        if fh.hour in (13, 14) and c["esperado"]["fecha_entrega"]:
            out.write(f"    {c['id']} {fh:%a %H:%M} → {c['esperado']['fecha_entrega']}\n")
    exactos = [c["id"] for c in casos if c["entrada"]["fecha_hora"].endswith("T14:00")]
    out.write(f"* Casos a las 14:00 exactas: {exactos or 'ninguno'}\n")

    # Redondeo: dónde el float de Python diferiría del mitad-arriba.
    out.write("* Subtotales donde round(float) ≠ mitad-arriba (Decimal):\n")
    hay = False
    for c in casos:
        for ln in c["esperado"]["lineas"]:
            f = ln["cantidad"] * ln["precio_unitario"] * (1 - ln["descuento_pct"] / 100)
            d = subtotal_ref(ln["cantidad"], _dec(ln["precio_unitario"]), ln["descuento_pct"])
            if Decimal(str(round(f, 2))) != d:
                hay = True
                out.write(f"    {c['id']} {ln['sku']}: float={round(f, 2)} decimal={d}\n")
            bruto = (
                Decimal(ln["cantidad"])
                * _dec(ln["precio_unitario"])
                * (100 - ln["descuento_pct"])
                / 100
            )
            if bruto != bruto.quantize(CENTAVO) and bruto * 1000 % 10 == 5:
                out.write(f"    {c['id']} {ln['sku']}: bruto {bruto} termina en 5 → {d}\n")
                hay = True
    if not hay:
        out.write("    ninguno (ningún subtotal bruto tiene más de 2 decimales con 5 final)\n")

    # Crédito en el borde.
    out.write("* Crédito: casos con menor |límite − saldo − total| (margen < 0 ⇒ excede):\n")
    margenes = []
    for c in casos:
        if c["esperado"]["estado"] == "fuera_de_alcance":
            continue
        cli = datos.cliente(c["entrada"]["cliente_id"])
        margen = cli.limite_credito - cli.saldo_pendiente - _dec(c["esperado"]["total"])
        margenes.append((abs(margen), c["id"], margen, c["esperado"]["motivos_revision"]))
    for _, cid, margen, motivos in sorted(margenes)[:6]:
        out.write(f"    {cid}: margen {margen} → motivos {motivos}\n")

    out.write("* Sustituciones fusionadas con una línea del mismo sku (línea > sustitución):\n")
    for c in casos:
        lns = {ln["sku"]: ln["cantidad"] for ln in c["esperado"]["lineas"]}
        for s in c["esperado"]["sustituciones"]:
            if lns.get(s["sku_entregado"], 0) > s["cantidad"]:
                out.write(
                    f"    {c['id']}: {s['sku_solicitado']}→{s['sku_entregado']} ×{s['cantidad']} "
                    f"pero la línea {s['sku_entregado']} es ×{lns[s['sku_entregado']]}\n"
                )

    out.write("* 'Lo de siempre' de clientes cuyo pedido más reciente está cancelado:\n")
    for c in casos:
        if not _menciona_habitual(c):
            continue
        pedidos = datos.pedidos.get(c["entrada"]["cliente_id"], ())
        if pedidos and pedidos[-1].estado != "entregado":
            ult = datos.ultimo_pedido_entregado(c["entrada"]["cliente_id"])
            out.write(
                f"    {c['id']}: más reciente {pedidos[-1].pedido_id} ({pedidos[-1].estado}); "
                f"se usa {ult.pedido_id if ult else None}\n"
            )

    # Habitual con descuento o con líneas que reciben más cantidad.
    out.write("* 'Lo de siempre': ¿alguna línea habitual alcanza escala de descuento?\n")
    for c in casos:
        if not _menciona_habitual(c):
            continue
        for _sku, efecto, cant in lineas_habituales(datos, c["entrada"]["cliente_id"]):
            if efecto == "pendiente":
                continue
            destino = efecto.split(":")[1]
            pct = descuento_ref(datos, datos.producto(destino).categoria, cant)
            if pct:
                out.write(f"    {c['id']} {destino}×{cant} → {pct}%\n")
    out.write("    (si no hay filas arriba: ningún caso dev lo ejercita)\n")

    # Varios motivos y orden.
    out.write("* Casos con más de un motivo de revisión:\n")
    multi = [
        (c["id"], c["esperado"]["motivos_revision"])
        for c in casos
        if len(c["esperado"]["motivos_revision"]) > 1
    ]
    for cid, m in multi:
        out.write(f"    {cid}: {m}\n")
    if not multi:
        out.write("    ninguno\n")
    out.write("* Casos con varios pendientes (orden):\n")
    for c in casos:
        if len(c["esperado"]["pendientes"]) > 1:
            pares = [(p["motivo"], p["opciones"]) for p in c["esperado"]["pendientes"]]
            out.write(f"    {c['id']}: {pares}\n")

    # Textos de pendientes frente al mensaje.
    out.write("* Texto de pendientes frente al mensaje original:\n")
    for c in casos:
        texto = " ".join(c["entrada"]["mensajes"])
        for p in c["esperado"]["pendientes"]:
            literal = p["texto"] in texto
            out.write(
                f"    {c['id']} {p['motivo']}: {p['texto']!r} "
                f"{'(literal en el mensaje)' if literal else '(NO literal)'} "
                f"opciones={p['opciones']}\n"
            )
    out.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--casos", type=Path, default=CASOS)
    parser.add_argument("--salida", type=Path, default=SALIDA)
    args = parser.parse_args()

    casos = [
        json.loads(fila)
        for fila in args.casos.read_text(encoding="utf-8").splitlines()
        if fila.strip()
    ]
    datos = obtener_datos()

    out = io.StringIO()
    out.write(f"Exploración de {args.casos.name} · generado con scripts/explorar_casos.py\n\n")
    seccion_tipos(casos, out)
    seccion_reglas(casos, datos, out)
    seccion_observaciones(casos, datos, out)

    texto = out.getvalue()
    sys.stdout.write(texto)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(texto, encoding="utf-8")
    print(f"\nSalida guardada en {args.salida.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
