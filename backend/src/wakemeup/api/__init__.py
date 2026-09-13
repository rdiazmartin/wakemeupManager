"""App FastAPI del BE de wakemeupManager.

Esqueleto: expone el healthcheck bajo el prefijo de API del contrato (AD-1:
`/api/v1/status`). Los endpoints de inventario, escaneo, encendido y apagado se
añaden en stories posteriores del Epic 1/2; entonces el prefijo se parametriza
desde la configuración ([api] prefix).
"""
from fastapi import APIRouter, FastAPI

from wakemeup import __version__

app = FastAPI(title="wakemeup-backend", version=__version__)

api = APIRouter(prefix="/api/v1")


@api.get("/status")
def status() -> dict:
    """Healthcheck: vivo y versión."""
    return {"status": "ok", "version": __version__}


app.include_router(api)
