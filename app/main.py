from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.external_holders import router as external_holders_router
from app.api.health import router as health_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router)
    application.include_router(external_holders_router)
    return application


app = create_app()
