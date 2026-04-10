from fastapi import FastAPI

from app.api.voice_routes import router as voice_router
from app.utils.logging_utils import configure_logging

configure_logging()

app = FastAPI()
app.include_router(voice_router)
