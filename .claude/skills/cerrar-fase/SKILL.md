---
name: cerrar-fase
description: Cierra la sesión de trabajo del Laboratorio 3 (NovaDistribuidora). Verifica lo hecho (tests, servicios, git), toma capturas de pantalla del trabajo, actualiza PLAN.md y BITACORA.md, y entrega un resumen de lo logrado, de todo lo que falló y de cómo verificarlo. Úsalo al terminar, con "/cerrar-fase".
argument-hint: "[notas opcionales sobre la sesión]"
---

# /cerrar-fase — Cerrar la sesión

Objetivo: dejar **registrado, verificado y con evidencia visual** lo logrado, ser honesto sobre lo
que falló y que la próxima `/iniciar-fase` sepa por dónde seguir. Responde siempre en español.

## Paso 1 — Reunir evidencia (en paralelo, solo lectura)

1. `PLAN.md` y la entrada "_(sesión abierta)_" de hoy en `BITACORA.md` (si no hay, crear una al final).
2. Git: `git status --short`, `git diff --stat`, commits de la sesión.
3. El **reporte del subagente** de la fase (si se delegó) y lo de esta conversación: archivos
   tocados, comandos, decisiones y **todos los fallos** (tests
   rojos, errores de comandos, llamadas al LLM fallidas, cosas que no se pudieron verificar),
   incluso los que se resolvieron.
4. `$ARGUMENTS`: notas del usuario.

## Paso 2 — Verificar (no confiar en la memoria de la conversación)

- `uv run pytest -q` si hay `tests/`: anotar el resultado real.
- Si hay contenedores: `docker compose ps` y `curl -s localhost:8000/health`.
- Si la fase tocó la evaluación: el último reporte en `eval/resultados/` (exactitud global y por tipo).
- Cada tarea a marcar `[x]` debe cumplir su criterio; si no se pudo verificar, queda `[~]` y se dice.

## Paso 3 — Capturas de pantalla (cuando aporten)

Guardar en `evidencia/sesiones/AAAA-MM-DD_FN/` según lo trabajado:

| Si la fase tocó… | Capturar |
|---|---|
| API (F4+) | `http://localhost:8000/docs` y la respuesta de un caso |
| MLflow (F8+) | lista de trazas, una traza abierta, el run de evaluación |
| Comparación (F10) | vista "Compare runs" de MLflow y las gráficas |
| Tests / eval | salida de `pytest` o del reporte guardada como `.txt` (no hace falta imagen) |

Cómo capturar:
1. Preferido: `uv run python scripts/capturar.py <url> evidencia/sesiones/<carpeta>/<nombre>.png`
   (Playwright headless; existe desde F0.5).
2. Alternativa: el navegador integrado (`mcp__Claude_Browser__*`) para revisar la página y
   tomar la captura en el chat.
Enviar las capturas a la persona con `SendUserFile` (display `render`). Si el servicio no está
arriba o la captura falla, **decirlo** en "Lo que falló"; no inventar evidencia.

Las capturas de interés para la entrega (trazas, evaluaciones, comparación) se copian además a
`evidencia/` con un nombre descriptivo.

## Paso 4 — Actualizar PLAN.md y DECISIONES.md

- `[x]` lo terminado y verificado, `[~]` lo empezado. Tareas nuevas con el siguiente ID libre.
- Decisiones resueltas: marcar en "Riesgos y decisiones abiertas" y documentar en `DECISIONES.md`
  como **decisión · alternativas · justificación**.

## Paso 5 — Entrada en BITACORA.md

Reemplazar la entrada abierta de hoy por:

```
## Sesión AAAA-MM-DD — Fase N (<inicio|retoma>)
**Fase:** N — <nombre>
**Estado al cerrar:** <Fase N cerrada | en progreso: X de Y tareas>

### Logrado
- FN.a <qué se hizo, archivos clave>

### Verificación
- pytest: <N pasan / M fallan>
- eval: <exactitud dev / propios, si aplica>
- otras: <health, compose ps, latencia>

### Lo que falló
- <qué falló> · <error resumido> · <resuelto: cómo | sin resolver: por qué>
  (o "nada")

### Capturas
- evidencia/sesiones/AAAA-MM-DD_FN/<archivo>.png — <qué muestra>

### Decisiones tomadas
- <decisión> → DECISIONES.md §<sección> (o "ninguna")

### Próxima sesión
- Empezar por: <tarea con ID y primer comando/archivo>
- Luego: <2–3 tareas>
- Tener en cuenta: <riesgos>
```

## Paso 6 — Commit, push y PR (con permiso; nunca a `main` después de la Fase 0)

1. Auditar lo que entra: nada de `datos/`, `.parquet`, `.pdf`, `.env`, `mlruns/`. Sí entran
   `PLAN.md`, `BITACORA.md`, `CLAUDE.md`, `.claude/`, `evidencia/`. Nunca `git add -f`.
2. Proponer un mensaje convencional en español y **preguntar** antes de commitear.
3. Con el sí: commit → `git push -u origin <rama>` → proponer PR (`gh pr create --base main`) y
   crearlo solo con permiso. Mostrar `gh pr checks` sin esperar.

## Paso 7 — Resumen final al usuario

```
## Cierre de sesión — AAAA-MM-DD
**Fase N — <nombre>:** <cerrada ✅ | X de Y tareas (▓▓▓░░░)>

### Lo que se obtuvo
- ...

### ❌ Lo que falló
- <qué> — <estado: resuelto / pendiente> — <siguiente paso>
(o "Nada falló en esta sesión")

### 🖼️ Capturas
- <ruta> — <qué muestra>

### Cómo verificarlo tú
- <comando / URL / archivo concreto>

### Próxima sesión
1. ...

**Avance global:** <[x] totales> / <tareas totales> · fases cerradas: <lista>
```

## Reglas

- Resultados reales: si algo falló o no se verificó, se dice tal cual.
- No marcar `[x]` sin evidencia. No borrar entradas anteriores de la bitácora.
- Cerca de la entrega, recordar F11.5 (tag `entrega-lab3`, repo accesible, enlace en el GES).
