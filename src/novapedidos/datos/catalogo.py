"""Carga y validación de los datos publicados (los 5 parquet de `DATOS_DIR`).

Este módulo solo **lee, valida y organiza** los datos en estructuras inmutables. No contiene
reglas de negocio (precios, descuentos, fechas, crédito): eso vive en `novapedidos.motor`.

API pública:

- `obtener_datos()`: datos del proceso, cargados una sola vez desde `DATOS_DIR` (cacheado).
- `cargar_datos(directorio)`: lee los parquet de un directorio (sin caché).
- `construir_datos(productos, sinonimos, precios_escala, clientes, historial)`: valida
  DataFrames ya leídos (útil en pruebas con datos sintéticos).
- `ErrorDatos`: cualquier archivo faltante, columna faltante o dato inválido.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

import pandas as pd

ARCHIVOS: dict[str, str] = {
    "productos": "productos.parquet",
    "sinonimos": "sinonimos.parquet",
    "precios_escala": "precios_escala.parquet",
    "clientes": "clientes.parquet",
    "historial": "historial_pedidos.parquet",
}

COLUMNAS: dict[str, tuple[str, ...]] = {
    "productos": (
        "sku",
        "nombre",
        "marca",
        "categoria",
        "presentacion",
        "unidad_venta",
        "unidades_por_empaque",
        "precio",
        "disponible",
        "sustituto_sku",
    ),
    "sinonimos": ("sku", "alias"),
    "precios_escala": ("categoria", "cantidad_minima", "descuento_pct"),
    "clientes": (
        "cliente_id",
        "nombre_tienda",
        "propietario",
        "municipio",
        "dias_visita",
        "limite_credito",
        "saldo_pendiente",
    ),
    "historial": ("pedido_id", "cliente_id", "fecha_entrega", "sku", "cantidad", "estado"),
}

#: Días de la semana en español; el índice coincide con `date.weekday()` (0 = lunes).
DIAS_SEMANA: tuple[str, ...] = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)

ESTADOS_HISTORIAL: frozenset[str] = frozenset({"entregado", "cancelado"})


class ErrorDatos(Exception):
    """Los datos publicados faltan o no cumplen lo esperado."""


# ---------------------------------------------------------------------------
# Estructuras inmutables
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Producto:
    """Un producto del catálogo (`productos.parquet`)."""

    sku: str
    nombre: str
    marca: str
    categoria: str
    presentacion: str
    unidad_venta: str
    unidades_por_empaque: int
    precio: Decimal
    disponible: bool
    sustituto_sku: str | None


@dataclass(frozen=True, slots=True)
class Escala:
    """Un escalón de descuento por categoría (`precios_escala.parquet`)."""

    cantidad_minima: int
    descuento_pct: int


@dataclass(frozen=True, slots=True)
class Cliente:
    """Un cliente (`clientes.parquet`). `dias_visita` son índices de `date.weekday()`."""

    cliente_id: str
    nombre_tienda: str
    propietario: str
    municipio: str
    dias_visita: tuple[int, ...]
    limite_credito: Decimal
    saldo_pendiente: Decimal

    @property
    def nombres_dias_visita(self) -> tuple[str, ...]:
        """Los días de visita en español (p. ej. `("martes", "viernes")`)."""
        return tuple(DIAS_SEMANA[d] for d in self.dias_visita)


@dataclass(frozen=True, slots=True)
class LineaHistorial:
    """Una línea de un pedido histórico."""

    sku: str
    cantidad: int


@dataclass(frozen=True, slots=True)
class PedidoHistorial:
    """Un pedido histórico con sus líneas en el orden del archivo."""

    pedido_id: str
    cliente_id: str
    fecha_entrega: date
    estado: str
    lineas: tuple[LineaHistorial, ...]


@dataclass(frozen=True, slots=True)
class Datos:
    """Todos los datos publicados, validados e indexados para el motor.

    - `productos`: sku → `Producto`.
    - `alias`: alias normalizado (minúsculas, sin espacios sobrantes) → sku.
    - `escalas`: categoría → escalones ordenados por `cantidad_minima` ascendente.
    - `clientes`: cliente_id → `Cliente`.
    - `pedidos`: cliente_id → pedidos ordenados por (`fecha_entrega`, `pedido_id`).
    """

    productos: Mapping[str, Producto]
    alias: Mapping[str, str]
    escalas: Mapping[str, tuple[Escala, ...]]
    clientes: Mapping[str, Cliente]
    pedidos: Mapping[str, tuple[PedidoHistorial, ...]]

    def producto(self, sku: str) -> Producto | None:
        """Devuelve el producto con ese sku o `None`."""
        return self.productos.get(sku)

    def cliente(self, cliente_id: str) -> Cliente | None:
        """Devuelve el cliente con ese id o `None`."""
        return self.clientes.get(cliente_id)

    def sku_por_alias(self, texto: str) -> str | None:
        """Busca un alias exacto (tras normalizar a minúsculas y espacios simples)."""
        return self.alias.get(normalizar_alias(texto))

    def alias_de(self, sku: str) -> tuple[str, ...]:
        """Todos los alias conocidos de un sku."""
        return tuple(a for a, s in self.alias.items() if s == sku)

    def ultimo_pedido_entregado(self, cliente_id: str) -> PedidoHistorial | None:
        """El último pedido con estado `entregado` del cliente (los cancelados no cuentan).

        "Último" = mayor `fecha_entrega`; en empate, mayor `pedido_id`.
        """
        for pedido in reversed(self.pedidos.get(cliente_id, ())):
            if pedido.estado == "entregado":
                return pedido
        return None


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def normalizar_alias(texto: str) -> str:
    """Minúsculas y espacios simples (no quita tildes: los alias publicados no las llevan)."""
    return " ".join(str(texto).strip().lower().split())


def _sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


_DIA_A_INDICE: dict[str, int] = {_sin_tildes(d): i for i, d in enumerate(DIAS_SEMANA)}


def parsear_dias_visita(valor: str) -> tuple[int, ...]:
    """Convierte `"lunes,miércoles,viernes"` en `(0, 2, 4)` (acepta con o sin tildes).

    Lanza `ErrorDatos` si el texto está vacío o tiene un día desconocido.
    """
    if not isinstance(valor, str) or not valor.strip():
        raise ErrorDatos(f"dias_visita vacío o inválido: {valor!r}")
    indices: set[int] = set()
    for parte in valor.split(","):
        clave = _sin_tildes(parte.strip().lower())
        if clave not in _DIA_A_INDICE:
            raise ErrorDatos(f"Día de visita desconocido {parte.strip()!r} en {valor!r}")
        indices.add(_DIA_A_INDICE[clave])
    return tuple(sorted(indices))


def _a_decimal(valor: object, campo: str, ident: str) -> Decimal:
    try:
        numero = Decimal(str(valor))
    except Exception as exc:  # noqa: BLE001 - cualquier valor no numérico
        raise ErrorDatos(f"{campo} no numérico para {ident}: {valor!r}") from exc
    if not numero.is_finite():
        raise ErrorDatos(f"{campo} no finito para {ident}: {valor!r}")
    return numero


def _texto(valor: object) -> str | None:
    """Convierte a `str` sin espacios sobrantes; NaN/None/"" → None."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)) or valor is pd.NA:
        return None
    texto = str(valor).strip()
    return texto or None


