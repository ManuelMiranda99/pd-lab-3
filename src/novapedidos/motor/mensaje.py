"""Mensaje al cliente por plantilla determinista (`politicas.md` §9, `DECISIONES.md` D20).

Se redacta **solo** con los datos ya calculados de la respuesta (líneas, sustituciones,
pendientes, total, fecha) y los nombres del catálogo: nunca promete agotados ni pendientes, no
menciona otros precios que el total calculado y, si hay revisión, dice que un asesor confirmará.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from novapedidos.contrato import EstadoPedido, MotivoRevision, Pendiente, RespuestaPedido
from novapedidos.datos import DIAS_SEMANA, Datos, Producto
from novapedidos.motor.modelos import TEXTO_LO_DE_SIEMPRE

MESES: tuple[str, ...] = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

#: Largo máximo del texto del cliente citado en el mensaje (evita eco de mensajes largos).
_MAX_CITA = 60


def saludo(fecha_hora: datetime) -> str:
    """Buenos días (05:00–11:59), buenas tardes (12:00–18:59), buenas noches (resto)."""
    hora = fecha_hora.hour
    if 5 <= hora < 12:
        return "¡Buenos días!"
    if 12 <= hora < 19:
        return "¡Buenas tardes!"
    return "¡Buenas noches!"


def fecha_en_espanol(dia: date) -> str:
    """`date(2026, 11, 26)` → `"jueves 26 de noviembre"`."""
    return f"{DIAS_SEMANA[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]}"


def plural_unidad(unidad: str, cantidad: int) -> str:
    """fardo → fardos, caja → cajas, display → displays, quintal → quintales."""
    if cantidad == 1:
        return unidad
    return f"{unidad}es" if unidad[-1:] not in "aeiouy" else f"{unidad}s"


def cantidad_con_unidad(cantidad: int, producto: Producto) -> str:
    """`2, Kola Norte 600 ml` → `"2 fardos de Kola Norte 600 ml"`."""
    return f"{cantidad} {plural_unidad(producto.unidad_venta, cantidad)} de {producto.nombre}"


def formatear_quetzales(monto: Decimal) -> str:
    """`Decimal("1497.6")` → `"Q1,497.60"`."""
    return f"Q{monto:,.2f}"


def enumerar(partes: list[str]) -> str:
    """`["a", "b", "c"]` → `"a, b y c"`."""
    if len(partes) <= 1:
        return "".join(partes)
    return f"{', '.join(partes[:-1])} y {partes[-1]}"


def _cita(texto: str) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= _MAX_CITA else texto[: _MAX_CITA - 1].rstrip() + "…"


def _frase_pendiente(pendiente: Pendiente, datos: Datos) -> str:
    motivo = pendiente.motivo
    if motivo is MotivoRevision.PRODUCTO_AMBIGUO:
        if not pendiente.opciones and pendiente.texto == TEXTO_LO_DE_SIEMPRE:
            return "No encontramos un pedido anterior entregado para repetir «lo de siempre»."
        return f"Sobre «{_cita(pendiente.texto)}», necesitamos confirmar cuál producto desea."
    if motivo is MotivoRevision.PRODUCTO_NO_EXISTE:
        return f"No manejamos «{_cita(pendiente.texto)}» en nuestro catálogo."
    if motivo is MotivoRevision.AGOTADO_SIN_SUSTITUTO:
        return f"{pendiente.texto} está agotado por ahora y no va incluido."
    # cantidad_no_convertible
    producto = datos.producto(pendiente.opciones[0]) if pendiente.opciones else None
    if producto is None:
        return f"La cantidad de «{_cita(pendiente.texto)}» no corresponde a empaques completos."
    return (
        f"La cantidad de «{_cita(pendiente.texto)}» no completa "
        f"{plural_unidad(producto.unidad_venta, 2)} enteros "
        f"(de {producto.unidades_por_empaque} unidades)."
    )


def redactar_mensaje(respuesta: RespuestaPedido, fecha_hora: datetime, datos: Datos) -> str:
    """Mensaje breve y cordial en español para el cliente, a partir de la respuesta calculada."""
    partes = [saludo(fecha_hora)]
    if respuesta.estado is EstadoPedido.FUERA_DE_ALCANCE:
        partes.append("Gracias por escribirnos. Un asesor atenderá su consulta en breve.")
        return " ".join(partes)

    productos = [
        cantidad_con_unidad(linea.cantidad, datos.productos[linea.sku])
        for linea in respuesta.lineas
    ]
    if productos:
        partes.append(f"Recibimos su pedido: {enumerar(productos)}.")
    else:
        partes.append("Recibimos su mensaje.")

    for s in respuesta.sustituciones:
        solicitado = datos.productos[s.sku_solicitado]
        entregado = datos.productos[s.sku_entregado]
        partes.append(
            f"Como {solicitado.nombre} está agotado, en su lugar le enviamos "
            f"{cantidad_con_unidad(s.cantidad, entregado)}."
        )

    partes.extend(_frase_pendiente(p, datos) for p in respuesta.pendientes)
    if MotivoRevision.CREDITO_EXCEDIDO in respuesta.motivos_revision:
        partes.append("El pedido necesita una revisión de su crédito.")

    if productos:
        partes.append(f"Total: {formatear_quetzales(respuesta.total)}.")
        if respuesta.fecha_entrega is not None:
            entrega = fecha_en_espanol(respuesta.fecha_entrega)
            if respuesta.estado is EstadoPedido.CONFIRMADO:
                partes.append(f"Entrega el {entrega}.")
            else:
                partes.append(f"Entrega prevista el {entrega}.")

    if respuesta.estado is EstadoPedido.REQUIERE_REVISION:
        partes.append("Un asesor le confirmará su pedido en breve.")
    partes.append("¡Gracias!")
    return " ".join(partes)


__all__ = [
    "MESES",
    "cantidad_con_unidad",
    "enumerar",
    "fecha_en_espanol",
    "formatear_quetzales",
    "plural_unidad",
    "redactar_mensaje",
    "saludo",
]
