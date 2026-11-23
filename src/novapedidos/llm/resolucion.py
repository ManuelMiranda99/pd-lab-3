"""Resolución de productos en código, después del LLM (`DECISIONES.md` D31).

El LLM ya elige `sku` / `ambiguo` / `no_existe` con el catálogo en el prompt y un enum de sku
válidos. Aquí se corrigen, de forma conservadora y determinista, los errores típicos de un
modelo barato:

1. **sku inválido** (no debería pasar por el enum): pasa a `no_existe`.
2. **Alias exacto**: si el texto es exactamente un alias publicado (sin tildes, minúsculas), se
   usa ese sku aunque el LLM haya dicho `ambiguo` o `no_existe`.
3. **`no_existe` que sí existe**: si todas las palabras del texto aparecen en uno o más
   productos (nombre, marca, presentación o alias; singular/plural y tildes normalizados), o si
   el texto se parece muchísimo (`rapidfuzz` ≥ `UMBRAL_DIFUSO`) a un único producto, se usa ese
   sku (o `ambiguo` si son varios).
4. **Término genérico resuelto a un sku**: si el LLM eligió un sku pero el texto, palabra por
   palabra, encaja igual de bien en **varios** productos ("aceite", "naranjitas", "pasta"),
   se marca `ambiguo` con todas esas opciones (política §1: no elegir).
5. **Ambiguo incompleto**: a las opciones del LLM se suman los demás productos que encajan
   palabra por palabra (así "aceite" no queda solo con A06, lo que el motor convertiría en una
   elección).

Nada de esto inventa: todo sku sale del catálogo y, ante la duda, se deja lo del LLM.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from novapedidos.datos import Datos
from novapedidos.motor.modelos import (
    Accion,
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
)

#: Similitud mínima (0–100) para aceptar un `no_existe` del LLM como un producto conocido.
UMBRAL_DIFUSO = 90.0
#: Ventaja mínima del mejor producto sobre el segundo (otro sku) en la búsqueda difusa.
MARGEN_DIFUSO = 5.0

_VACIAS = frozenset(
    [
        "de",
        "del",
        "la",
        "el",
        "los",
        "las",
        "y",
        "con",
        "en",
        "para",
        "un",
        "una",
        "unos",
        "unas",
        "mi",
        "su",
        "al",
        "por",
        "favor",
    ]
)
#: Palabras de empaque/unidad que pueden colarse en el texto y no distinguen productos.
_UNIDADES = frozenset(
    [
        "fardo",
        "fardito",
        "caja",
        "cajita",
        "display",
        "quintal",
        "botella",
        "botellita",
        "lata",
        "latita",
        "bolsa",
        "bolsita",
        "unidad",
        "paquete",
        "paquetito",
        "docena",
        "sobre",
    ]
)


def sin_tildes(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFD", texto)
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes, solo letras/dígitos/punto y espacios simples."""
    limpio = re.sub(r"[^a-z0-9.ñ ]+", " ", sin_tildes(texto.lower()).replace("ñ", "n"))
    return " ".join(limpio.split())


#: Palabras informales observadas que nombran una familia del catálogo (exploración F1):
#: "cocas" = las kolas, "juguitos" = los jugos. Solo amplían la búsqueda palabra por palabra.
SINONIMOS_PALABRA: dict[str, str] = {
    "coca": "kola",
    "cola": "kola",
    "juguito": "jugo",
    "jugito": "jugo",
}


def formas(palabra: str) -> set[str]:
    """La palabra, sus posibles singulares ("cajas" → caja, "papeles" → papel) y su
    equivalente informal (`SINONIMOS_PALABRA`)."""
    resultado = {palabra}
    if len(palabra) > 3 and palabra.endswith("s"):
        resultado.add(palabra[:-1])
    if len(palabra) > 4 and palabra.endswith("es"):
        resultado.add(palabra[:-2])
    resultado |= {SINONIMOS_PALABRA[f] for f in resultado if f in SINONIMOS_PALABRA}
    return resultado


