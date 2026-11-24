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
        "sobrecito",
        "pieza",
        "piecita",
        "envase",
        "frasco",
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
    # Presentaciones dichas en letras (audios, D40): "kola seiscientos" = "kola 600".
    "seiscientos": "600",
    "quinientos": "500",
    "setecientos": "750",
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
    "piecit",
    "docena",
    "sueltas",
    "paquetit",
    "envase",
    "frasc",
)

#: Unidades sueltas con sus diminutivos ("botellitas", "sobrecitos", "latitas"): una sola
#: fuente para todos los patrones (antes "botellitas" no estaba y se tomaba como fardos).
_UNIDADES_SUELTA_TXT = (
    r"botell(?:it)?as?|lat(?:it)?as?|bols(?:it)?as?|sobre(?:cito)?s?|unidad(?:es)?|"
    r"piez(?:it)?as?|piecitas?|paquetit[oa]s?|envases?|frascos?"
)


def verificar_unidades(menciones: Sequence[Mencion], mensajes: Sequence[str]) -> list[Mencion]:
    """Si en la conversación no aparece ninguna palabra de unidad suelta o docena, una mención
    con `unidad_suelta`/`docena` es un error del modelo (p. ej. tomó "500 ml" por unidades):
    vuelve a `venta`. Si aparece alguna, una mención `unidad_suelta` con cantidad exige que
    **ese** número vaya seguido de una palabra de unidad suelta ("48 bolsas") o sea la media
    docena; si no ("4 de jabon lavandero" junto a "48 bolsas de detergente"), es `venta`."""
    texto = normalizar(" ".join(mensajes))
    hay_sueltas = any(p in texto for p in _PALABRAS_SUELTAS)
    hay_docena = "docena" in texto
    resultado = []
    for m in menciones:
        sin_respaldo = (
            m.unidad == Unidad.UNIDAD_SUELTA
            and m.cantidad is not None
            and not _numero_con_suelta(m.cantidad, texto)
        )
        if (m.unidad == Unidad.UNIDAD_SUELTA and (not hay_sueltas or sin_respaldo)) or (
            m.unidad == Unidad.DOCENA and not hay_docena
        ):
            m = m.model_copy(update={"unidad": Unidad.VENTA})
        elif m.unidad == Unidad.VENTA and m.cantidad is not None and _dijo_sueltas(m, texto):
            m = m.model_copy(update={"unidad": Unidad.UNIDAD_SUELTA})
        resultado.append(m)
    return resultado


_PATRON_SUELTAS = rf"(?:{_UNIDADES_SUELTA_TXT})"
#: Antes de una cantidad no puede ir letra, dígito, punto, coma ni barra ("1/2", "2.5").
_ANTES_NUMERO = r"(?<![\w./,])"


def _numero_con_suelta(cantidad: int, texto_normalizado: str) -> bool:
    """El número (en cifras o letras) va seguido de una palabra de unidad suelta, o es la
    "media docena" (6)."""
    if cantidad == 6 and "media docena" in texto_normalizado:
        return True
    escritos = [str(cantidad)] + [p for p, n in NUMEROS_EN_LETRAS.items() if n == cantidad]
    numero = "|".join(map(re.escape, escritos))
    patron = rf"{_ANTES_NUMERO}({numero})\s+(?:mas\s+)?{_PATRON_SUELTAS}\b"
    return re.search(patron, texto_normalizado) is not None


def _dijo_sueltas(m: Mencion, texto_normalizado: str) -> bool:
    """ "48 bolsas de detergente espumoso": el número de la mención va seguido de una palabra
    de unidad suelta y luego del texto del producto (el modelo dejó `venta` por error)."""
    inicio = normalizar(m.texto).split()[:2]
    if not inicio:
        return False
    patron = rf"{_ANTES_NUMERO}{m.cantidad}\s+{_PATRON_SUELTAS}\s+(de\s+)?" + r"\s+".join(
        map(re.escape, inicio)
    )
    return re.search(patron, texto_normalizado) is not None


