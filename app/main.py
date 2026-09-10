from fastapi import FastAPI

from app.routes.chat import router as chat_router

app = FastAPI(title="LLM Gateway")
app.include_router(chat_router)
