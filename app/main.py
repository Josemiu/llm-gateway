import logging

from fastapi import FastAPI

from app.routes.chat import router as chat_router
from app.routes.usage import router as usage_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

app = FastAPI(title="LLM Gateway")
app.include_router(chat_router)
app.include_router(usage_router)
