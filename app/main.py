from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings

app = FastAPI(title=get_settings().app_name)
app.include_router(health_router)
