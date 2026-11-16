from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .analytics import router as analytics_router
from .auth import router as auth_router
from .comments import router as comments_router
from .config import APP_ENV, production_problems
from .demo import router as demo_router
from .exports import router as exports_router
from .health import router as health_router
from .incidents import router as incidents_router
from .sla_api import router as sla_router
from .triage_api import router as triage_router
from .users import router as users_router

PRODUCTION = APP_ENV == "production"
if PRODUCTION and (problems := production_problems()):
    # Fail closed: never serve a public deployment with unsafe settings.
    raise RuntimeError("Unsafe production configuration: " + " ".join(problems))

# Interactive API docs are for development only.
docs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if PRODUCTION else {}
app = FastAPI(title="Incident Management API", version="0.3.0", **docs)
app.include_router(health_router)
app.include_router(demo_router)
app.include_router(auth_router)
app.include_router(incidents_router)
app.include_router(comments_router)
app.include_router(sla_router)
app.include_router(triage_router)
app.include_router(users_router)
app.include_router(analytics_router)
app.include_router(exports_router)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # FastAPI echoes submitted values ("input") in 422 responses by default. Strip them so
    # passwords and other credentials are never reflected back or captured downstream.
    errors = [{"loc": error.get("loc"), "msg": error.get("msg"), "type": error.get("type")} for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})
