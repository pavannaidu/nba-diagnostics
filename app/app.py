"""FastAPI entry point for the IDEXX next-best-action Databricks App."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import get_settings
from backend.routes import router


settings = get_settings()
app = FastAPI(
    title=settings.app_title,
    version="0.1.0",
    summary="Patient-visit next-best diagnostic action demo for synthetic IDEXX dog data.",
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")


def index_path() -> Path:
    return STATIC_DIR / "index.html"


def serve_index() -> Response:
    if not index_path().exists():
        return JSONResponse(
            status_code=503,
            content={
                "message": "Frontend assets are missing. Run `npm install && npm run build` in app/frontend before starting the app."
            },
        )
    return FileResponse(index_path())


@app.get("/", include_in_schema=False, response_model=None)
def root() -> Response:
    return serve_index()


@app.get("/{full_path:path}", include_in_schema=False, response_model=None)
def spa_fallback(full_path: str) -> Response:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found")

    candidate = STATIC_DIR / full_path
    if candidate.exists() and candidate.is_file():
        return FileResponse(candidate)
    return serve_index()
