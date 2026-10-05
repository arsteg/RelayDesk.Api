"""Test harness: points the app at the API test database, wipes it between
tests, seeds a business + owner + catalogue, and exposes an ASGI HTTP client.

Requires a reachable Postgres with the schema applied. By default it uses
API_TEST_DATABASE_URL (see apps/api/.env.example); the repo's local docker
Postgres on :5544 with database `relaydesk_api_test` is the intended target.
"""

from __future__ import annotations

import os

# Must be set before importing the app so settings/engine bind to the test DB.
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get(
        "API_TEST_DATABASE_URL",
        "postgresql://relaydesk:relaydesk@localhost:5544/relaydesk_api_test?schema=public",
    ),
)
os.environ.setdefault("APP_URL", "http://localhost:3000")
# Each test runs on its own event loop; avoid a cross-loop asyncpg pool.
os.environ["DB_DISABLE_POOL"] = "1"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import get_sessionmaker, now_utc  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Business, Category, Membership, Product, Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402

OWNER_PASSWORD = "correct horse battery"


@pytest_asyncio.fixture
async def db():
    sessionmaker = get_sessionmaker()
    # Wipe every table (except Prisma's migration ledger) before each test.
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> '_prisma_migrations'"
                )
            )
        ).all()
        tables = ", ".join(f'"{r[0]}"' for r in rows)
        if tables:
            await session.execute(text(f"TRUNCATE {tables} CASCADE"))
        await session.commit()
    async with sessionmaker() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def seed(db):
    """A business with an owner (OWNER_PASSWORD), one category and two products."""
    owner = User(
        id=None,
        email="owner@example.test",
        name="Owner",
        passwordHash=hash_password(OWNER_PASSWORD),
        emailVerifiedAt=now_utc(),
        isPlatformAdmin=False,
    )
    # id defaults are applied by the model mapper; set explicitly via flush.
    from app.ids import cuid

    owner.id = cuid()
    db.add(owner)
    await db.flush()  # the models carry no ORM relationships, so order inserts by FK dependency
    business = Business(
        id=cuid(),
        name="Sweet Crumbs",
        currency="INR",
        timezone="Asia/Kolkata",
        defaultTaxBps=500,  # 5% so tax maths is exercised
        invoicePrefix="ORD-",
        orderSequence=0,
    )
    db.add(business)
    await db.flush()
    db.add(Membership(id=cuid(), userId=owner.id, businessId=business.id, role=Role.OWNER, updatedAt=now_utc()))
    category = Category(id=cuid(), businessId=business.id, name="Cakes")
    db.add(category)
    await db.flush()
    chocolate = Product(
        id=cuid(),
        businessId=business.id,
        name="Chocolate Cake",
        categoryId=category.id,
        description="Rich chocolate",
        unit="piece",
        priceMinor=50000,  # 500.00
        isAvailable=True,
    )
    croissant = Product(
        id=cuid(),
        businessId=business.id,
        name="Croissant",
        categoryId=category.id,
        unit="piece",
        priceMinor=8000,  # 80.00
        isAvailable=True,
    )
    db.add(chocolate)
    db.add(croissant)
    await db.commit()
    return {
        "owner": owner,
        "business": business,
        "category": category,
        "products": {"chocolate": chocolate, "croissant": croissant},
    }


@pytest_asyncio.fixture
async def admin_token(client, seed):
    resp = await client.post(
        "/admin/auth/login", json={"email": "owner@example.test", "password": OWNER_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def platform_admin(db, seed):
    """A platform operator (isPlatformAdmin) plus a subscription + pricing plans
    for the seeded business, so operator endpoints have data to act on."""
    from datetime import timedelta

    from app.ids import cuid
    from app.models import Plan, PricingPlan, Subscription, SubscriptionStatus

    admin = User(
        id=cuid(),
        email="ops@relaydesk.test",
        name="Ops",
        passwordHash=hash_password(OWNER_PASSWORD),
        emailVerifiedAt=now_utc(),
        isPlatformAdmin=True,
    )
    db.add(admin)
    db.add(
        Subscription(
            id=cuid(),
            businessId=seed["business"].id,
            plan=Plan.STARTER,
            status=SubscriptionStatus.TRIALING,
            trialEndsAt=now_utc() + timedelta(days=14),
        )
    )
    for code, name, label, members, orders, order_ix in [
        ("STARTER", "Starter", "₹999 / month", 3, 200, 0),
        ("PRO", "Pro", "₹2,499 / month", 25, None, 1),
    ]:
        db.add(
            PricingPlan(
                code=code, name=name, description=f"{name} plan", priceLabel=label,
                maxMembers=members, maxMonthlyOrders=orders, sortOrder=order_ix,
            )
        )
    await db.commit()
    return {"admin": admin}


@pytest_asyncio.fixture
async def platform_token(client, platform_admin):
    resp = await client.post(
        "/platform/auth/login", json={"email": "ops@relaydesk.test", "password": OWNER_PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


@pytest_asyncio.fixture
async def customer_session(client, admin_token):
    """Invite + onboard a customer; returns their bearer token and business id."""
    invite = await client.post(
        "/admin/customers/invite",
        headers=auth(admin_token),
        json={"email": "alice@example.test", "name": "Alice"},
    )
    assert invite.status_code == 200, invite.text
    token = invite.json()["token"]
    accepted = await client.post(
        f"/invitations/{token}/accept",
        json={"name": "Alice", "password": "alice-password-123"},
    )
    assert accepted.status_code == 200, accepted.text
    data = accepted.json()
    return {"token": data["token"], "businessId": data["customer"]["businessId"], "customer": data["customer"]}
