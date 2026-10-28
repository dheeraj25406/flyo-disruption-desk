import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db import create_tables, make_engine, make_session_factory, seed_demo_data
from app.services.copilot import build_copilot_provider


DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


def _cors_origins() -> list[str]:
    configured_origins = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if "*" in configured_origins:
        raise ValueError("CORS_ORIGINS must not contain '*'.")
    return list(dict.fromkeys((*DEFAULT_CORS_ORIGINS, *configured_origins)))


def create_app(database_url: Optional[str] = None, seed_data: bool = True, copilot_provider=None) -> FastAPI:
    engine = make_engine(database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        create_tables(engine)
        if seed_data:
            seed_demo_data(session_factory)
        yield
        engine.dispose()

    application = FastAPI(title="Flyo Disruption Desk API", version="0.1.0", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.state.session_factory = session_factory
    # Provider selection is environment based; the Gemini HTTP client is not created here.
    application.state.copilot_provider = copilot_provider or build_copilot_provider()
    application.include_router(router)
    return application


app = create_app()
