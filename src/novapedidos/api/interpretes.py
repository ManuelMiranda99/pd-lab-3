"""Intérpretes disponibles para la API y cómo se elige uno (`DECISIONES.md` D25).

- `InterpreteFalso`: determinista y configurable (tabla mensajes → interpretación o una
  función). Lo usan las pruebas y la demo `INTERPRETE=falso`.
- `InterpreteNoDisponible`: siempre lanza `ErrorInterprete` con un motivo claro; se usa cuando
  el intérprete pedido no existe o no se puede construir (la API responde 503, nunca inventa).
- `crear_interprete(nombre)`: fábrica a partir de la variable `INTERPRETE` (`llm` | `falso`).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from novapedidos.config import Configuracion
from novapedidos.llm.interfaz import ErrorInterprete, Interprete
from novapedidos.motor.modelos import (
    InterpretacionResuelta,
    Mencion,
    Resolucion,
    Unidad,
)

#: Módulo y fábrica que debe proveer el intérprete real: `crear_interprete_llm(config)`.
MODULO_INTERPRETE_LLM = "novapedidos.llm.interprete"
FABRICA_INTERPRETE_LLM = "crear_interprete_llm"

FuncionInterprete = Callable[[str, list[str], datetime], InterpretacionResuelta]


def clave_mensajes(mensajes: Sequence[str]) -> tuple[str, ...]:
    """Clave de búsqueda del intérprete falso: mensajes sin espacios sobrantes, en minúsculas."""
    return tuple(" ".join(m.split()).lower() for m in mensajes)


class InterpreteFalso:
    """Intérprete determinista para pruebas y demos (no llama a ningún modelo).

    - `tabla`: mensajes → interpretación. La clave puede ser un `str` (un solo mensaje) o una
      tupla/lista de mensajes; se compara sin mayúsculas ni espacios sobrantes.
    - `funcion`: alternativa a la tabla; recibe `(cliente_id, mensajes, fecha_hora)`.
    - `por_defecto`: lo que devuelve si los mensajes no están en la tabla. Si es `None`, lanza
      `ErrorInterprete` (la API responde 503), para no inventar pedidos.

    Guarda las llamadas en `llamadas` para que las pruebas verifiquen qué recibió.
    """

    def __init__(
        self,
        tabla: Mapping[str | Sequence[str], InterpretacionResuelta] | None = None,
        *,
        funcion: FuncionInterprete | None = None,
        por_defecto: InterpretacionResuelta | None = None,
    ) -> None:
        self._tabla: dict[tuple[str, ...], InterpretacionResuelta] = {}
        for clave, interpretacion in (tabla or {}).items():
            mensajes = [clave] if isinstance(clave, str) else list(clave)
            self._tabla[clave_mensajes(mensajes)] = interpretacion
        self._funcion = funcion
        self._por_defecto = por_defecto
        self.llamadas: list[tuple[str, list[str], datetime]] = []

    def interpretar(
        self, cliente_id: str, mensajes: list[str], fecha_hora: datetime
    ) -> InterpretacionResuelta:
        self.llamadas.append((cliente_id, list(mensajes), fecha_hora))
        if self._funcion is not None:
            return self._funcion(cliente_id, list(mensajes), fecha_hora)
        encontrada = self._tabla.get(clave_mensajes(mensajes))
        if encontrada is not None:
            return encontrada
        if self._por_defecto is not None:
            return self._por_defecto
        raise ErrorInterprete(
            "El intérprete falso no conoce estos mensajes (solo responde los de su tabla de "
            "demostración); configure INTERPRETE=llm para interpretar mensajes reales."
        )


class InterpreteNoDisponible:
    """Intérprete que siempre falla con un motivo claro (configuración inválida o incompleta)."""

    def __init__(self, motivo: str) -> None:
        self.motivo = motivo

    def interpretar(
        self, cliente_id: str, mensajes: list[str], fecha_hora: datetime
    ) -> InterpretacionResuelta:
        raise ErrorInterprete(self.motivo)


# --------------------------------------------------------------------------- demo

#: Mensajes del ejemplo del Anexo del enunciado.
MENSAJES_ANEXO = (
    "Buenas, mándeme 2 fardos de kola 600, una caja de aceite",
    "y 48 latas de energía max",
)

#: Interpretación que daría un intérprete correcto para el ejemplo del Anexo: B01×2 fardos,
#: "aceite" ambiguo entre A06 y A07 (D10) y 48 latas de B11 (= 4 fardos de 12).
INTERPRETACION_ANEXO = InterpretacionResuelta(
    es_pedido=True,
    menciones=[
        Mencion(texto="kola 600", resolucion=Resolucion.SKU, sku="B01", cantidad=2),
        Mencion(texto="aceite", resolucion=Resolucion.AMBIGUO, opciones=["A06", "A07"], cantidad=1),
        Mencion(
            texto="energía max",
            resolucion=Resolucion.SKU,
            sku="B11",
            cantidad=48,
            unidad=Unidad.UNIDAD_SUELTA,
        ),
    ],
)

#: Un mensaje que no es pedido (consulta) → `fuera_de_alcance`.
MENSAJES_CONSULTA = ("¿A qué hora pasa el camión el jueves?",)


def interprete_demo() -> InterpreteFalso:
    """Intérprete falso con la tabla de demostración (Anexo y una consulta)."""
    return InterpreteFalso(
        {
            MENSAJES_ANEXO: INTERPRETACION_ANEXO,
            MENSAJES_CONSULTA: InterpretacionResuelta(es_pedido=False),
        }
    )


# --------------------------------------------------------------------------- fábrica


def _crear_interprete_llm(configuracion: Configuracion) -> Interprete:
    try:
        modulo = importlib.import_module(MODULO_INTERPRETE_LLM)
    except ModuleNotFoundError as exc:
        if exc.name != MODULO_INTERPRETE_LLM:
            raise
        return InterpreteNoDisponible(
            "El intérprete LLM todavía no está disponible en esta versión del servicio; "
            "use INTERPRETE=falso para probar la API con el ejemplo del Anexo."
        )
    fabrica: Callable[[Configuracion], Interprete] = getattr(modulo, FABRICA_INTERPRETE_LLM)
    return fabrica(configuracion)


def crear_interprete(configuracion: Configuracion) -> Interprete:
    """Construye el intérprete según `INTERPRETE` (`llm` por defecto, o `falso`).

    Nunca lanza: si el nombre es inválido o el intérprete no se puede construir, devuelve un
    `InterpreteNoDisponible` (la API arranca, `/health` lo reporta y los pedidos dan 503).
    """
    nombre = configuracion.interprete.strip().lower()
    if nombre == "falso":
        return interprete_demo()
    if nombre != "llm":
        return InterpreteNoDisponible(
            f"INTERPRETE={configuracion.interprete!r} no es válido (use 'llm' o 'falso')."
        )
    try:
        return _crear_interprete_llm(configuracion)
    except Exception as exc:  # noqa: BLE001 - la API debe arrancar igual y explicar el motivo
        return InterpreteNoDisponible(
            f"No se pudo inicializar el intérprete LLM: {type(exc).__name__}."
        )


__all__ = [
    "FABRICA_INTERPRETE_LLM",
    "INTERPRETACION_ANEXO",
    "MENSAJES_ANEXO",
    "MENSAJES_CONSULTA",
    "MODULO_INTERPRETE_LLM",
    "InterpreteFalso",
    "InterpreteNoDisponible",
    "clave_mensajes",
    "crear_interprete",
    "interprete_demo",
]