def _verificar_columnas(nombre: str, df: pd.DataFrame) -> None:
    faltantes = [c for c in COLUMNAS[nombre] if c not in df.columns]
    if faltantes:
        raise ErrorDatos(f"{ARCHIVOS[nombre]}: faltan columnas {faltantes}")
    nulos = [c for c in COLUMNAS[nombre] if c != "sustituto_sku" and df[c].isna().any()]
    if nulos:
        raise ErrorDatos(f"{ARCHIVOS[nombre]}: valores vacíos en columnas {nulos}")


def _entero(valor: object, campo: str, ident: str) -> int:
    numero = _a_decimal(valor, campo, ident)
    if numero != numero.to_integral_value():
        raise ErrorDatos(f"{campo} debe ser entero para {ident}: {valor!r}")
    return int(numero)


# ---------------------------------------------------------------------------
# Construcción y validación
# ---------------------------------------------------------------------------


def _construir_productos(df: pd.DataFrame) -> dict[str, Producto]:
    _verificar_columnas("productos", df)
    productos: dict[str, Producto] = {}
    for fila in df.to_dict("records"):
        sku = _texto(fila["sku"])
        if sku is None:
            raise ErrorDatos("productos.parquet: hay un sku vacío")
        if sku in productos:
            raise ErrorDatos(f"productos.parquet: sku duplicado {sku!r}")
        unidades = _entero(fila["unidades_por_empaque"], "unidades_por_empaque", sku)
        if unidades <= 0:
            raise ErrorDatos(f"unidades_por_empaque debe ser > 0 para {sku}")
        precio = _a_decimal(fila["precio"], "precio", sku)
        if precio <= 0:
            raise ErrorDatos(f"precio debe ser > 0 para {sku}")
        disponible = fila["disponible"]
        if not pd.api.types.is_bool(disponible):
            raise ErrorDatos(f"disponible debe ser booleano para {sku}: {disponible!r}")
        productos[sku] = Producto(
            sku=sku,
            nombre=_texto(fila["nombre"]) or "",
            marca=_texto(fila["marca"]) or "",
            categoria=_texto(fila["categoria"]) or "",
            presentacion=_texto(fila["presentacion"]) or "",
            unidad_venta=_texto(fila["unidad_venta"]) or "",
            unidades_por_empaque=unidades,
            precio=precio,
            disponible=bool(disponible),
            sustituto_sku=_texto(fila["sustituto_sku"]),
        )
    if not productos:
        raise ErrorDatos("productos.parquet está vacío")

    for p in productos.values():
        if p.sustituto_sku is None:
            continue
        sustituto = productos.get(p.sustituto_sku)
        if sustituto is None:
            raise ErrorDatos(f"El sustituto {p.sustituto_sku!r} de {p.sku} no existe")
        if sustituto.sku == p.sku:
            raise ErrorDatos(f"{p.sku} no puede ser su propio sustituto")
        if not sustituto.disponible:
            raise ErrorDatos(f"El sustituto {sustituto.sku} de {p.sku} no está disponible")
    return productos


