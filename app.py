"""FastAPI web server for the TPRM agent."""

from __future__ import annotations

import json
import warnings
warnings.filterwarnings("ignore", message="urllib3.*OpenSSL")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from typing import Optional
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from agent import run_agent_streaming

app = FastAPI(title="TPRM Agent")


class BriefRequest(BaseModel):
    query: str


class BriefResponse(BaseModel):
    query: str
    brief: Optional[str] = None
    error: Optional[str] = None


@app.post("/api/brief")
def create_brief(req: BriefRequest):
    def event_stream():
        try:
            for event in run_agent_streaming(req.query):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
