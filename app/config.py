"""Runtime configuration, loaded from environment variables.

The API shares the same PostgreSQL database as the Next.js app (one schema,
managed by Prisma). It never runs DDL.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit, parse_qsl

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Same connection string the Next.js app uses (DATABASE_URL). asyncpg does
    # not understand libpq query params such as sslmode/schema; build_async_url
    # strips them and maps them onto connect args.
    database_url: str = "postgresql://relaydesk:relaydesk@localhost:5544/relaydesk?schema=public"

    # Public base URL of the web app, used to build invitation links in emails.
    app_url: str = "http://localhost:3000"

    # Comma-separated origins allowed for cross-origin (mobile) requests.
    # Web talks to the API same-origin through a Next rewrite, so it needs no CORS.
    cors_origins: str = "*"

    # Session/token lifetimes.
    admin_session_ttl_days: int = 30
    customer_session_ttl_days: int = 30
    invite_ttl_days: int = 14

    environment: str = "development"  # "production" tightens cookie handling

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def admin_cookie_name(self) -> str:
        # Must match src/lib/auth/cookie.ts in the web app.
        return "__Host-bl_session" if self.is_production else "bl_session"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def async_database_url(self) -> tuple[str, dict]:
        """Return a SQLAlchemy asyncpg URL and connect_args for the driver."""
        return build_async_url(self.database_url)


def build_async_url(url: str) -> tuple[str, dict]:
    parts = urlsplit(url)
    scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    connect_args: dict = {}
    # libpq params asyncpg cannot consume.
    sslmode = query.pop("sslmode", None)
    query.pop("schema", None)
    query.pop("uselibpqcompat", None)
    query.pop("pgbouncer", None)
    if sslmode in {"require", "verify-ca", "verify-full", "prefer", "allow"}:
        connect_args["ssl"] = True
    # Supabase's transaction pooler disables prepared statement caching cleanly
    # when statement_cache_size is 0.
    if "pooler.supabase.com" in (parts.hostname or ""):
        connect_args["statement_cache_size"] = 0
    new_query = "&".join(f"{k}={v}" for k, v in query.items())
    async_url = urlunsplit((scheme, parts.netloc, parts.path, new_query, parts.fragment))
    return async_url, connect_args


@lru_cache
def get_settings() -> Settings:
    return Settings()
