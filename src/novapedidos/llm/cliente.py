"""Cliente de chat contra OpenRouter (LangChain `ChatOpenAI`) y metadatos de cada llamada.

- `crear_modelo_chat(config)`: `ChatOpenAI` con el modelo, la clave y la URL de la
  configuración, `temperature=0`, timeout y reintentos acotados (`DECISIONES.md` D30) y el
  costo de OpenRouter incluido en `usage` (`usage.include`).
- `MetadatosLlamada` / `extraer_metadatos`: modelo, latencia, tokens y costo de una llamada,
  leídos del mensaje crudo que devuelve LangChain. Los consume `scripts/probar_casos.py` y, en
  la Fase 8, las trazas de MLflow.

Nunca se imprime ni se registra el valor de `OPENROUTER_API_KEY`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import httpx
from langchain_openai import ChatOpenAI

from novapedidos.config import Configuracion

#: Segundos máximos por intento de llamada al modelo (un pedido normal tarda 1–3 s).
TIMEOUT_S = 15.0
#: Reintentos del cliente OpenAI ante errores transitorios (timeout, 429, 5xx).
MAX_REINTENTOS = 2
#: Encabezados opcionales de OpenRouter para identificar la aplicación (sin datos personales).
ENCABEZADOS_OPENROUTER = {
    "HTTP-Referer": "https://localhost/novapedidos",
    "X-Title": "NovaPedidos (laboratorio)",
}


@dataclass(frozen=True, slots=True)
class MetadatosLlamada:
    """Lo que cuesta y tarda una llamada al modelo (para reportes y trazas)."""

    modelo: str
    latencia_s: float
    tokens_entrada: int | None = None
    tokens_salida: int | None = None
    tokens_cache: int | None = None
    costo_usd: float | None = None
    id_generacion: str | None = None
    error: str | None = None
    #: El filtro de contenido del proveedor rechazó la conversación y se reintentó sin las
    #: oraciones manipuladoras (D32): la latencia incluye las dos llamadas.
    filtro_contenido: bool = False
    #: La conversación pedía ignorar las instrucciones y se saneó antes de la primera llamada
    #: (D41): una sola llamada en vez de rechazo + reintento.
    saneado_previo: bool = False

    def como_dict(self) -> dict[str, Any]:
        return asdict(self)


#: Valores válidos de `OPENROUTER_RAZONAMIENTO` además de vacío (el del proveedor).
ESFUERZOS_RAZONAMIENTO = ("minimal", "low", "medium", "high")


def parametro_razonamiento(valor: str | None) -> dict[str, Any] | None:
    """Traduce `OPENROUTER_RAZONAMIENTO` al parámetro `reasoning` de OpenRouter.

    Vacío → `None` (no se envía: decide el proveedor); `no` → `{"enabled": False}`; un
    esfuerzo → `{"effort": …}`. Un valor desconocido se ignora (no rompe la API).
    """
    limpio = (valor or "").strip().lower()
    if limpio in ("no", "off", "false", "0", "ninguno"):
        return {"enabled": False}
    if limpio in ESFUERZOS_RAZONAMIENTO:
        return {"effort": limpio}
    return None


def crear_modelo_chat(
    config: Configuracion,
    *,
    modelo: str | None = None,
    razonamiento: str | None = None,
    timeout: float = TIMEOUT_S,
    max_reintentos: int = MAX_REINTENTOS,
    http_client: httpx.Client | None = None,
) -> ChatOpenAI:
    """`ChatOpenAI` apuntando a OpenRouter con los parámetros del servicio.

    `http_client` permite inyectar un transporte falso en las pruebas (sin red ni modelo).
    `razonamiento` reemplaza a `config.openrouter_razonamiento` (comparación de la Fase 10).
    """
    extra_body: dict[str, Any] = {"usage": {"include": True}}
    reasoning = parametro_razonamiento(
        config.openrouter_razonamiento if razonamiento is None else razonamiento
    )
    if reasoning is not None:
        extra_body["reasoning"] = reasoning
    return ChatOpenAI(
        model=modelo or config.openrouter_model,
        base_url=config.openrouter_base_url,
        api_key=config.openrouter_api_key,
        temperature=0,
        timeout=timeout,
        max_retries=max_reintentos,
        default_headers=ENCABEZADOS_OPENROUTER,
        extra_body=extra_body,
        http_client=http_client,
    )


def _entero(valor: Any) -> int | None:
    try:
        return int(valor) if valor is not None else None
    except (TypeError, ValueError):
        return None


def extraer_metadatos(crudo: Any, latencia_s: float, modelo: str) -> MetadatosLlamada:
    """Lee tokens, costo e id de generación del `AIMessage` crudo (tolerante a faltantes)."""
    meta = getattr(crudo, "response_metadata", None) or {}
    uso = meta.get("token_usage") or {}
    uso_lc = getattr(crudo, "usage_metadata", None) or {}
    detalles = uso.get("prompt_tokens_details") or {}
    costo = uso.get("cost")
    try:
        costo_f = float(costo) if costo is not None else None
    except (TypeError, ValueError):
        costo_f = None
    return MetadatosLlamada(
        modelo=str(meta.get("model_name") or modelo),
        latencia_s=latencia_s,
        tokens_entrada=_entero(uso.get("prompt_tokens", uso_lc.get("input_tokens"))),
        tokens_salida=_entero(uso.get("completion_tokens", uso_lc.get("output_tokens"))),
        tokens_cache=_entero(detalles.get("cached_tokens")),
        costo_usd=costo_f,
        id_generacion=meta.get("id"),
    )


__all__ = [
    "ENCABEZADOS_OPENROUTER",
    "ESFUERZOS_RAZONAMIENTO",
    "MAX_REINTENTOS",
    "TIMEOUT_S",
    "MetadatosLlamada",
    "crear_modelo_chat",
    "extraer_metadatos",
    "parametro_razonamiento",
]
