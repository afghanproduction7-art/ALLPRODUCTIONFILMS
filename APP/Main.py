import logging

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.bot import start_bot
from app.database import init_db

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="ALL PRODUCTION FILMS",
    version="1.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)


@app.on_event("startup")
async def startup():
    await init_db()


@app.get("/")
async def home():
    return FileResponse(
        "static/index.html"
    )


@app.get("/health")
async def health():
    return JSONResponse(
        {
            "status": "ok",
            "project": "ALL PRODUCTION FILMS",
            "version": "1.0.0",
        }
    )


@app.get("/api")
async def api_info():
    return {
        "project": "ALL PRODUCTION FILMS",
        "status": "online",
        "version": "1.0.0",
    }