#: Números escritos con letras que usan los tenderos (normalizados, sin tildes).
NUMEROS_EN_LETRAS: dict[str, int] = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "veinte": 20,
    "veinticuatro": 24,
    "treinta": 30,
    "cuarenta": 40,
    "cincuenta": 50,
}
_UNIDADES_VENTA_TXT = r"fardos?|farditos?|cajas?|cajitas?|displays?|quintal(?:es)?|paquetes?"
_PATRON_CANTIDAD = (
    _ANTES_NUMERO + r"(?P<n>\d+|media|" + "|".join(NUMEROS_EN_LETRAS) + r") +"
    r"(?:(?P<u>docenas?|" + _UNIDADES_VENTA_TXT + "|" + _UNIDADES_SUELTA_TXT + r") +)?"
    r"(?:de +)?(?:(?:el|la|los|las) +)?"
)


def normalizar_suave(texto: str) -> str:
    """Minúsculas y sin tildes, pero **conserva** la puntuación y los saltos de línea, para que
    un patrón no cruce de una línea o lista a otra ("mani: 4\n- sopa rapida")."""
    return re.sub(r"[ \t]+", " ", sin_tildes(texto.lower()).replace("ñ", "n"))


def _numeros_de(texto: str) -> set[int]:
    """Números de la conversación, en cifras ("16.", "48") o en letras ("nueve")."""
    fichas = re.findall(r"[a-z]+|\d+(?:\.\d+)?", normalizar(texto))
    numeros = {int(f) for f in fichas if f.isdigit()}
    numeros |= {NUMEROS_EN_LETRAS[f] for f in fichas if f in NUMEROS_EN_LETRAS}
    return numeros


def _cantidad_en_mensaje(m: Mencion, texto_suave: str) -> tuple[int, Unidad] | None:
    """Cantidad y unidad que el mensaje pone **justo antes** del producto ("2 docenas de jabon
    rosa", "2 cajas de cloro", "media docena de cloro"). `None` si no hay o si hay varias
    lecturas distintas (el producto aparece con cantidades diferentes)."""
    inicio = palabras_inicio(m.texto)
    if not inicio:
        return None
    patron = _PATRON_CANTIDAD + _unir_palabras(inicio) + r"\b"
    lecturas = set()
    for coincidencia in re.finditer(patron, texto_suave):
        n_txt, u = coincidencia.group("n"), coincidencia.group("u") or ""
        if n_txt == "media":
            if u.startswith("docena"):
                lecturas.add((6, Unidad.UNIDAD_SUELTA))
            continue
        n = int(n_txt) if n_txt.isdigit() else NUMEROS_EN_LETRAS[n_txt]
        if u.startswith("docena"):
            unidad = Unidad.DOCENA
        elif u and re.fullmatch(_UNIDADES_SUELTA_TXT, u):
            unidad = Unidad.UNIDAD_SUELTA
        else:
            unidad = Unidad.VENTA
        lecturas.add((n, unidad))
    return lecturas.pop() if len(lecturas) == 1 else None


_VACIAS_TXT = "|".join(sorted(_VACIAS))


def _unir_palabras(inicio: Sequence[str]) -> str:
    """Patrón de las palabras de contenido de un texto permitiendo palabras vacías entre ellas
    ("kola de 3 litros" → `kola( de)* 3`)."""
    return rf"(?: +(?:{_VACIAS_TXT}))* +".join(map(re.escape, inicio))


def palabras_inicio(texto: str) -> list[str]:
    """Hasta dos primeras palabras de contenido del texto de una mención (sin unidades)."""
    contenido = palabras(texto, quitar_unidades=True)
    return [p for p in normalizar(texto).split() if p in contenido][:2]


def anclar_cantidades(menciones: Sequence[Mencion], mensajes: Sequence[str]) -> list[Mencion]:
    """Corrige la cantidad cuando el modelo **no copió** el número del mensaje (D37):

    - `cantidad = null` pero el mensaje dice "2 cajas de cloro" → 2.
    - una cantidad que no aparece en la conversación ni en cifras ni en letras (el modelo
      multiplicó: "2 docenas" → 24) y el mensaje pone otra justo antes del producto → esa,
      con la unidad que la acompaña.

    Conservador: solo `agregar`, solo si el producto aparece en **una** sola mención (las
    correcciones y repeticiones dicen el número después: "de X son 16") y solo si el mensaje
    tiene una única lectura."""
    texto = normalizar_suave(" ".join(mensajes))
    numeros = _numeros_de(" ".join(mensajes))
    claves = [m.sku if m.resolucion == Resolucion.SKU else normalizar(m.texto) for m in menciones]
    resultado = []
    for m, clave in zip(menciones, claves, strict=True):
        if (
            m.accion == Accion.AGREGAR
            and claves.count(clave) == 1
            and (m.cantidad is None or m.cantidad not in numeros)
        ):
            lectura = _cantidad_en_mensaje(m, texto)
            if lectura is not None and lectura != (m.cantidad, m.unidad):
                cantidad, unidad = lectura
                m = m.model_copy(update={"cantidad": cantidad, "unidad": unidad})
        resultado.append(m)
    return resultado


