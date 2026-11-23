---
name: iniciar-fase
description: Inicia o retoma una sesión de trabajo del Laboratorio 3 (NovaDistribuidora, pedidos por WhatsApp con LLMs). Lee PLAN.md y BITACORA.md, detecta la fase actual o la indicada, explica el objetivo de la fase, resume rápidamente lo que se hará y registra la sesión. Úsalo al comenzar a trabajar, con "/iniciar-fase" o "/iniciar-fase 3".
argument-hint: "[número de fase opcional, p. ej. 3]"
---

# /iniciar-fase — Iniciar o retomar una fase

Objetivo: que en menos de un minuto la persona sepa **qué busca la fase, qué se hará hoy y cómo
podrá verificarlo**. Responde siempre en español.

## Paso 1 — Reunir contexto (en paralelo, solo lectura)

Desde la raíz del proyecto (la carpeta con `PLAN.md`):
1. `PLAN.md` completo.
2. `BITACORA.md`, sobre todo la **última entrada** y su "Próxima sesión".
3. `CLAUDE.md` y `DECISIONES.md` si existen.
4. Si es repo git: `git status -sb`, `git log --oneline -10`, rama actual; `gh pr status` si hay remoto.
5. Desde la Fase 8: `docker compose ps` (sin levantar nada).

Si `PLAN.md` o `BITACORA.md` no existen, avisar y detenerse.

## Paso 2 — Determinar la fase

- Con argumento (`$ARGUMENTS`), esa fase. Sin argumento: la **primera fase con tareas `[ ]` o `[~]`**.
- **Inicio** si todas sus tareas están `[ ]`; si no, **retoma**.
- Si las fases previas tienen pendientes que son prerrequisito, advertirlo y preguntar.

## Paso 3 — Revisar el estado real

- Contrastar bitácora vs disco/git (tareas `[x]` sin archivos, cambios sin commitear).
- Si existe `tests/`: `uv run pytest -q` (si tarda > 2 min, en segundo plano o saltar y decirlo).
- Si la fase usa OpenRouter y no existe `.env` o no tiene `OPENROUTER_API_KEY`, avisarlo antes de empezar.

## Paso 4 — Briefing (lo primero que ve la persona)

```
## Fase N — <nombre> · <Inicio | Retoma>

**🎯 Objetivo de la fase:** <1–2 líneas, del PLAN, en lenguaje simple>
**📋 Requisitos del PDF que cubre:** <de la línea "Requisito PDF">
**Avance:** X de Y tareas (▓▓▓░░░)

### Dónde quedamos
<2–4 viñetas: "Logrado" y "Pendientes" de la última bitácora; estado de git/tests>

### Resumen rápido de lo que se hará hoy
1. <tarea con ID> — <qué se construye, en una línea>
2. ...
(3–5 pasos alcanzables en una sesión)

### Cómo lo vas a poder verificar tú
- <de "Verificación manual" del PLAN: comando, URL o archivo concreto>

### Decisiones abiertas que afectan hoy
<solo si las hay>
```

## Paso 5 — Registrar el inicio

1. Marcar `[~]` en `PLAN.md` la primera tarea a trabajar.
2. Crear la carpeta `evidencia/sesiones/AAAA-MM-DD_FN/` (fecha real de hoy) para las capturas.
3. Añadir al final de `BITACORA.md` (si ya hay una entrada de hoy "_(sesión abierta)_", retomarla):

```
---

## Sesión AAAA-MM-DD — Fase N (<inicio|retoma>)
**Fase:** N — <nombre>
**Estado al cerrar:** _(sesión abierta)_
**Plan del día:** FN.a, FN.b, ...
```

## Paso 6 — Rama de trabajo (después de la Fase 0 nunca se trabaja en `main`)

- En `main`: `git switch main && git pull --ff-only` y crear rama convencional (`feat/motor-calculos`,
  `test/...`, `docs/...`). Sin IDs de tarea en el nombre.
- Si ya hay una rama con PR abierto de la sesión anterior, retomarla.
- Excepción: el commit inicial de la Fase 0 va directo en `main`.

## Paso 7 — Arrancar: delegar la fase a un subagente

La sesión principal **orquesta**; cada fase la implementa **un subagente** (herramienta `Agent`,
`subagent_type: "general-purpose"`) para que el contexto de cada fase no se mezcle y se puedan
hacer varias fases en una misma sesión.

1. Si el usuario no pidió otra cosa, lanzar el subagente con un brief autocontenido:
   - Fase, objetivo, tareas pendientes con su ID y su criterio de "Verificación manual" (copiados del PLAN).
   - Reglas duras del lab y de `CLAUDE.md`; decisiones relevantes de `DECISIONES.md`.
   - Rama de trabajo; **no hacer commit, push ni PR** (eso lo hace `/cerrar-fase` con permiso).
   - Modelos: durante el desarrollo, el modelo barato de `OPENROUTER_MODEL` en `.env`.
   - Nunca leer ni imprimir el valor de `OPENROUTER_API_KEY`.
   - Debe terminar con un **reporte**: tareas hechas (con archivos), resultado real de tests/comandos,
     **lista de todo lo que falló** (comando, error resumido, resuelto o no), decisiones tomadas,
     URLs o pantallas que valga la pena capturar y la sugerencia de "Próxima sesión".
2. Fases independientes (sin dependencia entre sí ni archivos compartidos) pueden ir en paralelo;
   fases dependientes van una tras otra.
3. Al volver cada subagente, la sesión principal **no confía en el reporte**: revisa el diff y corre
   las pruebas, y luego aplica `/cerrar-fase` para esa fase (bitácora, fallos, capturas).
4. Si quedan fases y el usuario quiere seguir, repetir `/iniciar-fase` con la siguiente.

## Reglas

- No inventar avance: el estado sale de `PLAN.md`, bitácora, git y tests.
- No hacer commits ni push en este comando.
- Recordar las reglas duras cuando apliquen: cálculos solo en código, salida estructurada, nunca
  inventar sku, fecha con `fecha_hora` (no el reloj), contrato exacto del Anexo, MLflow caído no
  rompe la API, datos y `.env` fuera del repo.
