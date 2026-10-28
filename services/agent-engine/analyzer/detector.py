import os
from pathlib import Path
from typing import List, Optional
from .models import (
    ProjectAnalysis,
    ServiceMetadata,
    FrameworkType,
    Language,
    PackageManager,
)
from .node_detector import detect_node_service
from .python_detector import detect_python_service
from .env_detector import detect_environment_variables


def analyze_repository(repo_path: Path) -> ProjectAnalysis:
    repo_path = repo_path.resolve()
    repo_name = repo_path.name
    warnings: List[str] = []
    missing_info: List[str] = []

    # 1. Monorepo Heuristic Check
    subdirs_to_check = [
        ("frontend", "backend"),
        ("client", "server"),
        ("web", "api"),
        ("ui", "server"),
    ]

    detected_services: List[ServiceMetadata] = []
    is_monorepo = False

    for client_name, server_name in subdirs_to_check:
        c_path = repo_path / client_name
        s_path = repo_path / server_name
        if c_path.is_dir() and s_path.is_dir():
            is_monorepo = True
            # Analyze client
            c_service = detect_node_service(c_path, service_name=client_name)
            if c_service:
                c_service.path = client_name
                c_service.env_vars_detected = detect_environment_variables(c_path)
                detected_services.append(c_service)

            # Analyze server (could be Node or Python)
            s_service = detect_python_service(s_path, service_name=server_name) or detect_node_service(s_path, service_name=server_name)
            if s_service:
                s_service.path = server_name
                s_service.env_vars_detected = detect_environment_variables(s_path)
                detected_services.append(s_service)
            break

    # If not a standard client/server monorepo, check root directly
    if not detected_services:
        # Try Node detection first
        root_service = detect_node_service(repo_path, service_name=repo_name)
        if not root_service:
            # Try Python detection
            root_service = detect_python_service(repo_path, service_name=repo_name)

        if root_service:
            root_service.path = "."
            root_service.env_vars_detected = detect_environment_variables(repo_path)
            detected_services.append(root_service)
        else:
            warnings.append("Could not identify a supported Node.js or Python framework.")
            missing_info.append("Framework and start command must be provided manually.")

    # 2. Suggested Deployment Targets
    deployment_targets = {}
    for svc in detected_services:
        if svc.framework in {FrameworkType.REACT_VITE, FrameworkType.NEXTJS, FrameworkType.REACT_CRA, FrameworkType.VUE_VITE, FrameworkType.STATIC_HTML}:
            deployment_targets[svc.name] = "vercel"
        elif svc.framework in {FrameworkType.FASTAPI, FrameworkType.FLASK, FrameworkType.DJANGO, FrameworkType.EXPRESS, FrameworkType.NESTJS}:
            deployment_targets[svc.name] = "render"
        else:
            deployment_targets[svc.name] = "render"

        # Validate start commands
        if not svc.start_command:
            missing_info.append(f"No start command detected for service '{svc.name}'.")

        # Check env vars
        if svc.env_vars_detected and not (repo_path / ".env.example").exists() and not (Path(svc.path) / ".env.example").exists():
            warnings.append(
                f"Service '{svc.name}' references environment variables ({', '.join(svc.env_vars_detected[:3])}...) but no .env.example was found."
            )

    return ProjectAnalysis(
        repo_name=repo_name,
        is_monorepo=is_monorepo,
        services=detected_services,
        suggested_deployment_targets=deployment_targets,
        warnings=warnings,
        missing_info=missing_info,
    )
