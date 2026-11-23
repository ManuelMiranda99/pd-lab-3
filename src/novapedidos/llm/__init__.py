"""Intérprete con LangChain + OpenRouter: solo extracción estructurada.

Por ahora solo expone la interfaz (`Interprete`, `ErrorInterprete`) que consume la API.
"""

from novapedidos.llm.interfaz import ErrorInterprete, Interprete

__all__ = ["ErrorInterprete", "Interprete"]
