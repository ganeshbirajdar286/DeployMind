from typing import Dict, List, Optional
from pydantic import BaseModel, Field


class DeploymentResult(BaseModel):
    service_name: str
    target_platform: str  # "vercel" | "render"
    success: bool
    live_url: Optional[str] = None
    dashboard_url: Optional[str] = None
    service_id: Optional[str] = None
    deployment_id: Optional[str] = None
    status: str = "unknown"
    error_message: Optional[str] = None
    env_vars_configured: List[str] = Field(default_factory=list)


class MultiServiceDeploymentSummary(BaseModel):
    repo_name: str
    branch_name: str
    all_successful: bool
    frontend_result: Optional[DeploymentResult] = None
    backend_result: Optional[DeploymentResult] = None
    live_frontend_url: Optional[str] = None
    live_backend_url: Optional[str] = None
    details: List[DeploymentResult] = Field(default_factory=list)
