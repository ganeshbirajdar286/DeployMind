import asyncio
import ipaddress
import os
import socket
from pathlib import Path
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse
import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks, Security, Depends
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel, Field

from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.agent.graph import run_devops_workflow
from ai_devops_agent.deployer.orchestrator import CloudDeployer
from ai_devops_agent.utils.sanitizer import redact_secrets

app = FastAPI(
    title="AI DevOps Agent Engine",
    description="LangGraph-powered DevOps analysis, generation, and validation microservice with Cloud Sandboxes",
    version="0.1.0",
)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> bool:
    """Verifies API key if AGENT_ENGINE_API_KEY environment variable is configured."""
    expected_key = os.getenv("AGENT_ENGINE_API_KEY")
    if expected_key:
        if not api_key or api_key != expected_key:
            raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing API key.")
    return True


def is_safe_webhook_url(url: str) -> bool:
    """Validates that a webhook URL is HTTP/HTTPS and does not target internal or private network assets (SSRF protection)."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        # Disallow loopback / cloud metadata hosts
        blocked_hosts = {
            "localhost", "127.0.0.1", "::1", "metadata.google.internal",
            "169.254.169.254", "instance-data"
        }
        if hostname.lower() in blocked_hosts or hostname.lower().endswith(".local"):
            return False

        # Resolve IP to verify it is not private / link-local / loopback
        try:
            resolved_ip = socket.gethostbyname(hostname)
            ip_addr = ipaddress.ip_address(resolved_ip)
            if ip_addr.is_private or ip_addr.is_loopback or ip_addr.is_link_local or ip_addr.is_multicast or ip_addr.is_reserved:
                return False
        except Exception:
            return False

        return True
    except Exception:
        return False


class RunAgentRequest(BaseModel):
    repo_path: str = Field(description="Local filesystem path to repository")
    job_id: Optional[str] = Field(default=None, description="Unique job ID from Express backend")
    webhook_url: Optional[str] = Field(default=None, description="Callback URL for event streaming")
    max_retries: int = Field(default=2, description="Maximum AI patch attempts")
    sandbox_mode: str = Field(default="auto", description="Verification sandbox: auto, local, or github")
    github_repo: Optional[str] = Field(default=None, description="GitHub repository (owner/repo) for cloud sandbox")
    github_token: Optional[str] = Field(default=None, description="GitHub API token")
    github_branch: Optional[str] = Field(default="main", description="Branch to verify")


class InspectRequest(BaseModel):
    repo_path: str = Field(description="Local filesystem path to repository")


class DeployRequest(BaseModel):
    repo_path: str = Field(description="Local filesystem path to repository")
    github_repo: str = Field(description="GitHub repository (owner/repo)")
    branch: str = Field(default="main", description="Branch to deploy from")
    env_vars: Dict[str, str] = Field(default_factory=dict, description="Key-value environment variables")
    vercel_token: Optional[str] = Field(default=None, description="Vercel Access Token")
    render_api_key: Optional[str] = Field(default=None, description="Render API Key")
    job_id: Optional[str] = Field(default=None, description="Job identifier")
    webhook_url: Optional[str] = Field(default=None, description="Webhook callback URL")


def notify_webhook(webhook_url: str, payload: Dict[str, Any]):
    """Sends background event callbacks to Node.js backend with SSRF protection and secret redaction."""
    if not is_safe_webhook_url(webhook_url):
        print(f"[Webhook Security Block] Rejecting dangerous or private webhook URL: {webhook_url}")
        return

    try:
        requests.post(webhook_url, json=payload, timeout=10)
    except Exception as e:
        print(f"[Webhook Error] Failed to notify {webhook_url}: {e}")


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "agent-engine",
        "version": "0.1.0",
    }


@app.post("/api/agent/inspect", dependencies=[Depends(verify_api_key)])
def inspect_endpoint(req: InspectRequest):
    repo_path = Path(req.repo_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

    analysis = analyze_repository(repo_path)
    return analysis.model_dump()


@app.post("/api/agent/run", dependencies=[Depends(verify_api_key)])
def run_agent_endpoint(req: RunAgentRequest, background_tasks: BackgroundTasks):
    repo_path = Path(req.repo_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

    result_state = run_devops_workflow(
        repo_path=str(repo_path),
        max_retries=req.max_retries,
        job_id=req.job_id,
        sandbox_mode=req.sandbox_mode,
        github_repo=req.github_repo,
        github_token=req.github_token,
        github_branch=req.github_branch,
    )

    response_payload = {
        "job_id": req.job_id,
        "stage": result_state.get("current_stage"),
        "build_succeeded": result_state.get("build_succeeded", False),
        "compose_succeeded": result_state.get("compose_succeeded", False),
        "retry_count": result_state.get("retry_count", 0),
        "patch_history": result_state.get("patch_history", []),
        "artifacts": result_state.get("artifacts", {}),
        "analysis": result_state.get("analysis"),
        "status_message": result_state.get("status_message", ""),
        "compose_logs": [redact_secrets(l) for l in result_state.get("compose_logs", [])],
    }

    if req.webhook_url:
        background_tasks.add_task(notify_webhook, req.webhook_url, response_payload)

    return response_payload


@app.post("/api/agent/deploy", dependencies=[Depends(verify_api_key)])
def deploy_endpoint(req: DeployRequest, background_tasks: BackgroundTasks):
    repo_path = Path(req.repo_path).resolve()
    if not repo_path.exists() or not repo_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.repo_path}")

    analysis = analyze_repository(repo_path)
    deployer = CloudDeployer(
        vercel_token=req.vercel_token,
        render_api_key=req.render_api_key,
    )

    logs: List[str] = []

    def log_collector(msg: str):
        sanitized_msg = redact_secrets(msg)
        logs.append(sanitized_msg)
        if req.webhook_url and req.job_id:
            if is_safe_webhook_url(req.webhook_url):
                try:
                    requests.post(
                        req.webhook_url,
                        json={"job_id": req.job_id, "event": "log", "message": sanitized_msg},
                        timeout=5,
                    )
                except Exception:
                    pass

    summary = deployer.deploy_project(
        analysis=analysis,
        github_repo=req.github_repo,
        branch=req.branch,
        env_values=req.env_vars,
        log_callback=log_collector,
    )

    response_payload = {
        "job_id": req.job_id,
        "repo_name": summary.repo_name,
        "branch": summary.branch_name,
        "all_successful": summary.all_successful,
        "live_frontend_url": summary.live_frontend_url,
        "live_backend_url": summary.live_backend_url,
        "logs": logs,
    }

    if req.webhook_url:
        background_tasks.add_task(notify_webhook, req.webhook_url, response_payload)

    return response_payload


if __name__ == "__main__":
    import uvicorn
    from ai_devops_agent.utils.banner import print_banner
    print_banner()
    port = int(os.getenv("PORT", 8000))
    print(f"\033[38;2;0;229;255m🚀 FastAPI Agent Engine running on http://localhost:{port}\033[0m\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