def palabras(texto: str, *, quitar_unidades: bool = False) -> set[str]:
    """Palabras de contenido normalizadas (sin vacías y, si se pide, sin unidades)."""
    resultado = set()
    for p in normalizar(texto).split():
        p = p.strip(".")
        if not p or p in _VACIAS:
            continue
        if quitar_unidades and formas(p) & _UNIDADES:
            continue
        resultado.add(p)
    return resultado


def _vocabulario(texto: str) -> set[str]:
    return {f for p in palabras(texto) for f in formas(p)}


#: Palabras con las que el cliente pide unidades sueltas o docenas (raíces normalizadas).
_PALABRAS_SUELTAS = (
    "botell",
    "lata",
    "latit",
    "bols",
    "sobre",
    "unidad",
    "pieza",
    "docena",
    "sueltas",
    "paquetit",
)


def verificar_unidades(menciones: Sequence[Mencion], mensajes: Sequence[str]) -> list[Mencion]:
    """Si en la conversación no aparece ninguna palabra de unidad suelta o docena, una mención
    con `unidad_suelta`/`docena` es un error del modelo (p. ej. tomó "500 ml" por unidades):
    vuelve a `venta`. Si aparece alguna, se respeta lo que dijo el modelo."""
    texto = normalizar(" ".join(mensajes))
    hay_sueltas = any(p in texto for p in _PALABRAS_SUELTAS)
    hay_docena = "docena" in texto
    resultado = []
    for m in menciones:
        if (m.unidad == Unidad.UNIDAD_SUELTA and not hay_sueltas) or (
            m.unidad == Unidad.DOCENA and not hay_docena
        ):
            m = m.model_copy(update={"unidad": Unidad.VENTA})
        elif m.unidad == Unidad.VENTA and m.cantidad is not None and _dijo_sueltas(m, texto):
            m = m.model_copy(update={"unidad": Unidad.UNIDAD_SUELTA})
        resultado.append(m)
    return resultado


_PATRON_SUELTAS = r"(botellas?|latas?|latitas?|bolsas?|bolsitas?|sobres?|unidades|piezas?)"


def _dijo_sueltas(m: Mencion, texto_normalizado: str) -> bool:
    """ "48 bolsas de detergente espumoso": el número de la mención va seguido de una palabra
    de unidad suelta y luego del texto del producto (el modelo dejó `venta` por error)."""
    inicio = normalizar(m.texto).split()[:2]
    if not inicio:
        return False
    patron = rf"\b{m.cantidad}\s+{_PATRON_SUELTAS}\s+(de\s+)?" + r"\s+".join(map(re.escape, inicio))
    return re.search(patron, texto_normalizado) is not None


