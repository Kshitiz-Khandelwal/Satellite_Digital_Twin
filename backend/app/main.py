from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"

app = FastAPI(
    title="ORBITAL - Satellite Digital Twin",
    description="Satellite and ISS Monitoring Dashboard",
    version="1.0.0",
)

app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR / "static"),
    name="static",
)


@app.get("/")
async def dashboard():
    return FileResponse(FRONTEND_DIR / "templates" / "index.html")


@app.get("/health")
async def health():
    return {"status": "healthy"}
