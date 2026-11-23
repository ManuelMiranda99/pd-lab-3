"""Pruebas mínimas: el paquete importa y la configuración se lee sin datos ni clave."""

from pathlib import Path

import novapedidos
from novapedidos.config import Configuracion


def test_paquete_importa():
    assert novapedidos.__version__


def test_configuracion_por_defecto(monkeypatch):
    for var in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "OPENROUTER_BASE_URL", "DATOS_DIR"):
        monkeypatch.delenv(var, raising=False)
    config = Configuracion(_env_file=None)
    assert config.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert config.datos_dir == Path("datos")
    assert config.tiene_clave is False


def test_configuracion_desde_entorno(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "proveedor/modelo-x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("DATOS_DIR", "/datos")
    config = Configuracion(_env_file=None)
    assert config.openrouter_model == "proveedor/modelo-x"
    assert config.tiene_clave is True
    assert config.datos_dir == Path("/datos")
    assert "clave-de-prueba" not in repr(config)
