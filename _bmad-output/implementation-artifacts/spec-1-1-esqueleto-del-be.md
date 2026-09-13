---
title: '1-1-esqueleto-del-be'
type: 'chore'
created: '2026-09-13'
status: 'done'
route: 'oneshot'
review_loop_iteration: 1
context: []
---

## Intent

**Problem:** No existe todavía el backend de wakemeupManager; el repo solo contiene planificación.

**Approach:** Crear el esqueleto del BE en `backend/`: proyecto uv con Python 3.14, estructura hexagonal `src/wakemeup/{api,services,adapters,core,cli}`, `config.toml.example` con las secciones `[scan] [api] [ssh] [auth]`, una app FastAPI mínima con `GET /status` (healthcheck) y la unidad systemd `deploy/wakemeup.service` (usuario `wakemeup`, Restart=always, Wants=network-online.target).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Healthcheck | `GET /status` sin token | 200 con JSON `{"status":"ok","version":"0.1.0"}` | — |
| Config ausente | arranque sin `config.toml` | el BE arranca con defaults de código y loguea "usando defaults" | — |

## Tasks & Acceptance

**Execution:**
- [ ] `backend/pyproject.toml` — proyecto uv con Python 3.14 y dependencias base (fastapi, uvicorn, pydantic-settings; dev: pytest, httpx)
- [ ] `backend/src/wakemeup/api/__init__.py` + `app.py` — app FastAPI, `GET /status`
- [ ] `backend/src/wakemeup/{services,adapters,core,cli}/__init__.py` — paquetes del esqueleto hexagonal
- [ ] `backend/config/config.toml.example` — secciones `[scan] [api] [ssh] [auth]` con defaults del spine
- [ ] `backend/deploy/wakemeup.service` — unidad systemd (usuario `wakemeup`, `Restart=always`, `Wants=network-online.target`)
- [ ] `backend/tests/test_status.py` — pytest instancia la app (TestClient) y verifica `/status` 200
- [ ] `backend/.gitignore` — ignorar `.venv`, `__pycache__`, `.env`

**Acceptance Criteria:**
- Given el repo sin backend, when se ejecuta `uv run pytest`, then todos los tests pasan (al menos el de `/status`).
- Given `GET /status`, then responde 200 con JSON `{"status":"ok","version":"0.1.0"}`.
- Given la estructura `src/wakemeup/{api,services,adapters,core,cli}`, then respeta la forma hexagonal (dependencias adaptadores → servicios → núcleo, sin ciclos).
- Given `config.toml.example`, then expone las secciones `[scan] [api] [ssh] [auth]` con default de escaneo 10 min, TTL 60 s, backoff 5/5 min → 429/15 min.
- Given `deploy/wakemeup.service`, then define usuario `wakemeup`, `Restart=always`, `Wants=network-online.target`.
- Given la story completa, then se ejecuta commit + push obligatorio (requisito transversal del usuario).

## Implementation Notes

- Layout `src/` con hatchling como build backend (`[tool.hatch.build.targets.wheel] packages = ["src/wakemeup"]`) para que uv instale el paquete en editable; sin esto `tests/` no importa `wakemeup` con layout src.
- Healthcheck expuesto bajo el contrato AD-1: `GET /api/v1/status` (rutas verbatim de la arquitectura), no `/status`. El prefijo se parametriza desde `[api] prefix` cuando la config se cargue (story 1.2).
- Ruta del AD-4: el comando de apagado vive en la sección `[shutdown] command` (alineado con el spine) — en el ejemplo anterior estaba en `[ssh] shutdown_command` (incoherencia detectada en review).
- Unidad systemd: eliminado `ProtectHome=true` (rompía el ExecStart con uv en ~wakemeup/.local); rutas finales marcadas con TODO(4.1) para la story de instalador.
- `.python-version` = 3.14 para reproducibilidad de uv.
- Warnings heredados del ecosistema (starlette/httpx2, anyio BlockingPortal) en TestClient: no bloqueantes en v1, revisar al fijar pin de CI.
- Pendiente (defer): healthcheck exento de auth — FR-10 obliga token en "toda petición"; se resuelve con el middleware de auth de la story 1.4 (listado de exenciones).


## Spec Change Log

## Review Triage Log

- `AD-1 /api/v1` (healthcheck en ruta errónea) — **high** — verificado: el test confirmaba `/status` y divergía de la arquitectura. Parcheado: ruta → `/api/v1/status`, test actualizado.
- `ProtectHome=true` rompe ExecStart — **high** — verificado: uv vive en ~wakemeup/.local. Parcheado: quitado, nota TODO(4.1).
- "Defaults de código" no implementados — **medium** — verificado: pydantic-settings declarado sin Settings. Admitido explícito en config.example; el modelo se integra en la story 1.2 con carga real.
- `[ssh] shutdown_command` vs `[shutdown] command` del AD-4 — **medium** — verificado contra el spine. Parcheado: sección `[shutdown] command`.
- Unidad con host/port hardcodeados vs config — **low** — aceptado con TODO(4.1); la unidad es referencia de despliegue, la parametrización llega con el instalador.
- Healthcheck vs FR-10 (auth total) — **medium** — diferido a 1.4: middleware de auth con lista de exenciones; registrado en Implementation Notes y defer-work.
- Tasks del spec `[ ]` sin marcar — **low** — el checklist se marca al cerrar; no afecta al entregable.
- Versión duplicada en test — **low** — parcheado: el test importa `wakemeup.__version__`.
- Sin `.python-version` — **medium** — parcheado: fijado `3.14`.
- Warnings de TestClient — **low** — diferido: pin de CI en story 4.x; anotado.

