"""Auth guards for admin and customer surfaces."""

from conftest import auth


async def test_admin_login_wrong_password(client, seed):
    resp = await client.post("/admin/auth/login", json={"email": "owner@example.test", "password": "nope"})
    assert resp.status_code == 401


async def test_admin_me_requires_token(client):
    assert (await client.get("/admin/me")).status_code == 401


async def test_admin_me_ok(client, admin_token):
    resp = await client.get("/admin/me", headers=auth(admin_token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "OWNER"
    assert resp.json()["business"]["name"] == "Sweet Crumbs"


async def test_customer_endpoints_require_auth(client):
    assert (await client.get("/customer/menu")).status_code == 401
    assert (await client.get("/customer/orders")).status_code == 401


async def test_customer_menu_lists_available_products(client, seed, customer_session):
    resp = await client.get("/customer/menu", headers=auth(customer_session["token"]))
    assert resp.status_code == 200
    names = {item["name"] for item in resp.json()["items"]}
    assert {"Chocolate Cake", "Croissant"} <= names


async def test_health(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