@dataclass
class ResolvedorProductos:
    """Índices del catálogo para la resolución post-LLM (se construye una vez por catálogo)."""

    datos: Datos
    _palabras_sku: dict[str, set[str]] = field(init=False)
    _alias: dict[str, str] = field(init=False)
    _nombres_difusos: list[tuple[str, str]] = field(init=False)

    def __post_init__(self) -> None:
        self._palabras_sku = {}
        self._alias = {}
        self._nombres_difusos = []
        for sku, p in self.datos.productos.items():
            alias = self.datos.alias_de(sku)
            fuente = " ".join([p.nombre, p.marca, p.presentacion, *alias])
            self._palabras_sku[sku] = _vocabulario(fuente)
            for nombre in (p.nombre, *alias):
                self._nombres_difusos.append((normalizar(nombre), sku))
            for a in alias:
                self._alias[normalizar(a)] = sku

    # ------------------------------------------------------------------ búsquedas

    def por_alias(self, texto: str) -> str | None:
        """sku cuyo alias (o nombre) coincide exactamente con el texto normalizado."""
        clave = normalizar(texto)
        if clave in self._alias:
            return self._alias[clave]
        for sku, p in self.datos.productos.items():
            if normalizar(p.nombre) == clave:
                return sku
        return None

    def candidatos(self, texto: str) -> list[str]:
        """sku cuyo vocabulario contiene **todas** las palabras de contenido del texto."""
        buscadas = palabras(texto, quitar_unidades=True)
        if not buscadas:
            return []
        return sorted(
            sku
            for sku, vocab in self._palabras_sku.items()
            if all(formas(p) & vocab for p in buscadas)
        )

    def difuso(self, texto: str) -> str | None:
        """sku único muy parecido al texto (errores de ortografía), o `None`."""
        clave = normalizar(texto)
        if len(clave) < 4:
            return None
        resultados = process.extract(
            clave, [n for n, _ in self._nombres_difusos], scorer=fuzz.ratio, limit=10
        )
        mejor: dict[str, float] = {}
        for _, puntaje, indice in resultados:
            sku = self._nombres_difusos[indice][1]
            mejor[sku] = max(mejor.get(sku, 0.0), puntaje)
        if not mejor:
            return None
        orden = sorted(mejor.items(), key=lambda kv: kv[1], reverse=True)
        sku, puntaje = orden[0]
        segundo = orden[1][1] if len(orden) > 1 else 0.0
        if puntaje >= UMBRAL_DIFUSO and puntaje - segundo >= MARGEN_DIFUSO:
            return sku
        return None

    # ------------------------------------------------------------------ resolución

    def resolver_mencion(self, m: Mencion) -> Mencion:
        productos = self.datos.productos
        texto = m.texto

        if m.resolucion == Resolucion.SKU:
            if m.sku not in productos:
                return m.model_copy(update={"resolucion": Resolucion.NO_EXISTE, "sku": None})
            alias = self.por_alias(texto)
            if alias is not None:
                return m if alias == m.sku else m.model_copy(update={"sku": alias})
            cands = self.candidatos(texto)
            if len(cands) == 1 and m.sku not in cands:
                # El texto nombra claramente otro producto ("energia max" → B11, no S01).
                return m.model_copy(update={"sku": cands[0]})
            if len(cands) >= 2:
                # Término genérico: el texto encaja igual en varios productos.
                return m.model_copy(
                    update={"resolucion": Resolucion.AMBIGUO, "sku": None, "opciones": cands}
                )
            return m

        alias = self.por_alias(texto)
        if alias is not None:
            return m.model_copy(update={"resolucion": Resolucion.SKU, "sku": alias, "opciones": []})

        if m.resolucion == Resolucion.AMBIGUO:
            validas = [o for o in m.opciones if o in productos]
            cands = self.candidatos(texto)
            if len(cands) >= 2:
                validas = sorted(set(validas) | set(cands))
            return m.model_copy(update={"opciones": validas, "sku": None})

        # no_existe: ¿el texto nombra en realidad productos del catálogo?
        cands = self.candidatos(texto)
        if len(cands) == 1:
            return m.model_copy(
                update={"resolucion": Resolucion.SKU, "sku": cands[0], "opciones": []}
            )
        if len(cands) >= 2:
            return m.model_copy(
                update={"resolucion": Resolucion.AMBIGUO, "sku": None, "opciones": cands}
            )
        parecido = self.difuso(texto)
        if parecido is not None:
            return m.model_copy(
                update={"resolucion": Resolucion.SKU, "sku": parecido, "opciones": []}
            )
        return m.model_copy(update={"sku": None, "opciones": []})

    def anclar_al_mensaje(self, m: Mencion, mensajes: Sequence[str]) -> Mencion:
        """Si el LLM **reescribió** el texto (palabras que el cliente no dijo, p. ej.
        "naranjitas" → "naranjita 600"), se re-resuelve solo con las palabras que sí aparecen
        en la conversación. Entre los candidatos se elige el único cuyas palabras distintivas
        no numéricas (p. ej. "instantaneo") aparecen en la conversación; si no hay uno solo,
        queda `ambiguo`. Si el texto es literal, no se toca."""
        if m.resolucion != Resolucion.SKU or m.accion == Accion.QUITAR:
            return m
        fichas = [f.strip(".") for f in normalizar(" ".join(mensajes)).split()]
        formas_fichas = [formas(f) for f in fichas]
        todas = set().union(*formas_fichas) if formas_fichas else set()
        buscadas = [
            p for p in normalizar(m.texto).split() if p in palabras(m.texto, quitar_unidades=True)
        ]
        conservadas = [
            p
            for p in buscadas
            if formas(p) & todas or (len(p) >= 4 and any(fuzz.ratio(p, f) >= 80 for f in fichas))
        ]
        if not conservadas or len(conservadas) == len(buscadas):
            return m
        cands = self.candidatos(" ".join(conservadas))
        if not cands:
            return m
        literal = []
        for p in conservadas:
            for f, fs in zip(fichas, formas_fichas, strict=True):
                if formas(p) & fs:
                    literal.append(f)
                    break
        texto = " ".join(literal)
        if len(cands) > 1:
            distintivas = {
                sku: {
                    w
                    for w in self._palabras_sku[sku]
                    if not any(c.isdigit() for c in w)
                    and all(w not in self._palabras_sku[o] for o in cands if o != sku)
                }
                for sku in cands
            }
            presentes = [sku for sku in cands if distintivas[sku] & todas]
            if len(presentes) == 1:
                cands = presentes
        if len(cands) == 1:
            return m.model_copy(update={"texto": texto, "sku": cands[0], "opciones": []})
        return m.model_copy(
            update={
                "texto": texto,
                "resolucion": Resolucion.AMBIGUO,
                "sku": None,
                "opciones": cands,
            }
        )

    def resolver(
        self, interpretacion: InterpretacionResuelta, mensajes: Sequence[str] | None = None
    ) -> InterpretacionResuelta:
        """Aplica `resolver_mencion` a cada mención y, si se dan los mensajes, el anclaje al
        texto literal, la verificación de unidades y la detección de correcciones."""
        menciones = [self.resolver_mencion(m) for m in interpretacion.menciones]
        if mensajes is not None:
            menciones = [self.anclar_al_mensaje(m, mensajes) for m in menciones]
            menciones = verificar_unidades(menciones, mensajes)
            menciones = detectar_correcciones(menciones, mensajes)
        return interpretacion.model_copy(update={"menciones": menciones})


