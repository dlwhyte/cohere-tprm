"""FastAPI web server for the TPRM agent."""

from __future__ import annotations

import json
import warnings
warnings.filterwarnings("ignore", message="urllib3.*OpenSSL")

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from agent import run_agent_streaming

app = FastAPI(title="TPRM Agent")


@app.post("/api/brief")
async def create_brief(
    query: str = Form(...),
    sbom: Optional[UploadFile] = File(None),
):
    sbom_json = None
    if sbom and sbom.filename:
        content = await sbom.read()
        sbom_json = content.decode("utf-8")

    def event_stream():
        try:
            for event in run_agent_streaming(query, sbom_json=sbom_json):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
