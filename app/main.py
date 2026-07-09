import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.db.session import init_db
from app.web.routes import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initialisation de la base de données...")
    await init_db()
    yield


app = FastAPI(
    title="MediaGuard",
    description="Gestionnaire d'état Sonarr/Radarr pour réduire la charge et optimiser les médias",
    version="1.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(web_router)
app.include_router(api_router, prefix="/api/v1")
