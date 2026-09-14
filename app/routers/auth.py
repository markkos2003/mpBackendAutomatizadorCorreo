from typing import Optional
from urllib.parse import unquote, urlencode
import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from app.config import settings
from app.database import supabase
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/api/auth", tags=["Auth"])

# --- GMAIL ---
@router.get("/gmail/login")
def gmail_login(user_id: Optional[str] = None):
    if not user_id:
        raise HTTPException(status_code=400, detail="Debe proporcionar user_id")

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "state": user_id,
    }
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}")


@router.get("/gmail/callback")
def gmail_callback(request: Request):
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    state = request.query_params.get("state")

    if error:
        return RedirectResponse(url=f"{settings.FRONTEND_URL}/login?error=cancelled")

    if not code:
        raise HTTPException(status_code=400, detail="No se recibió el código de autorización")

    payload = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
    }

    try:
        response = requests.post("https://oauth2.googleapis.com/token", data=payload)
        
        if response.status_code != 200:
            return {"error_google": response.json()}

        data = response.json()
        refresh_token = data.get("refresh_token")
        clean_user_id = unquote(state) if state else None

        if refresh_token and clean_user_id:
            url = f"{settings.SUPABASE_URL}/rest/v1/user_tokens?on_conflict=user_id,provider"
            headers = {
                                    "apikey": settings.SUPABASE_KEY,
                                    "Authorization": f"Bearer {settings.SUPABASE_KEY}",
                                    "Content-Type": "application/json",
                                    "Prefer": "resolution=merge-duplicates"  # Hace un UPSERT nativo en PostgreSQL
                                }
            data = {
                                    "user_id": clean_user_id,
                                    "provider": "gmail",
                                    "refresh_token": refresh_token
                                }
                                
            res = requests.post(url, json=data, headers=headers)
            if res.status_code not in [200, 201,204]:
                    return {"error_supabase_directo": res.text, "status": res.status_code}                    

          

        redirect_url = f"{settings.FRONTEND_URL}/dashboard?user_id={clean_user_id}&provider=gmail"
        return RedirectResponse(url=redirect_url)

    except Exception as e:
        return {"error_backend": str(e), "tipo": type(e).__name__}


# --- OUTLOOK ---
OUTLOOK_SCOPES = ["offline_access", "User.Read", "Mail.Read"]

@router.get("/outlook/login")
def outlook_login(user_id: Optional[str] = None):
    if not user_id:
        raise HTTPException(status_code=400, detail="Debe proporcionar user_id")

    scopes_str = "%20".join(OUTLOOK_SCOPES)
    auth_url = (
        f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?"
        f"client_id={settings.OUTLOOK_CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={settings.OUTLOOK_REDIRECT_URI}"
        f"&scope={scopes_str}"
        f"&response_mode=query"
        f"&prompt=consent"
        f"&state={user_id}"
    )
    return RedirectResponse(auth_url)


@router.get("/outlook/callback")
def outlook_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    payload = {
        "client_id": settings.OUTLOOK_CLIENT_ID,
        "client_secret": settings.OUTLOOK_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.OUTLOOK_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    try:
        response = requests.post(token_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
        
        if response.status_code != 200:
            return {"error_microsoft": response.json()}

        data = response.json()
        refresh_token = data.get("refresh_token")
        clean_user_id = unquote(state) if state else None

        if refresh_token and clean_user_id:
                    url = f"{settings.SUPABASE_URL}/rest/v1/user_tokens?on_conflict=user_id,provider"
                    headers = {
                        "apikey": settings.SUPABASE_KEY,
                        "Authorization": f"Bearer {settings.SUPABASE_KEY}",
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates"  # Hace un UPSERT nativo en PostgreSQL
                    }
                    data = {
                        "user_id": clean_user_id,
                        "provider": "outlook",
                        "refresh_token": refresh_token
                    }
                    
                    res = requests.post(url, json=data, headers=headers)
                    if res.status_code not in [200, 201]:
                        return {"error_supabase_directo": res.text, "status": res.status_code}

        

        redirect_url = f"{settings.FRONTEND_URL}/dashboard?user_id={clean_user_id}&provider=outlook"
        return RedirectResponse(url=redirect_url)     
    
    

    except Exception as e:
        return {"error_backend": str(e), "tipo": type(e).__name__}