_NUMERO_TXT = r"(?P<n>\d+|" + "|".join(NUMEROS_EN_LETRAS) + r")"
_ARTICULO_TXT = r"(?:de +)?(?:(?:el|la|los|las) +)?"


def _numero(texto: str) -> int:
    return int(texto) if texto.isdigit() else NUMEROS_EN_LETRAS[texto]


def docenas_de_empaques(menciones: Sequence[Mencion], mensajes: Sequence[str]) -> list[Mencion]:
    """ "Una docena de cajas de sopa" son 12 **cajas** (unidad de venta), no 12 sopas sueltas
    (D40): si el mensaje pone "<n> docena(s) de <empaque> de <producto>" (o "media docena de
    cajas de…") justo antes del producto de una mención `agregar`, la cantidad es 12 × n
    unidades de venta. Solo con una única lectura."""
    texto = normalizar_suave(" ".join(mensajes))
    resultado = []
    for m in menciones:
        inicio = palabras_inicio(m.texto)
        if m.accion == Accion.AGREGAR and inicio:
            patron = (
                _ANTES_NUMERO
                + r"(?P<n>\d+|media|"
                + "|".join(NUMEROS_EN_LETRAS)
                + r") +docenas? +de +"
                r"(?:"
                + _UNIDADES_VENTA_TXT
                + r") +"
                + _ARTICULO_TXT
                + _unir_palabras(inicio)
                + r"\b"
            )
            lecturas = {
                6 if x.group("n") == "media" else 12 * _numero(x.group("n"))
                for x in re.finditer(patron, texto)
            }
            if len(lecturas) == 1:
                m = m.model_copy(update={"cantidad": lecturas.pop(), "unidad": Unidad.VENTA})
        resultado.append(m)
    return resultado


#: Medidas con las que la distribuidora no vende: una cantidad en ellas no se convierte (D50).
_MEDIDAS_TXT = (
    r"libras?|lbs?|kilos?|kgs?|kilogramos?|gramos?|grs?|onzas?|oz|litros?|lts?|mililitros?|"
    r"galon(?:es)?|rollos?|metros?|cartones?|tiras?|blisters?"
)
#: Palabras que distinguen una variante (sabor, color, tipo): si el cliente la dice y el
#: producto elegido no la tiene, ese producto no es el que pidió (D50).
_VARIANTES = frozenset(
    [
        "res",
        "pollo",
        "carne",
        "camaron",
        "costilla",
        "gallina",
        "uva",
        "fresa",
        "pina",
        "mango",
        "durazno",
        "manzana",
        "naranja",
        "limon",
        "mandarina",
        "chocolate",
        "vainilla",
        "coco",
        "tamarindo",
        "jamaica",
        "horchata",
        "cereza",
        "menta",
        "mora",
        "frambuesa",
        "sandia",
        "melon",
        "banano",
        "light",
        "zero",
        "diet",
        "dieta",
        "picante",
        "queso",
        "integral",
        "descremada",
        "deslactosada",
        "blanco",
        "blanca",
        "amarillo",
        "amarilla",
        "rojo",
        "roja",
        "negro",
        "negra",
        "morado",
        "rosado",
        "azul",
    ]
)


