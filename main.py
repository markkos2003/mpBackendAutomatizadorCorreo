from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.routers import auth, comprobantes

app = FastAPI(title="SUNAT Engine API", version="1.0")

# Configurar permisos para Angular
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:4200","https://mp-fronted-automatizador-correo.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Conectar routers
app.include_router(auth.router)
app.include_router(comprobantes.router)

@app.get("/")
def home():
    return {"message": "SUNAT Engine API activa"}