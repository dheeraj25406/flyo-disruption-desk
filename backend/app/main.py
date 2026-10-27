from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI

from app.api.routes import router
from app.db import create_tables, make_engine, make_session_factory, seed_demo_data
from app.services.copilot import build_copilot_provider


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
    application.state.session_factory = session_factory
    # Provider selection is environment based; the Gemini HTTP client is not created here.
    application.state.copilot_provider = copilot_provider or build_copilot_provider()
    application.include_router(router)
    return application


app = create_app()
