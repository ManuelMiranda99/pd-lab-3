"""Intérprete real: LangChain + OpenRouter con salida estructurada (`DECISIONES.md` D27).

Flujo por pedido (**una sola llamada** al modelo):

1. Prompt del sistema estático (reglas + catálogo completo y compacto + ejemplos) y mensaje
   de usuario con la conversación delimitada como datos (`novapedidos.llm.prompt`).
2. `ChatOpenAI.with_structured_output(esquema, method="json_schema", include_raw=True)`: el
   esquema restringe `sku`/`opciones` a los sku del catálogo (`novapedidos.llm.esquema`).
3. Conversión a `InterpretacionResuelta` y resolución post-LLM en código
   (`novapedidos.llm.resolucion`).

Cualquier fallo (sin clave, sin datos, timeout, error del proveedor, JSON inválido) se
convierte en `ErrorInterprete` → la API responde 503 (D23). Los metadatos de cada llamada
(latencia, tokens, costo) quedan en `ultima_llamada` y se pasan a `al_terminar` (gancho para
MLflow en la Fase 8).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, NoReturn

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable
from openai import ContentFilterFinishReasonError
from pydantic import BaseModel, ValidationError

from novapedidos.config import Configuracion
from novapedidos.datos import Datos, cargar_datos
from novapedidos.llm.cliente import MetadatosLlamada, crear_modelo_chat, extraer_metadatos
from novapedidos.llm.defensas import pide_ignorar_instrucciones, quitar_instrucciones
from novapedidos.llm.esquema import a_interpretacion, construir_esquema
from novapedidos.llm.interfaz import ErrorInterprete, Interprete
from novapedidos.llm.prompt import mensaje_usuario, prompt_sistema
from novapedidos.llm.resolucion import ResolvedorProductos
from novapedidos.motor.modelos import InterpretacionResuelta

logger = logging.getLogger("novapedidos.llm")

ProveedorDatos = Callable[[], Datos]
FabricaModelo = Callable[[], BaseChatModel]
GanchoLlamada = Callable[[MetadatosLlamada], None]


class _Componentes:
    """Lo que depende del catálogo: prompt, esquema, extractor y resolvedor."""

    def __init__(self, datos: Datos, modelo: BaseChatModel) -> None:
        self.datos = datos
        self.prompt_sistema = prompt_sistema(datos)
        self.esquema: type[BaseModel] = construir_esquema(datos.productos.keys())
        self.extractor: Runnable = modelo.with_structured_output(
            self.esquema, method="json_schema", include_raw=True
        )
        self.resolvedor = ResolvedorProductos(datos)


class InterpreteLLM:
    """Intérprete con LangChain + OpenRouter que cumple el protocolo `Interprete`.

    - `proveedor_datos`: devuelve el catálogo (se llama en cada pedido; los componentes se
      reconstruyen solo si cambia el objeto `Datos`).
    - `fabrica_modelo`: construye el `BaseChatModel` (en producción, `ChatOpenAI` contra
      OpenRouter; en pruebas, uno con transporte HTTP falso).
    - `nombre_modelo`: para los metadatos.
    - `resolver`: aplicar la resolución post-LLM (se puede apagar para medirla).
    - `al_terminar`: gancho opcional que recibe los metadatos de cada llamada.
    """

    def __init__(
        self,
        proveedor_datos: ProveedorDatos,
        fabrica_modelo: FabricaModelo,
        *,
        nombre_modelo: str = "",
        resolver: bool = True,
        al_terminar: GanchoLlamada | None = None,
    ) -> None:
        self._proveedor_datos = proveedor_datos
        self._fabrica_modelo = fabrica_modelo
        self.nombre_modelo = nombre_modelo
        self.resolver = resolver
        self.al_terminar = al_terminar
        self.ultima_llamada: MetadatosLlamada | None = None
        self._componentes: _Componentes | None = None
        self._modelo: BaseChatModel | None = None
        self._candado = threading.Lock()

    # ------------------------------------------------------------------ preparación

    def _preparar(self) -> _Componentes:
        with self._candado:
            if self._modelo is None:  # primero la clave/cliente: el motivo más claro
                try:
                    self._modelo = self._fabrica_modelo()
                except ErrorInterprete:
                    raise
                except Exception as exc:  # noqa: BLE001
                    raise ErrorInterprete(
                        f"no se pudo crear el cliente del modelo ({type(exc).__name__})"
                    ) from exc
            modelo = self._modelo
        try:
            datos = self._proveedor_datos()
        except Exception as exc:  # noqa: BLE001
            raise ErrorInterprete(
                f"el catálogo no está disponible para el intérprete ({type(exc).__name__})"
            ) from exc
        with self._candado:
            if self._componentes is None or self._componentes.datos is not datos:
                self._componentes = _Componentes(datos, modelo)
            return self._componentes

    def prompt_sistema(self) -> str:
        """El prompt del sistema vigente (para inspección y pruebas)."""
        return self._preparar().prompt_sistema

    # ------------------------------------------------------------------ interpretación

    def _notificar(self, metadatos: MetadatosLlamada) -> None:
        self.ultima_llamada = metadatos
        if self.al_terminar is not None:
            try:
                self.al_terminar(metadatos)
            except Exception:  # noqa: BLE001 - el gancho nunca afecta la respuesta
                logger.warning("El gancho al_terminar falló", exc_info=True)

    def _invocar(self, componentes: _Componentes, mensajes: list[str]) -> Any:
        entrada = [
            ("system", componentes.prompt_sistema),
            ("human", mensaje_usuario(mensajes)),
        ]
        return componentes.extractor.invoke(entrada)

    def _fallo_llamada(self, exc: Exception, inicio: float) -> NoReturn:
        latencia = time.perf_counter() - inicio
        self._notificar(
            MetadatosLlamada(
                modelo=self.nombre_modelo, latencia_s=latencia, error=type(exc).__name__
            )
        )
        logger.warning("Fallo al llamar al modelo: %s", type(exc).__name__)
        if isinstance(exc, ValidationError | OutputParserException | json.JSONDecodeError):
            motivo = "el modelo devolvió una respuesta que no cumple el esquema"
        else:
            motivo = "el modelo no respondió correctamente"
        raise ErrorInterprete(f"{motivo} ({type(exc).__name__})") from exc

    def interpretar_con_metadatos(
        self, cliente_id: str, mensajes: list[str], fecha_hora: datetime
    ) -> tuple[InterpretacionResuelta, MetadatosLlamada]:
        """Como `interpretar`, pero devuelve también latencia, tokens y costo de la llamada."""
        componentes = self._preparar()
        inicio = time.perf_counter()
        filtro = False
        saneado = pide_ignorar_instrucciones(mensajes)
        if saneado:
            # D41: el filtro del proveedor rechazaría la conversación; se sanea de una vez.
            mensajes = quitar_instrucciones(mensajes)
            if not mensajes:
                metadatos = MetadatosLlamada(
                    modelo=self.nombre_modelo,
                    latencia_s=time.perf_counter() - inicio,
                    error="manipulacion_sin_pedido",
                    saneado_previo=True,
                )
                self._notificar(metadatos)
                return InterpretacionResuelta(es_pedido=False), metadatos
        try:
            resultado: Any = self._invocar(componentes, mensajes)
        except ContentFilterFinishReasonError:
            filtro = True
            # Respaldo (D32): el filtro del proveedor rechazó la conversación; se reintenta
            # una vez sin las oraciones manipuladoras. Si no queda nada, no hay pedido.
            logger.warning("El filtro de contenido del proveedor rechazó la conversación")
            mensajes = quitar_instrucciones(mensajes)
            if not mensajes:
                metadatos = MetadatosLlamada(
                    modelo=self.nombre_modelo,
                    latencia_s=time.perf_counter() - inicio,
                    error="content_filter_sin_pedido",
                    filtro_contenido=True,
                )
                self._notificar(metadatos)
                return InterpretacionResuelta(es_pedido=False), metadatos
            try:
                resultado = self._invocar(componentes, mensajes)
            except Exception as exc:  # noqa: BLE001
                self._fallo_llamada(exc, inicio)
        except Exception as exc:  # noqa: BLE001 - timeout, red, proveedor, validación
            self._fallo_llamada(exc, inicio)
        latencia = time.perf_counter() - inicio

        crudo = resultado.get("raw") if isinstance(resultado, dict) else None
        metadatos = extraer_metadatos(crudo, latencia, self.nombre_modelo)
        if filtro or saneado:
            metadatos = MetadatosLlamada(
                **{**metadatos.como_dict(), "filtro_contenido": filtro, "saneado_previo": saneado}
            )
        parseado = resultado.get("parsed") if isinstance(resultado, dict) else None
        error_parseo = resultado.get("parsing_error") if isinstance(resultado, dict) else None
        if parseado is None or error_parseo is not None:
            nombre_error = type(error_parseo).__name__ if error_parseo else "SinRespuesta"
            metadatos = MetadatosLlamada(**{**metadatos.como_dict(), "error": nombre_error})
            self._notificar(metadatos)
            raise ErrorInterprete(
                f"el modelo devolvió una respuesta que no cumple el esquema ({nombre_error})"
            )
        try:
            interpretacion = a_interpretacion(parseado)
            if self.resolver:
                interpretacion = componentes.resolvedor.resolver(interpretacion, mensajes)
        except Exception as exc:  # noqa: BLE001
            metadatos = MetadatosLlamada(**{**metadatos.como_dict(), "error": type(exc).__name__})
            self._notificar(metadatos)
            raise ErrorInterprete(
                f"no se pudo convertir la respuesta del modelo ({type(exc).__name__})"
            ) from exc
        self._notificar(metadatos)
        return interpretacion, metadatos

    def interpretar(
        self, cliente_id: str, mensajes: list[str], fecha_hora: datetime
    ) -> InterpretacionResuelta:
        interpretacion, _ = self.interpretar_con_metadatos(cliente_id, mensajes, fecha_hora)
        return interpretacion


def crear_interprete_llm(
    config: Configuracion,
    *,
    proveedor_datos: ProveedorDatos | None = None,
    al_terminar: GanchoLlamada | None = None,
) -> Interprete:
    """Fábrica que usa la API cuando `INTERPRETE=llm` (D25).

    No llama al modelo ni carga datos al construirse (la API arranca aunque falten): el
    catálogo se carga en el primer pedido y, si falta la clave, cada pedido da 503 con un
    motivo claro (y `/health` reporta `degradado`).
    """
    if proveedor_datos is None:
        cache: dict[str, Datos] = {}

        def proveedor_datos() -> Datos:
            if "datos" not in cache:
                cache["datos"] = cargar_datos(config.datos_dir)
            return cache["datos"]

    def fabrica_modelo() -> BaseChatModel:
        if not config.tiene_clave:
            raise ErrorInterprete("falta OPENROUTER_API_KEY")
        return crear_modelo_chat(config)

    return InterpreteLLM(
        proveedor_datos,
        fabrica_modelo,
        nombre_modelo=config.openrouter_model,
        al_terminar=al_terminar,
    )


__all__ = ["InterpreteLLM", "crear_interprete_llm"]
