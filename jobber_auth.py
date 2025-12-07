# jobber_auth.py

import os
import json
import base64
import hmac
import hashlib
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter()

# ==== ENV VARS ===============================================================

JOBBER_CLIENT_ID = os.environ["JOBBER_CLIENT_ID"]
JOBBER_CLIENT_SECRET = os.environ["JOBBER_CLIENT_SECRET"]
JOBBER_REDIRECT_URI = os.environ["JOBBER_REDIRECT_URI"]  # e.g. https://william-auth-production.up.railway.app/jobber/callback
STATE_SECRET = os.environ["JOBBER_STATE_SECRET"]
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL")      # e.g. https://n8n.yourdomain.com/webhook/jobber-tokens

JOBBER_AUTH_URL = "https://api.getjobber.com/api/oauth/authorize"
JOBBER_TOKEN_URL = "https://api.getjobber.com/api/oauth/token"
JOBBER_GRAPHQL_URL = "https://api.getjobber.com/api/graphql"
JOBBER_GRAPHQL_VERSION = "2023-08-18"  # update when you want a newer version

# ==== STUBS / HELPERS =======================================================

async def get_william_client_id_by_phone(phone_number: str) -> str | None:
    """
    TEMP: replace with real lookup in your William client DB.
    Given a phone number, return your internal client_id.
    """
    # For now always map to a test client.
    return "test_client_1"


async def store_jobber_tokens_for_client(
    client_id: str,
    phone_number: str,
    access_token: str,
    refresh_token: str | None,
    expires_in: int,
) -> None:
    payload = {
        "client_id": client_id,
        "phone_number": phone_number,
        "jobber_access_token": access_token,
        "jobber_refresh_token": refresh_token,
        "jobber_expires_in": expires_in,
        "jobber_expires_at": datetime.utcnow().timestamp() + expires_in,
    }

    if not N8N_WEBHOOK_URL:
        print("N8N_WEBHOOK_URL not set; payload:", payload)
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                N8N_WEBHOOK_URL,
                json=payload,
                timeout=15,
            )
    except Exception as e:
        print("SENT_JOBBER_TOKENS_TO_N8N ERROR", repr(e))
        return

    print("SENT_JOBBER_TOKENS_TO_N8N", resp.status_code, resp.text[:500])


def sign_state(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(STATE_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode("utf-8")


def verify_state(token: str) -> dict:
    try:
        decoded = base64.urlsafe_b64decode(token.encode("utf-8"))
        raw, sig = decoded.rsplit(b".", 1)
        expected = hmac.new(STATE_SECRET.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            raise ValueError("bad signature")
        return json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid state")


# ==== ROUTES =================================================================

@router.post("/jobber/start")
async def jobber_start(payload: dict):
    """
    Called by your Vercel page.
    Body: { "phone_number": "12185551234" }
    Returns: { "url": "<Jobber OAuth URL>" }
    """
    phone_number = payload.get("phone_number")
    if not phone_number:
        raise HTTPException(status_code=400, detail="phone_number required")

    william_client_id = await get_william_client_id_by_phone(phone_number)
    if not william_client_id:
        raise HTTPException(status_code=404, detail="Client not found for this phone number")

    state_payload = {
        "client_id": william_client_id,
        "phone_number": phone_number,
        "ts": int(datetime.utcnow().timestamp()),
    }
    state = sign_state(state_payload)

    scope = "clients:read clients:write"

    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": JOBBER_CLIENT_ID,
        "redirect_uri": JOBBER_REDIRECT_URI,
        "scope": scope,
        "state": state,
    }
    url = f"{JOBBER_AUTH_URL}?{urlencode(params)}"
    return {"url": url}


@router.get("/jobber/callback")
async def jobber_callback(request: Request):
    """
    OAuth callback endpoint Jobber hits after user approves access.
    Exchanges code for tokens and forwards them to n8n.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    state_payload = verify_state(state)
    william_client_id = state_payload["client_id"]
    phone_number = state_payload["phone_number"]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            JOBBER_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": JOBBER_CLIENT_ID,
                "client_secret": JOBBER_CLIENT_SECRET,
                "redirect_uri": JOBBER_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")

    data = resp.json()
    access_token = data["access_token"]
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 3600)

    await store_jobber_tokens_for_client(
        client_id=william_client_id,
        phone_number=phone_number,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )

    # Redirect user to your Vercel success page
    return RedirectResponse(
        url="https://jobber-connect-frontend.vercel.app/success.html"
    )


@router.get("/jobber/test")
async def jobber_test():
    """
    Manual debug endpoint: paste a valid access token for quick tests.
    Remove in production.
    """
    access_token = "PASTE_FULL_ACCESS_TOKEN_HERE"

    query = {
        "query": """
        query SampleQuery {
          clients(first: 5) {
            totalCount
            nodes {
              id
              firstName
              lastName
            }
          }
        }
        """
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            JOBBER_GRAPHQL_URL,
            json=query,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-JOBBER-GRAPHQL-VERSION": JOBBER_GRAPHQL_VERSION,
            },
            timeout=15,
        )

    return {"status_code": resp.status_code, "body": resp.json()}