def _construir_alias(df: pd.DataFrame, productos: Mapping[str, Producto]) -> dict[str, str]:
    _verificar_columnas("sinonimos", df)
    alias: dict[str, str] = {}
    for fila in df.to_dict("records"):
        sku = _texto(fila["sku"])
        clave = normalizar_alias(fila["alias"])
        if not clave:
            raise ErrorDatos(f"sinonimos.parquet: alias vacío para {sku!r}")
        if sku not in productos:
            raise ErrorDatos(
                f"sinonimos.parquet: el alias {clave!r} apunta a sku inexistente {sku!r}"
            )
        if alias.get(clave, sku) != sku:
            raise ErrorDatos(
                f"sinonimos.parquet: el alias {clave!r} apunta a {alias[clave]} y a {sku}"
            )
        alias[clave] = sku
    return alias


def _construir_escalas(
    df: pd.DataFrame, productos: Mapping[str, Producto]
) -> dict[str, tuple[Escala, ...]]:
    _verificar_columnas("precios_escala", df)
    categorias = {p.categoria for p in productos.values()}
    por_categoria: dict[str, list[Escala]] = defaultdict(list)
    for fila in df.to_dict("records"):
        categoria = _texto(fila["categoria"]) or ""
        if categoria not in categorias:
            raise ErrorDatos(f"precios_escala.parquet: categoría desconocida {categoria!r}")
        minima = _entero(fila["cantidad_minima"], "cantidad_minima", categoria)
        pct = _entero(fila["descuento_pct"], "descuento_pct", categoria)
        if minima <= 0:
            raise ErrorDatos(f"precios_escala.parquet: cantidad_minima debe ser > 0 ({categoria})")
        if not 0 <= pct < 100:
            raise ErrorDatos(f"precios_escala.parquet: descuento_pct fuera de rango ({categoria})")
        if any(e.cantidad_minima == minima for e in por_categoria[categoria]):
            raise ErrorDatos(f"precios_escala.parquet: escalón duplicado {categoria} ≥ {minima}")
        por_categoria[categoria].append(Escala(minima, pct))
    return {
        cat: tuple(sorted(escalas, key=lambda e: e.cantidad_minima))
        for cat, escalas in por_categoria.items()
    }


def _construir_clientes(df: pd.DataFrame) -> dict[str, Cliente]:
    _verificar_columnas("clientes", df)
    clientes: dict[str, Cliente] = {}
    for fila in df.to_dict("records"):
        cid = _texto(fila["cliente_id"])
        if cid is None:
            raise ErrorDatos("clientes.parquet: hay un cliente_id vacío")
        if cid in clientes:
            raise ErrorDatos(f"clientes.parquet: cliente_id duplicado {cid!r}")
        try:
            dias = parsear_dias_visita(fila["dias_visita"])
        except ErrorDatos as exc:
            raise ErrorDatos(f"clientes.parquet ({cid}): {exc}") from exc
        limite = _a_decimal(fila["limite_credito"], "limite_credito", cid)
        saldo = _a_decimal(fila["saldo_pendiente"], "saldo_pendiente", cid)
        if limite < 0 or saldo < 0:
            raise ErrorDatos(f"clientes.parquet: límite o saldo negativo para {cid}")
        clientes[cid] = Cliente(
            cliente_id=cid,
            nombre_tienda=_texto(fila["nombre_tienda"]) or "",
            propietario=_texto(fila["propietario"]) or "",
            municipio=_texto(fila["municipio"]) or "",
            dias_visita=dias,
            limite_credito=limite,
            saldo_pendiente=saldo,
        )
    return clientes


