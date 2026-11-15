from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import os

app = FastAPI(title="Incident Management API", version="0.1.0")
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
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="Database unavailable")
