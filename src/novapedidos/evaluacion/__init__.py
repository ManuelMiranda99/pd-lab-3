"""Evaluación local del servicio (`DECISIONES.md` D33–D36).

- `calificadores`: funciones puras que comparan una respuesta con su `esperado` (0/1 + detalle),
  la integridad de precios contra el catálogo y la latencia. Pensadas para reutilizarse como
  *scorers* de MLflow en la Fase 8.
- `mensaje_cliente`: calificador por reglas del `mensaje_cliente`.
- `casos`: lectura de `.jsonl` y construcción de casos con `esperado` calculado por el motor.
- `ejecutores`: corren un caso en proceso (intérprete + motor) o contra la API por HTTP.
- `corrida`: corre un conjunto con concurrencia, agrega métricas y escribe resultados/reporte.
- `cli`: línea de comandos (`uv run python eval/correr.py …`).
"""