def _a_fecha(valor: object, ident: str) -> date:
    try:
        marca = pd.Timestamp(valor)
    except Exception as exc:  # noqa: BLE001
        raise ErrorDatos(
            f"historial_pedidos.parquet: fecha inválida en {ident}: {valor!r}"
        ) from exc
    if pd.isna(marca):
        raise ErrorDatos(f"historial_pedidos.parquet: fecha vacía en {ident}")
    return marca.date()


def _construir_pedidos(
    df: pd.DataFrame, productos: Mapping[str, Producto], clientes: Mapping[str, Cliente]
) -> dict[str, tuple[PedidoHistorial, ...]]:
    _verificar_columnas("historial", df)
    cabeceras: dict[str, tuple[str, date, str]] = {}
    lineas: dict[str, list[LineaHistorial]] = defaultdict(list)
    for fila in df.to_dict("records"):
        pid = _texto(fila["pedido_id"]) or ""
        cid = _texto(fila["cliente_id"]) or ""
        sku = _texto(fila["sku"]) or ""
        estado = (_texto(fila["estado"]) or "").lower()
        fecha = _a_fecha(fila["fecha_entrega"], pid)
        cantidad = _entero(fila["cantidad"], "cantidad", pid)
        if cid not in clientes:
            raise ErrorDatos(f"historial_pedidos.parquet: cliente inexistente {cid!r} en {pid}")
        if sku not in productos:
            raise ErrorDatos(f"historial_pedidos.parquet: sku inexistente {sku!r} en {pid}")
        if estado not in ESTADOS_HISTORIAL:
            raise ErrorDatos(f"historial_pedidos.parquet: estado desconocido {estado!r} en {pid}")
        if cantidad <= 0:
            raise ErrorDatos(f"historial_pedidos.parquet: cantidad debe ser > 0 en {pid}")
        cabecera = (cid, fecha, estado)
        if cabeceras.setdefault(pid, cabecera) != cabecera:
            raise ErrorDatos(
                f"historial_pedidos.parquet: {pid} mezcla cliente, fecha o estado entre líneas"
            )
        lineas[pid].append(LineaHistorial(sku=sku, cantidad=cantidad))

    por_cliente: dict[str, list[PedidoHistorial]] = defaultdict(list)
    for pid, (cid, fecha, estado) in cabeceras.items():
        por_cliente[cid].append(
            PedidoHistorial(
                pedido_id=pid,
                cliente_id=cid,
                fecha_entrega=fecha,
                estado=estado,
                lineas=tuple(lineas[pid]),
            )
        )
    return {
        cid: tuple(sorted(pedidos, key=lambda p: (p.fecha_entrega, p.pedido_id)))
        for cid, pedidos in por_cliente.items()
    }


def construir_datos(
    productos: pd.DataFrame,
    sinonimos: pd.DataFrame,
    precios_escala: pd.DataFrame,
    clientes: pd.DataFrame,
    historial: pd.DataFrame,
) -> Datos:
    """Valida los cinco DataFrames y arma la estructura inmutable `Datos`.

    Lanza `ErrorDatos` con un mensaje claro ante columnas faltantes, sku duplicados,
    sustitutos inexistentes o agotados, alias a sku inexistentes, días de visita inválidos, etc.
    """
    prods = _construir_productos(productos)
    alias = _construir_alias(sinonimos, prods)
    escalas = _construir_escalas(precios_escala, prods)
    clis = _construir_clientes(clientes)
    pedidos = _construir_pedidos(historial, prods, clis)
    return Datos(
        productos=MappingProxyType(prods),
        alias=MappingProxyType(alias),
        escalas=MappingProxyType(escalas),
        clientes=MappingProxyType(clis),
        pedidos=MappingProxyType(pedidos),
    )


def cargar_datos(directorio: Path | str) -> Datos:
    """Lee y valida los 5 parquet de `directorio` (sin caché)."""
    base = Path(directorio)
    if not base.is_dir():
        raise ErrorDatos(f"No existe el directorio de datos {base.resolve()} (DATOS_DIR)")
    marcos: dict[str, pd.DataFrame] = {}
    for clave, archivo in ARCHIVOS.items():
        ruta = base / archivo
        if not ruta.is_file():
            raise ErrorDatos(f"Falta el archivo de datos {ruta}")
        try:
            marcos[clave] = pd.read_parquet(ruta)
        except Exception as exc:  # noqa: BLE001
            raise ErrorDatos(f"No se pudo leer {ruta}: {exc}") from exc
    return construir_datos(
        marcos["productos"],
        marcos["sinonimos"],
        marcos["precios_escala"],
        marcos["clientes"],
        marcos["historial"],
    )


@lru_cache(maxsize=1)
def obtener_datos() -> Datos:
    """Datos del proceso: se cargan una sola vez desde `DATOS_DIR` y se reutilizan."""
    from novapedidos.config import obtener_configuracion

    return cargar_datos(obtener_configuracion().datos_dir)
