# abckasefi-catalog/app/main.py
"""HTTP API + web UI. Run: uvicorn app.main:app --host 0.0.0.0 --port 8090"""
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, importer, routes_catalog, routes_lists, routes_titles
from . import tmdb_client as client
from .common import VERSION, auth

log = logging.getLogger("catalog")
STATIC = os.path.join(os.path.dirname(__file__), "static")


def _importer_loop():
    """Build the catalog at start (if missing) and then every IMPORT_EVERY_HOURS."""
    while True:
        try:
            if not db.ready() or _stale():
                importer.build()
        except Exception:  # noqa: BLE001 - keep the service alive, retry later
            log.exception("import failed")
        time.sleep(3600)


def _stale():
    built = db.stats()["meta"].get("built")
    if not built:
        return True
    age_h = (time.time() - os.path.getmtime(db.CATALOG)) / 3600
    return age_h >= config.IMPORT_EVERY_HOURS


@asynccontextmanager
async def lifespan(_app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # httpx logs full URLs at INFO, and the TMDB v3 key travels in the query string -> never log it
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db.init()
    threading.Thread(target=_importer_loop, daemon=True, name="importer").start()
    yield


app = FastAPI(title="abckasefi-catalog", version=VERSION, lifespan=lifespan)
app.include_router(routes_catalog.router)
app.include_router(routes_lists.router)
app.include_router(routes_titles.router)


@app.get("/health")
def health():
    return {"ok": True, "ready": db.ready(), "tmdb": client.enabled(), **db.stats()}


@app.get("/v1/ping", dependencies=[Depends(auth)])
def ping():
    """Cheap authenticated check for clients: reaching it with a 200 proves both the URL and the token."""
    return {"ok": True, "version": VERSION, "tmdb": client.enabled(), "ready": db.ready()}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
