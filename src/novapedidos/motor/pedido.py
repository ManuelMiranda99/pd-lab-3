"""Armado del pedido: de la interpretación resuelta a la respuesta del contrato.

Código puro y determinista (sin LLM, sin reloj del sistema). Aplica `politicas.md` §2–§8 y las
decisiones D03–D12 y D15–D21 de `DECISIONES.md`:

1. Se recorren las menciones en orden (con "lo de siempre" insertado en su posición) y se
   acumula una lista ordenada de **entradas**: cantidades por sku *solicitado* (en unidades de
   venta) y pendientes (`producto_ambiguo`, `producto_no_existe`, `cantidad_no_convertible`).
   `agregar` suma, `corregir` reemplaza en la misma posición y `quitar` elimina.
2. Al final cada sku solicitado se convierte en lo que se **entrega**: él mismo, su sustituto
   (con `sustituciones`, fusionando con una línea existente del sustituto, D07) o un pendiente
   `agotado_sin_sustituto`.
3. Precios y descuentos con la cantidad final de cada línea (D05), total, crédito (D12), fecha de
   entrega (D03), estado, motivos (D11) y mensaje al cliente por plantilla.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime

from novapedidos.contrato import (
    EstadoPedido,
    LineaPedido,
    MotivoRevision,
    Pendiente,
    RespuestaPedido,
    Sustitucion,
)
from novapedidos.datos import Cliente, Datos, Producto
from novapedidos.motor.credito import credito_excedido
from novapedidos.motor.fechas import fecha_entrega
from novapedidos.motor.mensaje import redactar_mensaje
from novapedidos.motor.modelos import (
    TEXTO_LO_DE_SIEMPRE,
    Accion,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
)
from novapedidos.motor.precios import calcular_linea, total


class ErrorClienteInexistente(LookupError):
    """El `cliente_id` no está en `clientes.parquet` (la API responde 404)."""


# ---------------------------------------------------------------------------
# Entradas acumuladas
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Entrada:
    """Una entrada de la lista acumulada.

    - Producto: `sku` con `cantidad` en unidades de venta (`pendiente` es None).
    - Pendiente: `pendiente` con, opcionalmente, el `sku` al que está ligado
      (`cantidad_no_convertible`), para que una corrección o un "quitar" lo elimine.
    """

    sku: str | None = None
    cantidad: int = 0
    pendiente: Pendiente | None = None

    @property
    def es_producto(self) -> bool:
        return self.pendiente is None


def _normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios simples (para comparar textos de pendientes)."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.lower().split())


def _sku_entregado(producto: Producto) -> str:
    """El sku que realmente se entrega: el propio o, si está agotado, su sustituto."""
    if not producto.disponible and producto.sustituto_sku:
        return producto.sustituto_sku
    return producto.sku


def convertir_a_unidades_de_venta(cantidad: int, unidad: Unidad, producto: Producto) -> int | None:
    """Convierte una cantidad a unidades de venta del producto (`politicas.md` §2).

    `venta` se toma tal cual; `unidad_suelta` y `docena` se dividen entre
    `unidades_por_empaque` **solo si la división es exacta** (si no, `None`: no se redondea).
    """
    if unidad is Unidad.VENTA:
        return cantidad
    unidades = cantidad * 12 if unidad is Unidad.DOCENA else cantidad
    por_empaque = producto.unidades_por_empaque
    if unidades % por_empaque != 0:
        return None
    return unidades // por_empaque


class _Acumulador:
    """Lista ordenada de entradas con las operaciones agregar / corregir / quitar."""

    def __init__(self, datos: Datos) -> None:
        self.datos = datos
        self.entradas: list[_Entrada] = []

    # -- consultas -----------------------------------------------------------

    def _entregado(self, sku: str) -> str:
        producto = self.datos.producto(sku)
        return _sku_entregado(producto) if producto else sku

    def _relacionada(self, entrada: _Entrada, sku: str) -> bool:
        """¿La entrada trata del producto `sku` (pedido como tal o entregado como `sku`)?"""
        if entrada.sku is None:
            return False
        return entrada.sku == sku or self._entregado(entrada.sku) == sku

    # -- operaciones sobre productos -----------------------------------------

    def agregar_producto(self, sku: str, cantidad: int) -> None:
        """Suma `cantidad` al sku (conserva la posición de su primera mención)."""
        for entrada in self.entradas:
            if entrada.es_producto and entrada.sku == sku:
                entrada.cantidad += cantidad
                return
        self.entradas.append(_Entrada(sku=sku, cantidad=cantidad))

    def agregar_no_convertible(self, sku: str, texto: str) -> None:
        """Pendiente `cantidad_no_convertible` ligado al sku (uno por sku)."""
        for entrada in self.entradas:
            if not entrada.es_producto and entrada.sku == sku:
                return
        self.entradas.append(_Entrada(sku=sku, pendiente=_pendiente_no_convertible(sku, texto)))

    def quitar_producto(self, sku: str) -> int | None:
        """Elimina todo lo relacionado con `sku` (líneas, sustituidos y pendientes ligados).

        Devuelve la posición de la primera entrada eliminada (o `None` si no había nada)."""
        posicion: int | None = None
        conservadas: list[_Entrada] = []
        for i, entrada in enumerate(self.entradas):
            if self._relacionada(entrada, sku):
                posicion = i if posicion is None else posicion
            else:
                conservadas.append(entrada)
        self.entradas = conservadas
        return posicion

    def corregir_producto(self, sku: str, nueva: _Entrada) -> None:
        """Reemplaza todo lo dicho de `sku` por `nueva`, en la posición de la primera mención."""
        posicion = self.quitar_producto(sku)
        if posicion is None:
            self.entradas.append(nueva)
        else:
            self.entradas.insert(posicion, nueva)

    # -- operaciones sobre pendientes ----------------------------------------

    def agregar_pendiente(self, pendiente: Pendiente) -> None:
        """Agrega un pendiente sin duplicar (mismo motivo y mismas opciones / mismo texto)."""
        if not any(self._mismo_pendiente(e.pendiente, pendiente) for e in self.entradas):
            self.entradas.append(_Entrada(pendiente=pendiente))

    def quitar_pendiente(self, pendiente: Pendiente) -> None:
        self.entradas = [
            e for e in self.entradas if not self._mismo_pendiente(e.pendiente, pendiente)
        ]

    @staticmethod
    def _mismo_pendiente(a: Pendiente | None, b: Pendiente) -> bool:
        if a is None or a.motivo != b.motivo:
            return False
        if b.motivo is MotivoRevision.PRODUCTO_AMBIGUO and b.opciones:
            return a.opciones == b.opciones
        return _normalizar(a.texto) == _normalizar(b.texto)


def _pendiente_no_convertible(sku: str, texto: str) -> Pendiente:
    return Pendiente(motivo=MotivoRevision.CANTIDAD_NO_CONVERTIBLE, texto=texto, opciones=[sku])


# ---------------------------------------------------------------------------
# Aplicación de las menciones
# ---------------------------------------------------------------------------


def _resolver(mencion: Mencion, datos: Datos) -> tuple[Resolucion, str | None, list[str]]:
    """Normaliza la resolución: nunca un sku fuera del catálogo (regla 4 de `CLAUDE.md`).

    - `sku` inexistente o vacío → `no_existe`.
    - `ambiguo`: opciones filtradas al catálogo, sin duplicados y ordenadas por sku (D10); sin
      opciones válidas → `no_existe`; con una sola → ese sku.
    """
    if mencion.resolucion is Resolucion.SKU:
        sku = (mencion.sku or "").strip()
        if datos.producto(sku) is None:
            return Resolucion.NO_EXISTE, None, []
        return Resolucion.SKU, sku, []
    if mencion.resolucion is Resolucion.AMBIGUO:
        opciones = sorted({o.strip() for o in mencion.opciones if datos.producto(o.strip())})
        if not opciones:
            return Resolucion.NO_EXISTE, None, []
        if len(opciones) == 1:
            return Resolucion.SKU, opciones[0], []
        return Resolucion.AMBIGUO, None, opciones
    return Resolucion.NO_EXISTE, None, []


def _texto(mencion: Mencion) -> str:
    return " ".join(mencion.texto.split())


def _aplicar(mencion: Mencion, acumulador: _Acumulador, datos: Datos) -> None:
    resolucion, sku, opciones = _resolver(mencion, datos)
    texto = _texto(mencion)
    accion = mencion.accion
    cantidad = 1 if mencion.cantidad is None else mencion.cantidad

    if resolucion is not Resolucion.SKU:
        motivo = (
            MotivoRevision.PRODUCTO_AMBIGUO
            if resolucion is Resolucion.AMBIGUO
            else MotivoRevision.PRODUCTO_NO_EXISTE
        )
        pendiente = Pendiente(motivo=motivo, texto=texto, opciones=opciones)
        if accion is Accion.QUITAR:
            # "sin las cocas": quita cualquiera de las opciones y el pendiente equivalente (D17).
            for opcion in opciones:
                acumulador.quitar_producto(opcion)
            acumulador.quitar_pendiente(pendiente)
        elif cantidad > 0 or accion is Accion.CORREGIR:
            acumulador.agregar_pendiente(pendiente)
        return

    assert sku is not None
    if accion is Accion.QUITAR:
        acumulador.quitar_producto(sku)
        return
    if cantidad <= 0:
        # D16: "agregar 0" no hace nada; "corregir a 0" equivale a quitar.
        if accion is Accion.CORREGIR:
            acumulador.quitar_producto(sku)
        return

    producto = datos.producto(sku)
    assert producto is not None
    unidades = convertir_a_unidades_de_venta(cantidad, mencion.unidad, producto)
    if unidades is None and not producto.disponible and not producto.sustituto_sku:
        # Agotado sin sustituto: la cantidad no importa, el pendiente será de agotado (D16).
        unidades = 1

    if accion is Accion.CORREGIR:
        nueva = (
            _Entrada(sku=sku, cantidad=unidades)
            if unidades is not None
            else _Entrada(sku=sku, pendiente=_pendiente_no_convertible(sku, texto))
        )
        acumulador.corregir_producto(sku, nueva)
    elif unidades is None:
        acumulador.agregar_no_convertible(sku, texto)
    else:
        acumulador.agregar_producto(sku, unidades)


def _agregar_lo_de_siempre(cliente: Cliente, acumulador: _Acumulador, datos: Datos) -> None:
    """Agrega las líneas del último pedido entregado, en el orden del historial (D06, D08).

    Sin pedidos entregados: pendiente `producto_ambiguo` "lo de siempre" sin opciones (D18).
    """
    pedido = datos.ultimo_pedido_entregado(cliente.cliente_id)
    if pedido is None:
        acumulador.agregar_pendiente(
            Pendiente(motivo=MotivoRevision.PRODUCTO_AMBIGUO, texto=TEXTO_LO_DE_SIEMPRE)
        )
        return
    for linea in pedido.lineas:
        if datos.producto(linea.sku) is not None:
            acumulador.agregar_producto(linea.sku, linea.cantidad)


# ---------------------------------------------------------------------------
# Entrega: sustitutos, agotados y líneas
# ---------------------------------------------------------------------------


def _entregar(
    entradas: list[_Entrada], datos: Datos
) -> tuple[dict[str, int], list[Sustitucion], list[Pendiente]]:
    """Convierte las entradas en cantidades por sku entregado, sustituciones y pendientes."""
    lineas: dict[str, int] = {}
    sustituciones: list[Sustitucion] = []
    pendientes: list[Pendiente] = []
    for entrada in entradas:
        if entrada.pendiente is not None:
            pendientes.append(entrada.pendiente)
            continue
        assert entrada.sku is not None
        producto = datos.producto(entrada.sku)
        assert producto is not None
        if producto.disponible:
            lineas[producto.sku] = lineas.get(producto.sku, 0) + entrada.cantidad
        elif producto.sustituto_sku and datos.producto(producto.sustituto_sku) is not None:
            sustituto = producto.sustituto_sku
            lineas[sustituto] = lineas.get(sustituto, 0) + entrada.cantidad
            sustituciones.append(
                Sustitucion(
                    sku_solicitado=producto.sku,
                    sku_entregado=sustituto,
                    cantidad=entrada.cantidad,
                )
            )
        else:
            pendientes.append(
                Pendiente(
                    motivo=MotivoRevision.AGOTADO_SIN_SUSTITUTO,
                    texto=producto.nombre,
                    opciones=[producto.sku],
                )
            )
    return lineas, sustituciones, pendientes


def _motivos(pendientes: list[Pendiente], excedido: bool) -> list[MotivoRevision]:
    """Motivos sin duplicados en orden de aparición; `credito_excedido` al final (D11)."""
    motivos: list[MotivoRevision] = []
    for pendiente in pendientes:
        if pendiente.motivo not in motivos:
            motivos.append(pendiente.motivo)
    if excedido:
        motivos.append(MotivoRevision.CREDITO_EXCEDIDO)
    return motivos


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------


def _fuera_de_alcance(cliente_id: str, fecha_hora: datetime, datos: Datos) -> RespuestaPedido:
    respuesta = RespuestaPedido(
        cliente_id=cliente_id,
        estado=EstadoPedido.FUERA_DE_ALCANCE,
        total=total([]),
        fecha_entrega=None,
        mensaje_cliente="",
    )
    return respuesta.model_copy(
        update={"mensaje_cliente": redactar_mensaje(respuesta, fecha_hora, datos)}
    )


def armar_pedido(
    cliente_id: str,
    fecha_hora: datetime,
    interpretacion: InterpretacionResuelta,
    datos: Datos,
) -> RespuestaPedido:
    """Arma la respuesta completa del contrato a partir de la interpretación resuelta.

    `fecha_hora` es la de la solicitud (último mensaje), nunca el reloj del sistema. Lanza
    `ErrorClienteInexistente` si el cliente no existe.
    """
    cliente = datos.cliente(cliente_id)
    if cliente is None:
        raise ErrorClienteInexistente(f"Cliente inexistente: {cliente_id!r}")
    if not interpretacion.es_pedido:
        return _fuera_de_alcance(cliente_id, fecha_hora, datos)

    acumulador = _Acumulador(datos)
    menciones = list(interpretacion.menciones)
    posicion = min(interpretacion.posicion_lo_de_siempre, len(menciones))
    for i in range(len(menciones) + 1):
        if interpretacion.lo_de_siempre and i == posicion:
            _agregar_lo_de_siempre(cliente, acumulador, datos)
        if i < len(menciones):
            _aplicar(menciones[i], acumulador, datos)

    cantidades, sustituciones, pendientes = _entregar(acumulador.entradas, datos)
    if not cantidades and not pendientes:
        # D19: es pedido pero no quedó nada (todo quitado o cantidades 0) → fuera de alcance.
        return _fuera_de_alcance(cliente_id, fecha_hora, datos)

    calculadas = [
        calcular_linea(datos.productos[sku], cantidad, datos.escalas)
        for sku, cantidad in cantidades.items()
    ]
    monto_total = total(calculadas)
    motivos = _motivos(pendientes, credito_excedido(cliente, monto_total))
    estado = EstadoPedido.REQUIERE_REVISION if motivos else EstadoPedido.CONFIRMADO
    respuesta = RespuestaPedido(
        cliente_id=cliente_id,
        estado=estado,
        lineas=[
            LineaPedido(
                sku=c.sku,
                cantidad=c.cantidad,
                precio_unitario=c.precio_unitario,
                descuento_pct=c.descuento_pct,
                subtotal=c.subtotal,
            )
            for c in calculadas
        ],
        total=monto_total,
        fecha_entrega=fecha_entrega(fecha_hora, cliente.dias_visita),
        sustituciones=sustituciones,
        pendientes=pendientes,
        motivos_revision=motivos,
        mensaje_cliente="",
    )
    return respuesta.model_copy(
        update={"mensaje_cliente": redactar_mensaje(respuesta, fecha_hora, datos)}
    )


__all__ = [
    "TEXTO_LO_DE_SIEMPRE",
    "ErrorClienteInexistente",
    "armar_pedido",
    "convertir_a_unidades_de_venta",
]
