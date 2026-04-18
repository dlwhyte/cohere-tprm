"""FastAPI web server for the TPRM agent."""

from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", message="urllib3.*OpenSSL")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Optional
from pydantic import BaseModel

from dotenv import load_dotenv
load_dotenv()

from agent import run_agent

app = FastAPI(title="TPRM Agent")


class BriefRequest(BaseModel):
    query: str


class BriefResponse(BaseModel):
    query: str
    brief: Optional[str] = None
    error: Optional[str] = None


@app.post("/api/brief")
def create_brief(req: BriefRequest) -> BriefResponse:
    try:
        result = run_agent(req.query)
        if result is None:
            return BriefResponse(
                query=req.query,
                brief=None,
                error="Agent did not produce a final answer within step limit.",
            )
        return BriefResponse(query=req.query, brief=result)
    except Exception as e:
        return BriefResponse(query=req.query, brief=None, error=str(e))


app.mount("/", StaticFiles(directory="static", html=True), name="static")
