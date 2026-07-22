#!/usr/bin/env python3
"""
usai-hpc-proxy: A local proxy server that forwards requests to the USAi API.

The API key is stored only in this process's environment (via the systemd
EnvironmentFile). Users on the system never see the key; they only interact
with the Unix domain socket or TCP port exposed by this server.

Dependencies (all available in miniforge):
    pip install fastapi uvicorn httpx
"""

import os
import sys
import logging
import textwrap
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

# ---------------------------------------------------------------------------
# Configuration (all from environment, set in the systemd EnvironmentFile)
# ---------------------------------------------------------------------------
USAI_BASE_URL = os.environ.get("USAI_BASE_URL", "https://api.doc.usai.gov")
USAI_API_KEY  = os.environ.get("USAI_API_KEY", "")
PROXY_SOCKET  = os.environ.get("PROXY_SOCKET", "")          # Unix socket path
PROXY_HOST    = os.environ.get("PROXY_HOST", "127.0.0.1")   # TCP fallback
PROXY_PORT    = int(os.environ.get("PROXY_PORT", "8742"))
LOG_LEVEL     = os.environ.get("LOG_LEVEL", "INFO").upper()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("usai-proxy")

# ---------------------------------------------------------------------------
# Startup check
# ---------------------------------------------------------------------------
def _check_config() -> None:
    if not USAI_API_KEY:
        log.error("USAI_API_KEY is not set. "
                  "Set it in the EnvironmentFile before starting the proxy.")
        sys.exit(1)
    log.info("USAi proxy starting — upstream: %s", USAI_BASE_URL)
    if PROXY_SOCKET:
        log.info("Listening on Unix socket: %s", PROXY_SOCKET)
    else:
        log.info("Listening on TCP %s:%s", PROXY_HOST, PROXY_PORT)


# ---------------------------------------------------------------------------
# Shared async HTTP client
# ---------------------------------------------------------------------------
_http_client: httpx.AsyncClient | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _check_config()
    _http_client = httpx.AsyncClient(
        base_url=USAI_BASE_URL,
        headers={"Authorization": f"Bearer {USAI_API_KEY}"},
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=5.0),
    )
    yield
    await _http_client.aclose()


app = FastAPI(
    title="USAi HPC Proxy",
    description="Local proxy for the USAi API. The API key is never exposed to clients.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health check — clients can use this to verify the proxy is running
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "upstream": USAI_BASE_URL}


# ---------------------------------------------------------------------------
# Models passthrough
# ---------------------------------------------------------------------------
@app.get("/api/v1/models")
async def list_models() -> JSONResponse:
    resp = await _http_client.get("/api/v1/models")
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# ---------------------------------------------------------------------------
# Chat completions passthrough (streaming and non-streaming)
# ---------------------------------------------------------------------------
@app.post("/api/v1/chat/completions")
async def chat_completions(request: Request) -> Any:
    body: dict = await request.json()

    if body.get("stream", False):
        # Stream the response back chunk by chunk
        async def _stream():
            async with _http_client.stream(
                "POST", "/api/v1/chat/completions", json=body
            ) as upstream:
                async for chunk in upstream.aiter_bytes():
                    yield chunk

        return StreamingResponse(_stream(), media_type="text/event-stream")

    resp = await _http_client.post("/api/v1/chat/completions", json=body)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# ---------------------------------------------------------------------------
# Embeddings passthrough
# ---------------------------------------------------------------------------
@app.post("/api/v1/embeddings")
async def embeddings(request: Request) -> JSONResponse:
    body: dict = await request.json()
    resp = await _http_client.post("/api/v1/embeddings", json=body)
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# ---------------------------------------------------------------------------
# Entry point (used when running directly, not via uvicorn CLI)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    if PROXY_SOCKET:
        # Remove stale socket file if present
        if os.path.exists(PROXY_SOCKET):
            os.unlink(PROXY_SOCKET)
        uvicorn.run(
            "proxy:app",
            uds=PROXY_SOCKET,
            log_level=LOG_LEVEL.lower(),
        )
        # After the server exits, clean up the socket file
        if os.path.exists(PROXY_SOCKET):
            os.unlink(PROXY_SOCKET)
    else:
        uvicorn.run(
            "proxy:app",
            host=PROXY_HOST,
            port=PROXY_PORT,
            log_level=LOG_LEVEL.lower(),
        )
