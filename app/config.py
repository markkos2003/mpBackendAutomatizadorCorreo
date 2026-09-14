import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    # Supabase
    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")
    
    # Frontend
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:4200")
    
    # Google OAuth
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI: str = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/gmail/callback")

    # Microsoft / Outlook OAuth
    OUTLOOK_CLIENT_ID: str = os.getenv("OUTLOOK_CLIENT_ID", "")
    OUTLOOK_CLIENT_SECRET: str = os.getenv("OUTLOOK_CLIENT_SECRET", "")
    OUTLOOK_REDIRECT_URI: str = os.getenv("OUTLOOK_REDIRECT_URI", "http://localhost:8000/api/auth/outlook/callback")
    OUTLOOK_SCOPES: str = os.getenv("OUTLOOK_SCOPES", "offline_access User.Read Mail.Read")

    OCRAPI: str=os.getenv("OCRKEY","helloworld")
    APIGEM: str=os.getenv("KEYPROYE")

settings = Settings()