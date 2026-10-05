"""Customer invitation + onboarding flow."""

from conftest import auth


async def test_invite_inspect_accept_login(client, admin_token):
    invite = await client.post(
        "/admin/customers/invite", headers=auth(admin_token), json={"email": "bob@example.test", "name": "Bob"}
    )
    assert invite.status_code == 200, invite.text
    body = invite.json()
    assert body["inviteUrl"].endswith(f"/order/accept/{body['token']}")
    token = body["token"]

    # Public inspect reveals the business + email without authentication.
    info = await client.get(f"/invitations/{token}")
    assert info.status_code == 200
    assert info.json()["email"] == "bob@example.test"
    assert info.json()["businessName"] == "Sweet Crumbs"

    accepted = await client.post(
        f"/invitations/{token}/accept", json={"name": "Bob", "password": "bob-password-123"}
    )
    assert accepted.status_code == 200, accepted.text
    data = accepted.json()
    assert data["token"]
    business_id = data["customer"]["businessId"]

    # The onboarding session works immediately.
    me = await client.get("/customer/me", headers=auth(data["token"]))
    assert me.status_code == 200
    assert me.json()["customer"]["email"] == "bob@example.test"

    # And the customer can log in again with the chosen password.
    login = await client.post(
        "/customer/auth/login",
        json={"email": "bob@example.test", "password": "bob-password-123", "businessId": business_id},
    )
    assert login.status_code == 200, login.text


async def test_invitation_is_single_use(client, admin_token):
    invite = await client.post(
        "/admin/customers/invite", headers=auth(admin_token), json={"email": "carol@example.test"}
    )
    token = invite.json()["token"]
    first = await client.post(f"/invitations/{token}/accept", json={"name": "Carol", "password": "carol-password-1"})
    assert first.status_code == 200
    second = await client.post(f"/invitations/{token}/accept", json={"name": "Carol", "password": "carol-password-1"})
    assert second.status_code in (409, 422)


async def test_cannot_invite_existing_account(client, admin_token):
    invite = await client.post(
        "/admin/customers/invite", headers=auth(admin_token), json={"email": "dave@example.test"}
    )
    token = invite.json()["token"]
    await client.post(f"/invitations/{token}/accept", json={"name": "Dave", "password": "dave-password-12"})
    again = await client.post(
        "/admin/customers/invite", headers=auth(admin_token), json={"email": "dave@example.test"}
    )
    assert again.status_code == 422
    assert "email" in (again.json()["error"].get("fields") or {})


async def test_revoke_invitation(client, admin_token):
    invite = await client.post(
        "/admin/customers/invite", headers=auth(admin_token), json={"email": "erin@example.test"}
    )
    listing = await client.get("/admin/customers", headers=auth(admin_token))
    inv_id = listing.json()["pending"][0]["id"]
    revoke = await client.post(f"/admin/customers/invitations/{inv_id}/revoke", headers=auth(admin_token))
    assert revoke.status_code == 200
    # The token is now unusable.
    token = invite.json()["token"]
    accept = await client.post(f"/invitations/{token}/accept", json={"name": "Erin", "password": "erin-password-12"})
    assert accept.status_code == 422


async def test_invite_requires_admin(client, customer_session):
    # A customer bearer token cannot reach admin endpoints.
    resp = await client.post(
        "/admin/customers/invite", headers=auth(customer_session["token"]), json={"email": "x@example.test"}
    )
    assert resp.status_code in (401, 403)
