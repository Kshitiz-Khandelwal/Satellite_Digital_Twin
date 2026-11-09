"""Main application entry point for ORBITAL - Satellite Digital Twin."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.api.websocket import router as ws_router
from app.api.websocket import start_telemetry_loop

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start background telemetry loop
    start_telemetry_loop()
    yield
    # Shutdown logic if needed


app = FastAPI(
    title="ORBITAL - Satellite Digital Twin",
    description="Extended Kalman Filter, SGP4 Orbit Propagation, and Real-Time Subsystem Health Digital Twin",
    version="2.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Routers
app.include_router(api_router)
app.include_router(ws_router)

# Mount Static Files
if (FRONTEND_DIR / "static").exists():
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
    return {
        "status": "healthy",
        "service": "ORBITAL Satellite Digital Twin",
        "version": "2.0.0",
    }
