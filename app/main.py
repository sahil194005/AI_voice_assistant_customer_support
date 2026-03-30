from fastapi import FastAPI

from app.api.voice_routes import router as voice_router

app = FastAPI()
app.include_router(voice_router)
