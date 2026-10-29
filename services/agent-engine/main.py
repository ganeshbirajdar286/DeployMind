import asyncio
import os
from pathlib import Path
from typing import Optional, Dict, Any, List
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from analyzer.detector import analyze_repository
from agent.graph import run_devops_workflow

app = FastAPI(
    title="AI DevOps Agent Engine",
    description="LangGraph-powered DevOps analysis, generation, and validation microservice",
    version="0.1.0",
)


class RunAgentRequest(BaseModel):
    repo_path: str = Field(description="Local filesystem path to repository")
    job_id: Optional[str] = Field(default=None, description="Unique job ID from Express backend")
    webhook_url: Optional[str] = Field(default=None, description="Callback URL for event streaming")
    max_retries: int = Field(default=2, description="Maximum AI patch attempts")


class InspectRequest(BaseModel):
    repo_path: str = Field(description="Local filesystem path to repository")


async def notify_webhook(webhook_url: str, payload: Dict[str, Any]):
    """Sends background event callbacks to Node.js backend."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(webhook_url, json=payload)
    except Exception as e:
        print(f"[Webhook Error] Failed to notify {webhook_url}: {e}")


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "agent-engine",
        "version": "0.1.0",
    }


@app.post("/api/agent/inspect")
def inspect_endpoint(req: InspectRequest):
    repo_path = Path(req.repo_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

    analysis = analyze_repository(repo_path)
    return analysis.model_dump()


@app.post("/api/agent/run")
async def run_agent_endpoint(req: RunAgentRequest, background_tasks: BackgroundTasks):
    repo_path = Path(req.repo_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

    # Execute the LangGraph workflow
    result_state = run_devops_workflow(
        repo_path=str(repo_path),
        max_retries=req.max_retries,
        job_id=req.job_id,
    )

    response_payload = {
        "job_id": req.job_id,
        "stage": result_state.get("current_stage"),
        "build_succeeded": result_state.get("build_succeeded", False),
        "retry_count": result_state.get("retry_count", 0),
        "patch_history": result_state.get("patch_history", []),
        "artifacts": result_state.get("artifacts", {}),
        "analysis": result_state.get("analysis"),
        "status_message": result_state.get("status_message", ""),
    }

    if req.webhook_url:
        background_tasks.add_task(notify_webhook, req.webhook_url, response_payload)

    return response_payload


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