def _numeros_no_enteros(inicio: Sequence[str], texto_suave: str) -> set[int] | None:
    """Si justo antes del producto hay una cantidad que no es un número entero de unidades
    (decimal, fracción, "dos y medio", o una medida como "25 libras"), los números que la
    forman; si no, `None`."""
    nombre = _ARTICULO_TXT + _unir_palabras(inicio) + r"\b"
    empaque = rf"(?:(?:{_UNIDADES_VENTA_TXT}|{_UNIDADES_SUELTA_TXT}) +)?"
    partes: set[int] = set()
    encontrado = False
    decimal = rf"{_ANTES_NUMERO}(?P<a>\d+)[.,](?P<b>\d+) +{empaque}" + nombre
    for x in re.finditer(decimal, texto_suave):
        if int(x.group("b")) != 0:
            encontrado = True
            partes |= {int(x.group("a")), int(x.group("b"))}
    fraccion = rf"{_ANTES_NUMERO}(?P<a>\d+) ?/ ?(?P<b>\d+) +{empaque}" + nombre
    for x in re.finditer(fraccion, texto_suave):
        a, b = int(x.group("a")), int(x.group("b"))
        if b != 0 and a % b != 0:
            encontrado = True
            partes |= {a, b}
    y_medio = rf"{_ANTES_NUMERO}{_NUMERO_TXT} +y +medi[oa] +{empaque}" + nombre
    for x in re.finditer(y_medio, texto_suave):
        encontrado = True
        partes.add(_numero(x.group("n")))
    medida = (
        rf"{_ANTES_NUMERO}(?P<n>\d+(?:[.,]\d+)?|medi[oa]|{'|'.join(NUMEROS_EN_LETRAS)}) +"
        rf"(?:{_MEDIDAS_TXT}) +(?:y +medi[oa] +)?" + nombre
    )
    for x in re.finditer(medida, texto_suave):
        encontrado = True
        n = x.group("n")
        if n.isdigit():
            partes.add(int(n))
        elif n in NUMEROS_EN_LETRAS:
            partes.add(NUMEROS_EN_LETRAS[n])
    return partes if encontrado else None


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
        completas = palabras(texto)
        if completas != buscadas:
            # Una palabra de empaque puede ser parte del nombre ("arroz ceiba quintal" → A02):
            # si con ella hay candidatos, se usan; si no, se ignora ("fardo de kola 600").
            con_unidades = self._que_contienen(completas)
            if con_unidades:
                return con_unidades
        return self._que_contienen(buscadas)

    def _que_contienen(self, buscadas: set[str]) -> list[str]:
        return sorted(
            sku
            for sku, vocab in self._palabras_sku.items()
            if all(formas(p) & vocab for p in buscadas)
        )

    def desambiguar_por_contexto(
        self, m: Mencion, mensajes: Sequence[str], otros_textos: Sequence[str] = ()
    ) -> Mencion:
        """Un `ambiguo` cuyo texto en el mensaje va **inmediatamente** seguido o precedido de una
        palabra o número que solo tiene una de las opciones ("papel suave 4: 3" → L04, "un
        quintal de arroz" → A02) se resuelve a esa opción (D37). El número que es la cantidad
        de la mención no cuenta ("cocas 3", "2 de frijol"). Solo si el texto aparece una sola
        vez en la conversación, sin contar las apariciones dentro del texto **más largo** de
        otra mención ("dos quintales de arroz y un fardo de arroz ceiba de libra": el segundo
        "arroz" es de "arroz ceiba de libra"; D49)."""
        if m.resolucion != Resolucion.AMBIGUO or len(m.opciones) < 2:
            return m
        inicio = [p for p in normalizar(m.texto).split() if p in palabras(m.texto)]
        if not inicio:
            return m
        texto = normalizar_suave(" ".join(mensajes))
        propio = normalizar_suave(m.texto)
        for otro in sorted({normalizar_suave(o) for o in otros_textos}, key=len, reverse=True):
            if otro != propio and len(otro) > len(propio) and propio in otro:
                texto = re.sub(r"\b" + re.escape(otro) + r"\b", " | ", texto)
        nombre = r" +".join(map(re.escape, inicio))
        if len(re.findall(r"\b" + nombre + r"\b", texto)) != 1:
            return m  # el texto aparece varias veces ("pasta: 2 … pasta dental"): no se adivina
        despues = r"\b" + nombre + r" +([a-z0-9][a-z0-9.]*)"
        antes = r"([a-z0-9][a-z0-9.]*) +(?:de +)?" + nombre + r"\b"
        fichas = [x.group(1) for x in re.finditer(despues, texto)]
        fichas += [x.group(1) for x in re.finditer(antes, texto)]
        elegidos = set()
        for ficha in fichas:
            ficha = ficha.rstrip(".")
            if ficha == str(m.cantidad) or ficha in _VACIAS:
                continue
            tienen = [o for o in m.opciones if formas(ficha) & self._palabras_sku.get(o, set())]
            if len(tienen) == 1:
                elegidos.add(tienen[0])
        if len(elegidos) != 1:
            return m
        return m.model_copy(
            update={"resolucion": Resolucion.SKU, "sku": elegidos.pop(), "opciones": []}
        )

    def medias_unidades(
        self, menciones: Sequence[Mencion], mensajes: Sequence[str]
    ) -> list[Mencion]:
        """ "Una caja y media de cloro", "medio fardo de agua": no es una unidad de venta
        completa (§2, D40). La mención pasa a unidades sueltas con una cantidad que **no** es
        múltiplo del empaque (n × empaque + empaque // 2), así el motor registra
        `cantidad_no_convertible` en lugar de redondear. Si el empaque es 1 ("medio quintal de
        arroz") no hay número de sueltas que lo exprese: pasa a `no_convertible` (D50). Solo
        menciones `sku` que agregan o corrigen y con una única lectura."""
        texto = normalizar_suave(" ".join(mensajes))
        resultado = []
        for m in menciones:
            producto = self.datos.productos.get(m.sku or "")
            inicio = palabras_inicio(m.texto)
            if (
                m.resolucion == Resolucion.SKU
                and m.accion != Accion.QUITAR
                and producto is not None
                and inicio
            ):
                nombre = _ARTICULO_TXT + _unir_palabras(inicio) + r"\b"
                empaque = r"(?:" + _UNIDADES_VENTA_TXT + r")"
                y_media = rf"{_ANTES_NUMERO}{_NUMERO_TXT} +{empaque} +y +medi[oa] +" + nombre
                media = rf"{_ANTES_NUMERO}medi[oa] +{empaque} +" + nombre
                mitades = {_numero(x.group("n")) for x in re.finditer(y_media, texto)}
                if re.search(media, texto):
                    mitades.add(0)
                if len(mitades) == 1:
                    e = producto.unidades_por_empaque
                    if e >= 2:
                        cambio = {
                            "cantidad": mitades.pop() * e + e // 2,
                            "unidad": Unidad.UNIDAD_SUELTA,
                        }
                    else:
                        cambio = {
                            "cantidad": max(m.cantidad or 1, 1),
                            "unidad": Unidad.NO_CONVERTIBLE,
                        }
                    m = m.model_copy(update=cambio)
            resultado.append(m)
        return resultado

    def cantidades_no_enteras(
        self, menciones: Sequence[Mencion], mensajes: Sequence[str]
    ) -> list[Mencion]:
        """Cantidades que no son unidades enteras (`politicas.md` §2: no se redondea; D50):

        - decimales o fracciones justo antes del producto ("2.5 fardos de kola 600", "2,5
          cajas de…", "1/2 quintal de arroz", "dos y medio fardos de…");
        - medidas con las que la distribuidora no vende ("25 libras de arroz ceiba", "6 rollos
          de papel suave 4", "3 litros de cloro").

        La mención pasa a `no_convertible` (el motor registra `cantidad_no_convertible`) y se
        descartan las menciones repetidas del mismo producto que el modelo sacó del mismo
        número (con "2.5" el modelo devuelve una mención de 2 y otra de 5)."""
        texto = normalizar_suave(" ".join(mensajes))
        resultado: list[Mencion] = []
        partes_por_sku: dict[str, set[int]] = {}
        for m in menciones:
            inicio = palabras_inicio(m.texto)
            if (
                m.resolucion != Resolucion.SKU
                or not m.sku
                or m.accion == Accion.QUITAR
                or not inicio
            ):
                resultado.append(m)
                continue
            if (
                m.sku in partes_por_sku
                and m.accion == Accion.AGREGAR
                and (m.cantidad is None or m.cantidad in partes_por_sku[m.sku])
            ):
                continue  # el mismo número partido en dos menciones
            # Solo cantidades que siguen en unidades de venta: las sueltas ya tienen respaldo
            # en el mensaje y las medias unidades ya las resolvió `medias_unidades`.
            partes = _numeros_no_enteros(inicio, texto) if m.unidad == Unidad.VENTA else None
            if partes is not None:
                partes_por_sku[m.sku] = partes
                m = m.model_copy(
                    update={"cantidad": max(m.cantidad or 1, 1), "unidad": Unidad.NO_CONVERTIBLE}
                )
            resultado.append(m)
        return resultado

    def variantes_no_vendidas(self, m: Mencion) -> Mencion:
        """El texto pide una **variante** que el catálogo no tiene ("sopa rápida de res" cuando
        solo hay de pollo, "agua pura de 1 litro" cuando hay de 600 ml y 1.5 L): el modelo o la
        resolución eligieron el producto más parecido, pero eso sería inventar (regla 4, D50).
        Cuenta como variante un número (presentación) o una palabra de `_VARIANTES` (sabor,
        color, tipo) que no está en el vocabulario del producto. Un `sku` así pasa a
        `no_existe`; un `ambiguo` pierde las opciones que no la tienen."""
        if m.accion == Accion.QUITAR or m.resolucion == Resolucion.NO_EXISTE:
            return m
        contenido = palabras(m.texto, quitar_unidades=True)
        orden = [p.strip(".") for p in normalizar(m.texto).split() if p.strip(".") in contenido]
        if orden and orden[0] == str(m.cantidad):
            orden = orden[1:]  # el modelo dejó la cantidad al inicio del texto ("2 kola 600")
        fichas = [p for p in orden if re.fullmatch(r"\d+(?:\.\d+)?", p) or p in _VARIANTES]
        if not fichas:
            return m

        def la_tiene(sku: str) -> bool:
            vocab = self._palabras_sku.get(sku, set())
            return all(formas(p) & vocab for p in fichas)

        if m.resolucion == Resolucion.SKU:
            if m.sku and not la_tiene(m.sku):
                return m.model_copy(update={"resolucion": Resolucion.NO_EXISTE, "sku": None})
            return m
        opciones = [o for o in m.opciones if la_tiene(o)]
        if not opciones:
            return m.model_copy(
                update={"resolucion": Resolucion.NO_EXISTE, "sku": None, "opciones": []}
            )
        if len(opciones) == 1:
            return m.model_copy(
                update={"resolucion": Resolucion.SKU, "sku": opciones[0], "opciones": []}
            )
        return m.model_copy(update={"opciones": opciones})

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
            if len(cands) == 1:
                # El texto ya distingue un solo producto ("arroz ceiba quintal" → A02).
                return m.model_copy(
                    update={"resolucion": Resolucion.SKU, "sku": cands[0], "opciones": []}
                )
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
        queda `ambiguo`. Si el texto es literal, no se toca. Si **ninguna** palabra del texto
        aparece en la conversación, el modelo inventó el producto: pasa a `no_existe`."""
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
        if buscadas and not conservadas and m.sku and m.sku.lower() in fichas:
            # El cliente escribió el código del producto ("B01 x2", un JSON con "sku": "B01")
            # y el modelo puso el nombre del catálogo: el pendiente o la línea citan el código.
            return m.model_copy(update={"texto": m.sku})
        if buscadas and not conservadas:
            # Ninguna palabra del texto está en la conversación: el modelo inventó el producto.
            # Nunca se entrega algo que el cliente no nombró; queda como pendiente (D37).
            return m.model_copy(update={"resolucion": Resolucion.NO_EXISTE, "sku": None})
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
        self,
        interpretacion: InterpretacionResuelta,
        mensajes: Sequence[str] | None = None,
        cliente_id: str | None = None,
    ) -> InterpretacionResuelta:
        """Aplica `resolver_mencion` a cada mención y, si se dan los mensajes, el anclaje al
        texto literal, las variantes no vendidas, el anclaje de cantidades, la verificación de
        unidades, las cantidades no enteras y la detección de correcciones. Con `cliente_id` y
        "lo de siempre", los productos de su último pedido entregado cuentan como ya pedidos
        para detectar correcciones ("lo de siempre pero de kola 600 que sean 5")."""
        menciones = [self.resolver_mencion(m) for m in interpretacion.menciones]
        if mensajes is not None:
            menciones = [self.anclar_al_mensaje(m, mensajes) for m in menciones]
            menciones = [self.variantes_no_vendidas(m) for m in menciones]
            textos = [m.texto for m in menciones]
            menciones = [self.desambiguar_por_contexto(m, mensajes, textos) for m in menciones]
            menciones = anclar_cantidades(menciones, mensajes)
            menciones = verificar_unidades(menciones, mensajes)
            menciones = docenas_de_empaques(menciones, mensajes)
            menciones = self.medias_unidades(menciones, mensajes)
            menciones = self.cantidades_no_enteras(menciones, mensajes)
            previos: list[str] = []
            if interpretacion.lo_de_siempre and cliente_id:
                pedido = self.datos.ultimo_pedido_entregado(cliente_id)
                previos = [linea.sku for linea in pedido.lineas] if pedido else []
            menciones = detectar_correcciones(menciones, mensajes, previos)
        menciones = referencias_a_lo_pedido(menciones)
        return interpretacion.model_copy(update={"menciones": menciones})


_MARCAS_CORRECCION = re.compile(
    r"\b(mejor|corrijo|corrige|que sean|perdon|ah no|en vez|cambie|cambio)\b"
)


#: Marca de corrección **justo después** del producto ("de kola 600 que sean 5").
_MARCAS_CORRECCION_DESPUES = re.compile(r"^\W*(?:que sean|que sea|mejor|serian|en vez)\b")


def detectar_correcciones(
    menciones: Sequence[Mencion], mensajes: Sequence[str], previos: Sequence[str] = ()
) -> list[Mencion]:
    """Una mención `agregar` de un sku **ya pedido** que en la conversación va precedida (a
    menos de 40 caracteres) por una marca de corrección ("Mejor 6 fardos de sal pacifico") o
    seguida de una ("de kola 600 que sean 5") es una corrección que el modelo no marcó: pasa a
    `corregir`. Sin marca, se suma (§2). `previos`: skus ya pedidos antes de la conversación
    (las líneas de "lo de siempre", D50)."""
    texto = normalizar(" ".join(mensajes))
    vistos: set[str] = set(previos)
    resultado = []
    for m in menciones:
        if m.resolucion == Resolucion.SKU and m.sku:
            if m.accion == Accion.AGREGAR and m.sku in vistos and m.cantidad is not None:
                inicio = normalizar(m.texto).split()[:2]
                if inicio:
                    patron = r"\s+".join(map(re.escape, inicio))
                    ocurrencias = list(re.finditer(patron, texto))
                    if ocurrencias:
                        ultima = ocurrencias[-1]
                        antes = texto[max(0, ultima.start() - 40) : ultima.start()]
                        despues = texto[ultima.end() : ultima.end() + 25]
                        if _MARCAS_CORRECCION.search(antes) or _MARCAS_CORRECCION_DESPUES.search(
                            despues
                        ):
                            m = m.model_copy(update={"accion": Accion.CORREGIR})
            vistos.add(m.sku)
        resultado.append(m)
    return resultado


def referencias_a_lo_pedido(menciones: Sequence[Mencion]) -> list[Mencion]:
    """Una corrección o un quitar `ambiguo` ("perdón, de kola que sean 9") se refiere a algo
    que ya está en el pedido: si **exactamente una** de sus opciones ya se pidió antes en la
    conversación, es ese sku (D49). Si ninguna o varias, queda `ambiguo`."""
    pedidos: list[str] = []
    resultado = []
    for m in menciones:
        if (
            m.accion in (Accion.CORREGIR, Accion.QUITAR)
            and m.resolucion == Resolucion.AMBIGUO
            and m.opciones
        ):
            previas = {o for o in m.opciones if o in pedidos}
            if len(previas) == 1:
                m = m.model_copy(
                    update={"resolucion": Resolucion.SKU, "sku": previas.pop(), "opciones": []}
                )
        if m.resolucion == Resolucion.SKU and m.sku:
            pedidos.append(m.sku)
        resultado.append(m)
    return resultado


__all__ = [
    "MARGEN_DIFUSO",
    "NUMEROS_EN_LETRAS",
    "anclar_cantidades",
    "docenas_de_empaques",
    "normalizar_suave",
    "SINONIMOS_PALABRA",
    "UMBRAL_DIFUSO",
    "ResolvedorProductos",
    "normalizar",
    "detectar_correcciones",
    "referencias_a_lo_pedido",
    "formas",
    "palabras",
    "sin_tildes",
    "verificar_unidades",
]
