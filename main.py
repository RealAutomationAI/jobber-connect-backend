from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from jobber_auth import router as jobber_router
from jobber_disconnect import router as jobber_disconnect_router

app = FastAPI()

ALLOWED_ORIGINS = [
    # exact origin from your Vercel site, no trailing slash
    "https://jobber-connect-frontend.vercel.app",  # change if your URL is different
    "http://localhost:3000",  # optional local
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(jobber_router)
app.include_router(jobber_disconnect_router)