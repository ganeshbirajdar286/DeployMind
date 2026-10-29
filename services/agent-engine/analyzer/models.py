from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class FrameworkType(str, Enum):
    # Frontend
    REACT_VITE = "react_vite"
    NEXTJS = "nextjs"
    REACT_CRA = "react_cra"
    VUE_VITE = "vue_vite"
    STATIC_HTML = "static_html"

    # Backend
    FASTAPI = "fastapi"
    FLASK = "flask"
    DJANGO = "django"
    EXPRESS = "express"
    NESTJS = "nestjs"

    # Generic
    NODE_GENERIC = "node_generic"
    PYTHON_GENERIC = "python_generic"
    UNKNOWN = "unknown"


class Language(str, Enum):
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    PYTHON = "python"
    UNKNOWN = "unknown"


class PackageManager(str, Enum):
    NPM = "npm"
    PNPM = "pnpm"
    YARN = "yarn"
    BUN = "bun"
    UV = "uv"
    POETRY = "poetry"
    PIP = "pip"
    UNKNOWN = "unknown"


class ServiceMetadata(BaseModel):
    name: str = Field(description="Name of the service (e.g. frontend, backend, root)")
    path: str = Field(default=".", description="Relative path from repo root")
    framework: FrameworkType
    language: Language
    package_manager: PackageManager
    runtime_version: Optional[str] = Field(default=None, description="e.g. Node 20 or Python 3.11")
    build_command: Optional[str] = Field(default=None, description="e.g. npm run build")
    start_command: Optional[str] = Field(default=None, description="e.g. uvicorn main:app --host 0.0.0.0 --port 8000")
    test_command: Optional[str] = Field(default=None, description="e.g. npm test or pytest")
    default_port: int = Field(default=8080, description="Container port exposed")
    health_check_path: str = Field(default="/", description="Health check HTTP route")
    output_dir: Optional[str] = Field(default=None, description="Build output directory (e.g. dist, .next)")
    env_vars_detected: List[str] = Field(default_factory=list, description="Environment variables referenced in code")
    extra_pypi_packages: List[str] = Field(default_factory=list, description="Unlisted PyPI packages detected via AST")
    system_packages: List[str] = Field(default_factory=list, description="OS-level apt packages required by libraries (e.g. ffmpeg)")
    has_existing_dockerfile: bool = False
    has_existing_compose: bool = False
    has_existing_ci: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class ProjectAnalysis(BaseModel):
    repo_name: str
    is_monorepo: bool = False
    services: List[ServiceMetadata] = Field(default_factory=list)
    suggested_deployment_targets: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of service name to recommended provider: vercel, render, etc."
    )
    warnings: List[str] = Field(default_factory=list)
    missing_info: List[str] = Field(default_factory=list)
