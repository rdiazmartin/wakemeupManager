- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-esqueleto-del-be.md`
  summary: Definir política de exención de auth para el healthcheck GET /api/v1/status cuando llegue el middleware de tokens (story 1.4).
  evidence: FR-10 exige token en toda petición; el healthcheck es anónimo por utilidad operativa (systemd Restart/healthcheck). Decidir lista de exenciones explícita en 1.4.
