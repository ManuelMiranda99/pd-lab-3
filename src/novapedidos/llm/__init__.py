"""Intérprete con LangChain + OpenRouter: solo extracción estructurada.

- `interfaz`: protocolo `Interprete` y `ErrorInterprete` (lo que consume la API).
- `interprete`: `InterpreteLLM` y `crear_interprete_llm(config)` (una llamada por pedido).
- `cliente`: `ChatOpenAI` contra OpenRouter y metadatos de cada llamada (tokens, costo).
- `esquema`, `prompt`, `resolucion`, `defensas`: salida estructurada, prompt con el catálogo,
  resolución post-LLM y respaldo ante el filtro de contenido (`DECISIONES.md` D27–D32).
"""

from novapedidos.llm.interfaz import ErrorInterprete, Interprete

__all__ = ["ErrorInterprete", "Interprete"]
