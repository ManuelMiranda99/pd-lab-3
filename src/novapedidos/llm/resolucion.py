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
    "docena",
    "sueltas",
    "paquetit",
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


_PATRON_SUELTAS = r"(botellas?|latas?|latitas?|bolsas?|bolsitas?|sobres?|unidades|piezas?)"


def _numero_con_suelta(cantidad: int, texto_normalizado: str) -> bool:
    """El número (en cifras o letras) va seguido de una palabra de unidad suelta, o es la
    "media docena" (6)."""
    if cantidad == 6 and "media docena" in texto_normalizado:
        return True
    escritos = [str(cantidad)] + [p for p, n in NUMEROS_EN_LETRAS.items() if n == cantidad]
    numero = "|".join(map(re.escape, escritos))
    return re.search(rf"(?<![\w.])({numero})\s+{_PATRON_SUELTAS}\b", texto_normalizado) is not None


def _dijo_sueltas(m: Mencion, texto_normalizado: str) -> bool:
    """ "48 bolsas de detergente espumoso": el número de la mención va seguido de una palabra
    de unidad suelta y luego del texto del producto (el modelo dejó `venta` por error)."""
    inicio = normalizar(m.texto).split()[:2]
    if not inicio:
        return False
    patron = rf"\b{m.cantidad}\s+{_PATRON_SUELTAS}\s+(de\s+)?" + r"\s+".join(map(re.escape, inicio))
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
_UNIDADES_SUELTA_TXT = r"botellas?|latas?|latitas?|bolsas?|bolsitas?|sobres?|unidades|piezas?"
_PATRON_CANTIDAD = (
    r"(?<![\w.])(?P<n>\d+|media|" + "|".join(NUMEROS_EN_LETRAS) + r") +"
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
                r"(?<![\w.])(?P<n>\d+|media|" + "|".join(NUMEROS_EN_LETRAS) + r") +docenas? +de +"
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

    def desambiguar_por_contexto(self, m: Mencion, mensajes: Sequence[str]) -> Mencion:
        """Un `ambiguo` cuyo texto en el mensaje va **inmediatamente** seguido o precedido de una
        palabra o número que solo tiene una de las opciones ("papel suave 4: 3" → L04, "un
        quintal de arroz" → A02) se resuelve a esa opción (D37). El número que es la cantidad
        de la mención no cuenta ("cocas 3", "2 de frijol"). Solo si el texto aparece una sola
        vez en la conversación."""
        if m.resolucion != Resolucion.AMBIGUO or len(m.opciones) < 2:
            return m
        inicio = [p for p in normalizar(m.texto).split() if p in palabras(m.texto)]
        if not inicio:
            return m
        texto = normalizar_suave(" ".join(mensajes))
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
        `cantidad_no_convertible` en lugar de redondear. Solo menciones `sku` que agregan o
        corrigen, con empaque ≥ 2 y una única lectura."""
        texto = normalizar_suave(" ".join(mensajes))
        resultado = []
        for m in menciones:
            producto = self.datos.productos.get(m.sku or "")
            inicio = palabras_inicio(m.texto)
            if (
                m.resolucion == Resolucion.SKU
                and m.accion != Accion.QUITAR
                and producto is not None
                and producto.unidades_por_empaque >= 2
                and inicio
            ):
                nombre = _ARTICULO_TXT + _unir_palabras(inicio) + r"\b"
                empaque = r"(?:" + _UNIDADES_VENTA_TXT + r")"
                y_media = rf"(?<![\w.]){_NUMERO_TXT} +{empaque} +y +medi[oa] +" + nombre
                media = rf"(?<![\w.])medi[oa] +{empaque} +" + nombre
                mitades = {_numero(x.group("n")) for x in re.finditer(y_media, texto)}
                if re.search(media, texto):
                    mitades.add(0)
                if len(mitades) == 1:
                    e = producto.unidades_por_empaque
                    m = m.model_copy(
                        update={
                            "cantidad": mitades.pop() * e + e // 2,
                            "unidad": Unidad.UNIDAD_SUELTA,
                        }
                    )
            resultado.append(m)
        return resultado

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
        self, interpretacion: InterpretacionResuelta, mensajes: Sequence[str] | None = None
    ) -> InterpretacionResuelta:
        """Aplica `resolver_mencion` a cada mención y, si se dan los mensajes, el anclaje al
        texto literal, el anclaje de cantidades, la verificación de unidades y la detección de
        correcciones."""
        menciones = [self.resolver_mencion(m) for m in interpretacion.menciones]
        if mensajes is not None:
            menciones = [self.anclar_al_mensaje(m, mensajes) for m in menciones]
            menciones = [self.desambiguar_por_contexto(m, mensajes) for m in menciones]
            menciones = anclar_cantidades(menciones, mensajes)
            menciones = verificar_unidades(menciones, mensajes)
            menciones = docenas_de_empaques(menciones, mensajes)
            menciones = self.medias_unidades(menciones, mensajes)
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
    "NUMEROS_EN_LETRAS",
    "anclar_cantidades",
    "docenas_de_empaques",
    "normalizar_suave",
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
