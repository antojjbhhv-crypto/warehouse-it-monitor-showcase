"""A tiny local service used to demonstrate the monitor without external systems."""

from fastapi import FastAPI

app = FastAPI(title="Warehouse Demo Service", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "warehouse-demo-service"}


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "Local demo service is running", "health": "/healthz"}
