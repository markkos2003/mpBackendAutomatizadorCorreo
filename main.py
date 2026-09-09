from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"status": "Backend FastAPI listo para SUNAT"}