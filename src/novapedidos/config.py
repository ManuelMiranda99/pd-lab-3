"""Configuración del servicio, leída de variables de entorno y de `.env`."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    """Variables de entorno del proyecto (ver `.env.example`)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: SecretStr = Field(default=SecretStr(""))
    openrouter_model: str = "google/gemini-2.5-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    #: Razonamiento del modelo en OpenRouter (D49): vacío = el del proveedor; `no` = apagado;
    #: `minimal`/`low`/`medium`/`high` = esfuerzo. Solo aplica a modelos que razonan.
    openrouter_razonamiento: str = ""
    mlflow_tracking_uri: str = "http://localhost:5000"
    #: Experimento de MLflow con las trazas de cada solicitud de la API (D42).
    mlflow_experimento_api: str = "novapedidos-api"
    #: Experimento de MLflow con los runs de evaluación (`eval/correr.py --mlflow`, D44).
    mlflow_experimento_eval: str = "novapedidos-evaluacion"
    #: Trazar las solicitudes de la API en MLflow (con `MLFLOW_TRACKING_URI` no vacía).
    mlflow_trazas: bool = True
    datos_dir: Path = Path("datos")
    #: Intérprete de mensajes que usa la API: `llm` (LangChain + OpenRouter) o `falso`
    #: (tabla fija para demos y pruebas). Ver `DECISIONES.md` D25.
    interprete: str = "llm"

    @property
    def tiene_clave(self) -> bool:
        """Indica si hay una clave de OpenRouter configurada (sin exponer su valor)."""
        return bool(self.openrouter_api_key.get_secret_value().strip())


@lru_cache
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuración (cacheada) del proceso."""
    return Configuracion()
