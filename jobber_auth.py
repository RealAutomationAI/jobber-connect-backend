# jobber_auth.py

import os
import json
import base64
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

router = APIRouter()

# ==== ENV VARS ===============================================================

JOBBER_CLIENT_ID = os.environ["JOBBER_CLIENT_ID"]
JOBBER_CLIENT_SECRET = os.environ["JOBBER_CLIENT_SECRET"]
JOBBER_REDIRECT_URI = os.environ["JOBBER_REDIRECT_URI"]  # e.g. https://william-auth-production.up.railway.app/jobber/callback
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
) -> bool:
    """
    Send tokens + phone to n8n and interpret the response.

    Returns:
        True  -> n8n says everything is OK ("success" in body, 2xx status)
        False -> any other response (e.g. "Client number not found.")
    """
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
        # If there is no webhook configured, don't block the flow.
        return True

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                N8N_WEBHOOK_URL,
                json=payload,
                timeout=15,
            )
    except Exception as e:
        print("SENT_JOBBER_TOKENS_TO_N8N ERROR", repr(e))
        # Network / n8n error -> treat as failure
        return False

    body_text = (resp.text or "").strip()
    print("SENT_JOBBER_TOKENS_TO_N8N", resp.status_code, body_text[:500])

    # Success heuristic: 2xx status AND body contains "success" (case-insensitive)
    if 200 <= resp.status_code < 300 and "success" in body_text.lower():
        return True

    # Anything else (including "Client number not found.") is a failure
    return False


def encode_state(payload: dict) -> str:
    """
    Just base64-encodes a small JSON blob.
    No signature / verification. We're only using this
    as a container to carry phone_number + client_id through OAuth.
    """
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_state(token: str) -> dict:
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        # Treat as "no state info" instead of hard failing
        return {}


# ==== ROUTES =================================================================

@router.get("/favicon.ico")
async def favicon():
    # Avoid noisy 404s in logs
    return Response(status_code=204)


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
        # You *could* talk to n8n here too, but right now the real
        # "not found" logic lives after we send tokens.
        raise HTTPException(status_code=404, detail="Client not found for this phone number")

    # Pack phone + client into a simple state blob
    state_payload = {
        "client_id": william_client_id,
        "phone_number": phone_number,
        "ts": int(datetime.utcnow().timestamp()),
    }
    state = encode_state(state_payload)

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


# IMPORTANT:
# Jobber sometimes redirects to /callback (no /jobber prefix).
# We register BOTH so either route works.
@router.get("/jobber/callback")
@router.get("/callback")
async def jobber_callback(request: Request):
    """
    OAuth callback endpoint Jobber hits after user approves access.
    Exchanges code for tokens and forwards them to n8n when we
    have a phone_number in state (started from our connect page).

    If there is no phone_number in state, assume the flow was
    started from inside Jobber (dashboard / app directory) and
    redirect the user to a page that explains they must connect
    via our phone-number flow.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        raise HTTPException(status_code=400, detail="Missing code")

    # Safely decode; returns {} if state is missing/invalid
    state_payload = decode_state(state) if state else {}
    phone_number = state_payload.get("phone_number")
    william_client_id = state_payload.get("client_id")

    # If we don't have a phone_number, we can't map this Jobber
    # connection to a specific William client. Send them to a
    # page that tells them how to connect properly.
    if not phone_number or not william_client_id:
        return RedirectResponse(
            url="https://jobber-connect-frontend.vercel.app/phone-required.html"
        )

    # Normal flow: started from our Vercel connect page
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

    ok = await store_jobber_tokens_for_client(
        client_id=william_client_id,
        phone_number=phone_number,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )

    # If n8n says "Client number not found" (or anything non-success),
    # send them to an error page instead of the normal success page.
    if not ok:
        return RedirectResponse(
            url="https://jobber-connect-frontend.vercel.app/phone-not-found.html"
        )

    # Redirect user to your normal success page
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
            },
        )

    return {"status_code": resp.status_code, "body": resp.json()}