_MARCAS_CORRECCION = re.compile(
    r"\b(mejor|corrijo|corrige|que sean|perdon|ah no|en vez|cambie|cambio)\b"
)


def detectar_correcciones(menciones: Sequence[Mencion], mensajes: Sequence[str]) -> list[Mencion]:
    """Una mención `agregar` de un sku **ya pedido** que en la conversación va precedida (a
    menos de 40 caracteres) por una marca de corrección ("Mejor 6 fardos de sal pacifico")
    es una corrección que el modelo no marcó: pasa a `corregir`. Sin marca, se suma (§2)."""
    texto = normalizar(" ".join(mensajes))
    vistos: set[str] = set()
    resultado = []
    for m in menciones:
        if m.resolucion == Resolucion.SKU and m.sku:
            if m.accion == Accion.AGREGAR and m.sku in vistos and m.cantidad is not None:
                inicio = normalizar(m.texto).split()[:2]
                if inicio:
                    patron = r"\s+".join(map(re.escape, inicio))
                    posiciones = [x.start() for x in re.finditer(patron, texto)]
                    if posiciones:
                        ventana = texto[max(0, posiciones[-1] - 40) : posiciones[-1]]
                        if _MARCAS_CORRECCION.search(ventana):
                            m = m.model_copy(update={"accion": Accion.CORREGIR})
            vistos.add(m.sku)
        resultado.append(m)
    return resultado


__all__ = [
    "MARGEN_DIFUSO",
    "SINONIMOS_PALABRA",
    "UMBRAL_DIFUSO",
    "ResolvedorProductos",
    "normalizar",
    "detectar_correcciones",
    "formas",
    "palabras",
    "sin_tildes",
    "verificar_unidades",
]
