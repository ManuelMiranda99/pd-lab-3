# QA como evaluador (2026-11-23)

Se repitió lo que hará el equipo docente, sin mirar la implementación. Primero se clonó el repo
en una carpeta limpia, se colocaron los parquet en `datos/` y se hizo `cp .env.example .env` con
la clave. Después se levantó con `docker compose up -d --build` y se enviaron ~120 solicitudes:

- 56 pedidos no publicados con el resultado esperado calculado a mano desde `politicas.md`.
- ~50 variantes de unidades, fracciones e inyección.
- 43 solicitudes inválidas.
- Carga con 4 y 10 solicitudes en paralelo.
- Caídas de MLflow con `stop`, `pause` y `kill`.

Los fallos se corrigieron en código (decisión **D50** de `DECISIONES.md`) y se agregaron como
casos propios P056–P063 y pruebas de pytest.

## Hallazgos y correcciones

| Hallazgo (antes) | Causa | Corrección | Ahora |
|---|---|---|---|
| `2.5 fardos de kola 600` → 7 fardos, `confirmado` | El modelo partía "2.5" en 2 fardos + 5 sueltas | `cantidades_no_enteras`: decimales y fracciones antes del producto → `cantidad_no_convertible`; se descartan las menciones partidas | `requiere_revision`, `cantidad_no_convertible` |
| `2,5 fardos` → 2; `1/2 quintal` → 2; `medio quintal` → 1 | Truncado del modelo; el ancla leía el "2" de "1/2"; `medias_unidades` exigía empaque ≥ 2 | Ancla de cantidades sin `/`, `.` ni `,` antes del número; media unidad con empaque 1 → `no_convertible` | `cantidad_no_convertible` |
| `24 botellitas`/`24 sobrecitos` → 24 fardos/displays | La verificación de unidades no conocía los diminutivos y **revertía** a fardos lo que el modelo entendió como sueltas | Una sola lista de unidades sueltas con diminutivos (botellitas, latitas, sobrecitos, piecitas, envases, frascos) | 1 fardo / 1 display |
| `25 libras de arroz` → 25 fardos; `6 rollos de papel` → 6 fardos | Medidas que no son unidades de venta | Libras, kilos, litros, rollos, galones… antes del producto → `cantidad_no_convertible` | `requiere_revision` |
| `lo de siempre pero de kola 600 que sean 5` → 7 | La corrección solo se detectaba sobre productos pedidos en la misma conversación y con la marca antes | Las líneas de "lo de siempre" cuentan como ya pedidas; marca de corrección también después ("que sean 5") | B01 = 5 |
| `sopa rápida de res` → sopa de pollo; `agua pura de 1 litro` → 1.5 L | Se elegía el producto más parecido | `variantes_no_vendidas`: una presentación (número) o sabor/color/tipo que el producto no tiene → `producto_no_existe` | `producto_no_existe` |
| JSON con `"sku": "B01"` → "No manejamos «Kola Norte 600 ml»" | El modelo reescribió el texto con el nombre del catálogo y el ancla lo tomó como inventado | Si el código del producto está en el mensaje, se acepta (el precio del mensaje se ignora) | B01 × 2 a precio de catálogo |
| `fecha_hora` `9999-12-31T15:00` → 503 `error_interno` | Desborde de `date` al sumar días | Año entre 2000 y 2099; fuera de rango → 422 con motivo | 422 |
| `c001` → 404 pero `" C001 "` → 200 | Criterio inconsistente | `cliente_id` sin espacios y sin distinguir mayúsculas | 200 |
| `/health` `ok` hasta 1 s después de colgar MLflow; `trazas: activas` con MLflow caído | El `ok` se guardaba en caché | Solo se guardan en caché las fallas; `trazas: en espera (MLflow no disponible)` | `degradado` desde la primera consulta |
| Pico de 16 s en un pedido | Un intento colgado esperaba hasta el timeout de 15 s | Timeout por intento de 8 s (p99 normal < 2 s) | máx 1.5 s en la re-evaluación |
| Pedido cancelado, 0 o cantidad negativa → "atenderá su consulta" | Plantilla de consulta | Plantilla de pedido vacío: "No registramos productos en su pedido…" | Sigue `fuera_de_alcance` (D19) |

## Sin cambio (decisión)

- **`coca cola 600` → Kola Norte 600 (B01).** `politicas.md` §1 trata "coca" como nombre
  informal de las kolas (es su ejemplo de ambigüedad), así que "coca 600" es B01. En cambio,
  una marca que no se vende (`pepsi 600`) da `producto_no_existe`.
- **`pendientes[].texto`** es el fragmento que nombra el producto (`"aceite"`), igual que en
  los casos dev (D09; documentado en el README).
- **`name: novapedidos`** en `docker-compose.yml` se mantiene: nombra los contenedores y el
  volumen, y es el nombre que usan los scripts. Para no compartir el volumen con otra copia, el
  README explica cómo usar `-p`.

## Re-evaluación con los cambios (Docker, por HTTP)

`reporte.txt` y `resumen.json`: corrida `qa_evaluador_docker` contra la API en Docker Compose
(puertos 8002/5002) con 4 solicitudes en paralelo. Resultado: **185/185 (100 %)**. Por conjunto:
dev 84/84, variantes 24/24, control 14/14 y propios 63/63. Integridad de precios 100 %, 0
errores, p50 0.78 s, p95 1.03 s y máximo 1.54 s.

Con el mismo stack también pasaron:

- los 56 casos del QA;
- las 43 solicitudes inválidas: 422 o 404 según el caso, y ninguna terminó en 500 ni 503;
- `scripts/probar_resiliencia_docker.py`.
