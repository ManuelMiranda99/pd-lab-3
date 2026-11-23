"""Guarda una captura PNG de una URL con Playwright (usa el Google Chrome instalado).

Uso:
    uv run python scripts/capturar.py <url> <salida.png> [--ancho 1440] [--alto 900]
        [--espera-ms 2500] [--pagina-completa]

Lo usa `/cerrar-fase` para dejar evidencia en `evidencia/sesiones/<carpeta>/`.
No requiere `playwright install`: abre Chrome con `channel="chrome"`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def parsear_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Captura PNG de una URL con Chrome headless.")
    parser.add_argument("url", help="URL a capturar (http://, https:// o file://)")
    parser.add_argument("salida", type=Path, help="Ruta del PNG de salida")
    parser.add_argument("--ancho", type=int, default=1440, help="Ancho del viewport (px)")
    parser.add_argument("--alto", type=int, default=900, help="Alto del viewport (px)")
    parser.add_argument(
        "--espera-ms", type=int, default=2500, help="Espera tras cargar antes de capturar (ms)"
    )
    parser.add_argument(
        "--pagina-completa", action="store_true", help="Captura toda la página, no solo el viewport"
    )
    return parser.parse_args(argv)


def capturar(
    url: str,
    salida: Path,
    ancho: int = 1440,
    alto: int = 900,
    espera_ms: int = 2500,
    pagina_completa: bool = False,
) -> Path:
    """Abre `url` en Chrome headless y guarda la captura en `salida`."""
    salida.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        navegador = p.chromium.launch(channel="chrome", headless=True)
        try:
            pagina = navegador.new_page(viewport={"width": ancho, "height": alto})
            pagina.goto(url, wait_until="load", timeout=30_000)
            pagina.wait_for_timeout(espera_ms)
            pagina.screenshot(path=str(salida), full_page=pagina_completa)
        finally:
            navegador.close()
    return salida


def main(argv: list[str] | None = None) -> int:
    args = parsear_argumentos(argv)
    try:
        ruta = capturar(
            args.url, args.salida, args.ancho, args.alto, args.espera_ms, args.pagina_completa
        )
    except PlaywrightError as error:
        print(f"Error al capturar {args.url}: {error}", file=sys.stderr)
        return 1
    tamano = ruta.stat().st_size
    if tamano == 0:
        print(f"La captura {ruta} quedó vacía.", file=sys.stderr)
        return 1
    print(f"Captura guardada: {ruta} ({tamano:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
