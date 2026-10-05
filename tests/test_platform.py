"""Platform-operator (SaaS admin) endpoint tests."""

from __future__ import annotations

import pytest

from .conftest import OWNER_PASSWORD, auth

pytestmark = pytest.mark.asyncio


async def test_login_requires_platform_admin(client, seed, platform_admin):
    # A business owner (not a platform admin) is refused.
    denied = await client.post(
        "/platform/auth/login", json={"email": "owner@example.test", "password": OWNER_PASSWORD}
    )
    assert denied.status_code == 403, denied.text

    ok = await client.post(
        "/platform/auth/login", json={"email": "ops@relaydesk.test", "password": OWNER_PASSWORD}
    )
    assert ok.status_code == 200, ok.text
    token = ok.json()["token"]
    me = await client.get("/platform/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["email"] == "ops@relaydesk.test"


async def test_me_requires_auth(client):
    assert (await client.get("/platform/me")).status_code == 401


async def test_stats_and_business_list(client, platform_token):
    stats = await client.get("/platform/stats", headers=auth(platform_token))
    assert stats.status_code == 200, stats.text
    body = stats.json()
    assert body["businesses"] >= 1 and body["users"] >= 1
    assert body["byStatus"].get("TRIALING", 0) >= 1

    listing = await client.get("/platform/businesses", headers=auth(platform_token))
    assert listing.status_code == 200
    rows = listing.json()["rows"]
    assert any(r["name"] == "Sweet Crumbs" for r in rows)
    row = next(r for r in rows if r["name"] == "Sweet Crumbs")
    assert row["owner"]["email"] == "owner@example.test"
    assert row["subscription"]["status"] == "TRIALING"
    assert row["access"]["level"] == "full"  # trialing, not yet expired

    # search by owner email
    found = await client.get("/platform/businesses", headers=auth(platform_token), params={"q": "owner@example"})
    assert any(r["name"] == "Sweet Crumbs" for r in found.json()["rows"])


async def _business_id(client, token):
    rows = (await client.get("/platform/businesses", headers=auth(token))).json()["rows"]
    return rows[0]["id"]


async def test_suspend_and_reactivate(client, platform_token):
    bid = await _business_id(client, platform_token)
    bad = await client.post(f"/platform/businesses/{bid}/suspend", headers=auth(platform_token), json={"suspend": True, "reason": "x"})
    assert bad.status_code == 422  # reason too short

    sus = await client.post(
        f"/platform/businesses/{bid}/suspend", headers=auth(platform_token), json={"suspend": True, "reason": "payment fraud"}
    )
    assert sus.status_code == 200, sus.text
    assert sus.json()["suspendedAt"] is not None
    assert sus.json()["access"]["level"] == "suspended"

    re = await client.post(f"/platform/businesses/{bid}/suspend", headers=auth(platform_token), json={"suspend": False})
    assert re.status_code == 200
    assert re.json()["suspendedAt"] is None


async def test_subscription_controls(client, platform_token):
    bid = await _business_id(client, platform_token)
    up = await client.post(
        f"/platform/businesses/{bid}/subscription", headers=auth(platform_token), json={"action": "set_plan", "plan": "PRO"}
    )
    assert up.status_code == 200, up.text
    assert up.json()["subscription"]["plan"] == "PRO"

    cancel = await client.post(
        f"/platform/businesses/{bid}/subscription", headers=auth(platform_token), json={"action": "cancel"}
    )
    assert cancel.json()["subscription"]["status"] == "CANCELED"

    bad = await client.post(
        f"/platform/businesses/{bid}/subscription", headers=auth(platform_token), json={"action": "set_plan"}
    )
    assert bad.status_code == 422  # plan required


async def test_plan_editing(client, platform_token):
    before = await client.get("/platform/plans", headers=auth(platform_token))
    assert {p["code"] for p in before.json()} >= {"STARTER", "PRO"}

    patched = await client.patch(
        "/platform/plans/STARTER", headers=auth(platform_token), json={"priceLabel": "₹1,099 / month", "maxMembers": 5}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["priceLabel"] == "₹1,099 / month"
    assert patched.json()["maxMembers"] == 5

    after = await client.get("/platform/plans", headers=auth(platform_token))
    starter = next(p for p in after.json() if p["code"] == "STARTER")
    assert starter["priceLabel"] == "₹1,099 / month"


async def test_admin_grant_and_revoke(client, platform_token):
    grant = await client.post(
        "/platform/admins", headers=auth(platform_token), json={"email": "owner@example.test", "grant": True}
    )
    assert grant.status_code == 200, grant.text
    assert grant.json()["isPlatformAdmin"] is True

    admins = await client.get("/platform/admins", headers=auth(platform_token))
    assert any(a["email"] == "owner@example.test" for a in admins.json())

    revoke = await client.post(
        "/platform/admins", headers=auth(platform_token), json={"email": "owner@example.test", "grant": False}
    )
    assert revoke.json()["isPlatformAdmin"] is False


async def test_audit_records_actions(client, platform_token):
    bid = await _business_id(client, platform_token)
    await client.post(
        f"/platform/businesses/{bid}/suspend", headers=auth(platform_token), json={"suspend": True, "reason": "testing audit"}
    )
    audit = await client.get("/platform/audit", headers=auth(platform_token))
    assert audit.status_code == 200
    actions = {row["action"] for row in audit.json()}
    assert "business.suspended" in actions
