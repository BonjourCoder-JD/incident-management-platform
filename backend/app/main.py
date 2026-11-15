from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import os

from .incidents import router as incidents_router

app = FastAPI(title="Incident Management API", version="0.2.0")
app.include_router(incidents_router)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://incident:incident@db:5432/incidents")

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/health/db")
def database_health():
    try:
        with create_engine(DATABASE_URL, pool_pre_ping=True).connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Database unavailable")
