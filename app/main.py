from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from . import models  # noqa: F401  (register tables)
from .config import settings
from .db import Base, SessionLocal, engine
from .routers import core, evidence, frameworks, grc, insights
from .security import rate_limited
from .seed import seed_frameworks

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Aegis GRC API", version="1.0.0", description="Multi-tenant, AI-assisted GRC platform",
              docs_url="/docs" if settings.env != "production" else None, redoc_url=None)

if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type", "X-Org-Id"])

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def guard(request: Request, call_next):
    ip = request.client.host if request.client else "?"
    if request.url.path.startswith("/api/") and rate_limited(f"api:{ip}", settings.rate_limit_per_minute):
        return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
    resp = await call_next(request)
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                         "Permissions-Policy": "geolocation=(), microphone=(), camera=()", "Content-Security-Policy": CSP})
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


for r in (core.router, frameworks.router, evidence.router, grc.router, insights.router):
    app.include_router(r)


@app.on_event("startup")
def startup() -> None:
    if settings.auto_create_schema:
        Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_frameworks(db)


@app.get("/api/health", tags=["system"])
def health():
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
        db = "ok"
    except Exception:
        db = "unavailable"
    return {"status": "ok" if db == "ok" else "degraded", "database": db}


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})
