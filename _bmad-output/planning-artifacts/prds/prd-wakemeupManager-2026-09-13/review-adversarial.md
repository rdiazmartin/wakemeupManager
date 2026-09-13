# Review adversarial — wakemeupManager

Revisión de seguridad y edge-cases sobre `prd.md` (r2 pendiente de aplicar). Fuente: subagente adversarial.

## Verdict general

El PRD es sólido a nivel de producto y sus decisiones (WOL local, password de un solo uso) son correctas, pero sus garantías de seguridad estaban descritas de forma más optimista que defendible — la mayor brecha conceptual era tratar el inventario (IP) como identidad de máquina sin fijar la clave de host SSH. Los OQ2/3/4 ya estaban medio-respondidos por el propio documento y debían cerrarse; las métricas de éxito necesitaban método de medición explícito.

## Hallazgos

1. **[high]** Manejo de password de un solo uso no era lo suficientemente seguro: `ssh-copy-id` no lee stdin directamente, sshpass debe inyectar vía pty y la password queda en memoria del proceso (legible por procesos del mismo UID vía `/proc/PID/mem`/strace); la vía de fichero es más segura pero quedaba indefinido su borrado/ubicación. — §4.3/FR-6 — Resuelto: fichero temporal 600 con `O_EXCL` + borrado inmediato con `sshpass -f`; se documenta la exposición residual en memoria.
2. **[high]** Sin fijación de identidad de host: el apagado SSH iba a la IP del inventario sin pinning de known_hosts; tras re-IP por DHCP el BE podía apagar la máquina equivocada, y el alta era medianamente MITM-able. — FR-1/FR-7/§4.3 — Resuelto: fingerprint fijado en el alta, apagado solo si coincide, IP con estado obsoleto → offline.
3. **[high]** Token en claro: sin HTTPS, esnifable en la LAN; la API se servía deliberadamente en tailnet + LAN local, ampliando la exposición. — §4.4/FR-9, FR-10 — Resuelto: BE escucha solo en interfaz tailnet + loopback (cifrado WireGuard); fuera de alcance la LAN física.
4. **[medium]** Modelo de token contradictorio ("un único token por usuario" vs "sin multi-cuenta"); en la práctica un token copiado entre móviles y un móvil perdido obligaba a rotar para todos. — FR-10 — Resuelto: token por dispositivo, revocación individual.
5. **[medium]** BE no-root vs escaneo ARP: arp-scan/scapy necesitan root/CAP_NET_RAW y FR-8 no contaba una historia de capabilities. — FR-1/FR-8 — Resuelto: ping + `/proc/net/arp` sin privilegios.
6. **[medium]** Sin autoexclusión del propio BE en el escaneo: su IP/MAC (y la interfaz tailnet) aparecerían en el inventario y se podrían gestionar (apagar el controlador remotamente). — FR-1 — Resuelto: exclusión de interfaces propias y direcciones no-LAN.
7. **[medium]** Sin anti-bruteforce: FR-10 solo especificaba 401; sin throttling ni lockout. — FR-10/OQ2 — Resuelto: backoff 5 fallos/5 min → 429/15 min.
8. **[medium]** Métricas sin método de medición: SM-1 requería atribución por persona inexistente; SM-2/3/4 sin fuente; los logs de FR-11 nunca se vinculaban como instrumentación. — §7/FR-11 — Resuelto: medición declarada sobre logs FR-11 + tokens por dispositivo; SM-1 a nivel dispositivo; SM-4 solo numerador.
9. **[medium]** OQ3 ya respondido en el cuerpo: FR-3 (inventario sobrevive) y FR-6 (estado gestionado perdura) implican persistencia duradera; solo era mecanismo. — §8/OQ3 — Resuelto: SQLite, requisito declarado en §6.1 y FR-6.
10. **[low]** Carreras de escaneos concurrentes: FR-1 garantizaba no-bloqueo de wake/shutdown, pero el periódico y el forzado podían solaparse y corromper/duplicar escrituras del inventario. — FR-1/FR-3 — Resuelto: singleflight, un escaneo a la vez.
11. **[low]** sudoers NOPASSWD prerrequisito real para SM-4 pero enmarcado como advertencia; OQ4 preguntaba algo ya resuelto (nunca hubo auto-configuración). — FR-7/OQ4 — Resuelto: prerrequisito documentado en el flujo de alta; OQ4 eliminado.
12. **[low]** Sin validación de MAC en wake: MAC malformada/multicast/cero enviaba paquete inútil al broadcast. — FR-4 — Resuelto: validación con 422.
