import logging

from fastapi import FastAPI

from app.routes.chat import router as chat_router

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

app = FastAPI(title="LLM Gateway")
app.include_router(chat_router)
