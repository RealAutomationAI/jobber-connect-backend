from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import httpx
import os

router = APIRouter()

# Set this in Railway
N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL")  # e.g. https://n8n.yourdomain.com/webhook/jobber-disconnect


class DisconnectPayload(BaseModel):
    phoneNumber: str
    trigger: str | None = None  # frontend sends "jobber_disconnect"


@router.post("/jobber/disconnect/start")
async def jobber_disconnect_start(payload: DisconnectPayload):
    if not payload.phoneNumber or not payload.phoneNumber.strip():
        raise HTTPException(status_code=400, detail="Phone number is required.")

    if not N8N_WEBHOOK_URL:
        raise HTTPException(status_code=500, detail="N8N_WEBHOOK_URL not configured.")

    phone = payload.phoneNumber.strip()

    body = {
        "trigger": payload.trigger or "jobber_disconnect",
        "phone": phone,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            res = await client.post(N8N_WEBHOOK_URL, json=body)
        except Exception:
            raise HTTPException(status_code=502, detail="Failed to reach n8n webhook.")

    # You can ignore n8n response content; just surface status
    if res.status_code >= 300:
        raise HTTPException(
            status_code=502,
            detail=f"n8n webhook returned status {res.status_code}",
        )

    return {
        "success": True,
        "message": "If this number was connected, it is now being disconnected in our system.",
    }