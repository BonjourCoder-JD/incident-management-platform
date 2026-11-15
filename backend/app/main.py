from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import os

from .analytics import router as analytics_router
from .auth import router as auth_router
from .exports import router as exports_router
from .incidents import router as incidents_router
from .users import router as users_router

app = FastAPI(title="Incident Management API", version="0.3.0")
app.include_router(auth_router)
app.include_router(incidents_router)
app.include_router(users_router)
app.include_router(analytics_router)
app.include_router(exports_router)
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://incident:incident@db:5432/incidents")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # FastAPI echoes submitted values ("input") in 422 responses by default. Strip them so
    # passwords and other credentials are never reflected back or captured downstream.
    errors = [{"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


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
