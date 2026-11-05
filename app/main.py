from fastapi import FastAPI
from app.routers import tools, webhooks

app = FastAPI(title="Voice Agent")

app.include_router(tools.router,    prefix="/tools")
app.include_router(webhooks.router, prefix="/webhooks")


@app.get("/health")
async def health():
    return {"status": "ok"}
