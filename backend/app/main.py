"""
Ultex Ads Audit API.

The audit and analysis routes are read-only against Meta. The Ad manager has an
explicit plan/apply write path. The Meta token stays in this process; the
browser talks to these routes and never sees it.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routers import auth as auth_router
from .routers import adops as adops_router
from .routers import creative as creative_router
from .routers import data as data_router
from .routers import strategy as strategy_router

settings = get_settings()

app = FastAPI(
    title="Ultex Ads Audit",
    version="0.2.0",
    description="Meta Marketing API audit, analysis and reporting.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,  # required for the session cookie
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(data_router.router)
app.include_router(strategy_router.router)
app.include_router(creative_router.router)
app.include_router(adops_router.router)


@app.get("/api/health")
def health():
    """Unauthenticated: reports config readiness without leaking any secret."""
    return {
        "ok": True,
        "metaTokenConfigured": bool(settings.meta_access_token),
        "loginConfigured": bool(
            settings.dashboard_user and settings.dashboard_password and settings.session_secret
        ),
        "graphApiVersion": settings.graph_api_version,
        "strategistConfigured": bool(settings.openai_api_key),
        "strategistModel": settings.openai_model,
        "imageModel": settings.openai_image_model,
        "videoModel": settings.gemini_video_model if settings.gemini_api_key else None,
    }
