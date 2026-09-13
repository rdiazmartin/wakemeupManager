- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-esqueleto-del-be.md`
  summary: Definir política de exención de auth para el healthcheck GET /api/v1/status cuando llegue el middleware de tokens (story 1.4).
  evidence: FR-10 exige token en toda petición; el healthcheck es anónimo por utilidad operativa (systemd Restart/healthcheck). Decidir lista de exenciones explícita en 1.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-descubrimiento-de-maquinas.md`
  summary: Ruta del fichero SQLite CWD-relative: fijarla con config `[db]` e instalador (story 1.4/4.1).
  evidence: Db() usa Path("wakemeup.db") relativo al CWD; en despliegue systemd WorkingDirectory=/var/lib/wakemeup la DB caería ahí sin estar documentado. Decidir ruta + knob env en 1.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-descubrimiento-de-maquinas.md`
  summary: `_in_flight` reporta False durante la fase de upsert de un escaneo en curso.
  evidence: verificado en revision 1.2 (verification-gap): _in_flight se limpia al terminar scan_range, antes del upsert; el endpoint POST /scan de la story 1.4 deberá consumir estado "scanning" real de la tarea _current en lugar de in_flight.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-descubrimiento-de-maquinas.md`
  summary: own_interfaces con rc!=0 (o timeout) devuelve set() silencioso: el BE podría no auto-excluirse.
  evidence: riesgo asumido y documentado; mitigación parcial (loopback/tailnet se excluyen por rango propio). Revisar con el instalador 4.1.
