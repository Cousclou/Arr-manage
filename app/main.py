import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.db.session import async_session, init_db
from app.services.runtime_config import RuntimeConfig
from app.utils.timezone import set_request_timezone
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


@app.middleware("http")
async def timezone_middleware(request: Request, call_next):
    if not request.url.path.startswith("/static"):
        try:
            async with async_session() as db:
                cfg = RuntimeConfig(db)
                set_request_timezone(await cfg.get_timezone())
        except Exception:
            logger.exception("Impossible de charger le fuseau horaire")
    return await call_next(request)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")
