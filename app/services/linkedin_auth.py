import secrets
import uuid
import httpx
from datetime import datetime, timezone
from app.config import settings
from app.services.supabase_client import get_supabase_client

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


def get_linkedin_authorization_url(state: str) -> str:
    """
    Construct the LinkedIn OpenID Connect authorization URL.
    Uses response_type=code, client_id, redirect_uri, state, and scopes (openid profile email).
    """
    params = {
        "response_type": "code",
        "client_id": settings.linkedin_client_id,
        "redirect_uri": settings.linkedin_redirect_uri,
        "state": state,
        "scope": "openid profile email"
    }
    req = httpx.Request("GET", LINKEDIN_AUTH_URL, params=params)
    return str(req.url)


def exchange_code_for_token(code: str) -> str:
    """
    Exchange authorization code for a LinkedIn access token.
    Secrets are passed securely via HTTPS POST and never exposed or logged.
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.linkedin_client_id,
        "client_secret": settings.linkedin_client_secret,
        "redirect_uri": settings.linkedin_redirect_uri,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    with httpx.Client(timeout=10.0) as client:
        response = client.post(LINKEDIN_TOKEN_URL, data=data, headers=headers)

    if response.status_code != 200:
        raise ValueError(
            f"Failed to exchange authorization code with LinkedIn: status {response.status_code}, response: {response.text}"
        )

    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise ValueError("LinkedIn token response did not contain access_token")

    return access_token


def get_linkedin_user_info(access_token: str) -> dict:
    """
    Retrieve authenticated user's OIDC profile from LinkedIn userinfo endpoint.
    Extracts all safe OIDC profile fields without exposing tokens or secrets.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    with httpx.Client(timeout=10.0) as client:
        response = client.get(LINKEDIN_USERINFO_URL, headers=headers)

    if response.status_code != 200:
        raise ValueError(
            f"Failed to retrieve user info from LinkedIn: status {response.status_code}"
        )

    user_data = response.json()

    sub = user_data.get("sub", "")
    email = user_data.get("email", "")
    given_name = user_data.get("given_name", "")
    family_name = user_data.get("family_name", "")
    name = user_data.get("name")
    if not name:
        name = f"{given_name} {family_name}".strip() if (given_name or family_name) else ""

    # Derive first and last name if given_name/family_name were missing but name is present
    if not given_name and name:
        parts = name.split(" ", 1)
        given_name = parts[0]
        if not family_name and len(parts) > 1:
            family_name = parts[1]

    profile_image = user_data.get("picture")
    email_verified = user_data.get("email_verified")

    # Format locale if present
    locale_data = user_data.get("locale")
    if isinstance(locale_data, dict):
        lang = locale_data.get("language", "")
        country = locale_data.get("country", "")
        locale_str = f"{lang}_{country}" if (lang and country) else (lang or country or "")
    elif isinstance(locale_data, str):
        locale_str = locale_data
    else:
        locale_str = ""

    return {
        "sub": sub,
        "email": email,
        "name": name,
        "given_name": given_name,
        "family_name": family_name,
        "profile_image": profile_image,
        "email_verified": email_verified,
        "locale": locale_str
    }


def sync_user_to_supabase(user_info: dict) -> dict:
    """
    Create or update user in existing Supabase 'users' table.
    Fields used: id, email, name, profile_image, created_at, updated_at.
    Enriches return dict with normalized frontend keys (full_name, first_name, profile_picture, etc.).
    """
    supabase = get_supabase_client()
    email = user_info.get("email")
    name = user_info.get("name")
    profile_image = user_info.get("profile_image")
    sub = user_info.get("sub")

    now = datetime.now(timezone.utc).isoformat()

    existing_user = None
    if email:
        res = supabase.table("users").select("*").eq("email", email).execute()
        if res.data:
            existing_user = res.data[0]

    if existing_user:
        user_id = existing_user["id"]
        update_payload = {
            "name": name or existing_user.get("name"),
            "profile_image": profile_image or existing_user.get("profile_image"),
            "updated_at": now
        }
        res = supabase.table("users").update(update_payload).eq("id", user_id).execute()
        user_record = res.data[0] if res.data else existing_user
    else:
        if sub:
            user_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"linkedin:{sub}"))
        else:
            user_id = str(uuid.uuid4())

        insert_payload = {
            "id": user_id,
            "email": email,
            "name": name,
            "profile_image": profile_image,
            "created_at": now,
            "updated_at": now
        }
        res = supabase.table("users").insert(insert_payload).execute()
        user_record = res.data[0] if res.data else insert_payload

    # Attach derived fields so app.js can populate UI without missing field errors
    full_name = user_record.get("name") or user_info.get("name") or "User"
    given_name = user_info.get("given_name") or (full_name.split()[0] if full_name else "")
    family_name = user_info.get("family_name") or (full_name.split()[-1] if len(full_name.split()) > 1 else "")

    user_record["full_name"] = full_name
    user_record["first_name"] = given_name
    user_record["last_name"] = family_name
    user_record["profile_picture"] = user_record.get("profile_image") or profile_image
    user_record["linkedin_member_id"] = sub or user_record.get("id")
    user_record["locale"] = user_info.get("locale", "")
    user_record["email_verified"] = user_info.get("email_verified", True)

    return user_record