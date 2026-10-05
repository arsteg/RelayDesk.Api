"""FastAPI application factory for the RelayDesk ordering API."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .errors import register_error_handlers
from .routers import admin, customer, platform, public


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RelayDesk Ordering API",
        version="0.1.0",
        description="Customer ordering, admin order visibility, and real-time order events.",
    )

    # Web talks to the API same-origin via a Next.js rewrite (no CORS needed);
    # mobile talks cross-origin, so allow the configured origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials="*" not in settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)
    app.include_router(public.router)
    app.include_router(admin.router)
    app.include_router(customer.router)
    app.include_router(platform.router)
    return app


app = create_app()
