# Despliegue operacional del BE (story 4.1)

Checklist para instalar el BE como servicio `systemd` en el host de destino
(un Raspberry / NucBox en la tailnet). **No ejecutar sobre la máquina de
desarrollo**: el instalador crea usuario, claves y unidad, y habilita un
servicio.

## Requisitos previos

- Linux con `systemd` y `python3` ≥ 3.14 (o el Python que `uv` gestiona).
- [`uv`](https://docs.astral.sh/uv/) instalado (`/usr/local/bin/uv` o en `PATH`).
- `tailscale` instalado y con la máquina dentro de la tailnet (`tailscale up`).
- El checkout del repo en el host (el instalador lo usa como `--app-dir`).
- La app/el agente MCP alcanzan el BE por la tailnet (NFR-1/AD-6): el bind es
  **solo tailnet + loopback**, nunca `0.0.0.0` ni la LAN física.

## Instalación (idempotente)

```bash
sudo backend/deploy/install.sh
```

El instalador (`wakemeup-install`, en `src/wakemeup/install.py`) hace:

1. Detecta la IP tailnet (`tailscale ip -4`) y el `DNSName`/MagicDNS
   (`tailscale status --json`) y escribe
   `[api] bind_hosts=[<tailnet>, "127.0.0.1"]` y
   `extra_allowed_hosts=[<dnsname>]`. Sin `tailscale` avisa y deja solo-loopback
   (el operador completa la config).
2. Crea el usuario de sistema `wakemeup` (si falta).
3. Prepara `/var/lib/wakemeup/` y `/etc/wakemeup/`.
4. Genera el par Ed25519 del BE (privada `600` en directorio `700`) si falta.
5. Escribe `/etc/wakemeup/config.toml` **solo si no existe** (nunca la pisa).
6. `uv sync --frozen` en el proyecto (reproduce `uv.lock`).
7. Escribe la unidad `/etc/systemd/system/wakemeup.service`, `daemon-reload` y
   `systemctl enable --now`.
8. Healthcheck `GET http://127.0.0.1:8080/api/v1/status` → 200.

Opciones: `--app-dir`, `--user`, `--data-dir`, `--config-path`, `--unit-path`,
`--port`, `--uv-path`.

## Verificación

```bash
systemctl status wakemeup               # active (running)
journalctl -u wakemeup -n 50            # sin credenciales ni claves
curl -fsS http://127.0.0.1:8080/api/v1/status          # 200
curl -fsS http://<ip-tailnet>:8080/api/v1/status       # 200 desde la tailnet
```

Desde la tablet (logueada a Tailscale): abrir la app con la URL
`http://<ip-tailnet>:8080` debe devolver el inventario.

## Idempotencia

Re-ejecutar `install.sh`:

- NO regenera el par Ed25519 (se conserva la clave: re-enrollar máquinas
  rompería las `authorized_keys` ya instaladas).
- NO pisa `/etc/wakemeup/config.toml`.
- NO duplica la unidad; solo la reescribe si su contenido cambia.
- NO borra la DB (`/var/lib/wakemeup/wakemeup.db`) ni el inventario.

## Arranque tolerante a la tailnet tardía

El runner `wakemeup.server` bindea `[api] bind_hosts` con reintento acotado
(`bind_retry_seconds`, 30 s por defecto) y degrada a loopback con warning si la
interfaz tailnet no aparece; **nunca aborta** (evita el crash-loop con
`Restart=always`). La unidad lleva `After=tailscaled.service`.

## Reversión sobre `testing` (192.168.0.39)

Nada preexistente se toca: el instalador solo crea rutas/usuario/servicio
dedicados. Para deshacer:

```bash
sudo systemctl disable --now wakemeup
sudo rm /etc/systemd/system/wakemeup.service && sudo systemctl daemon-reload
sudo rm -rf /etc/wakemeup /var/lib/wakemeup /opt/wakemeup
sudo userdel --remove wakemeup
```

Si el host ya tenía `wakemeup` u otra ruta reutilizada, revisar antes de borrar
para no arrastrar datos de otra instalación.

## Solución de problemas

- `ningún bind_hosts asignable`: la IP tailnet no existe todavía o está mal en
  la config; comprobar `tailscale ip -4` y `systemctl status wakemeup`.
- Healthcheck fallido: `journalctl -u wakemeup -n 100`; revisar que el puerto
  no esté ocupado (`ss -ltnp | grep 8080`).
- El MCP rechaza `Host` del MagicDNS: añadir el nombre a
  `[api] extra_allowed_hosts` (el instalador lo hace automáticamente).
