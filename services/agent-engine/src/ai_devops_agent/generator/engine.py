from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional
from jinja2 import Environment, FileSystemLoader
from ai_devops_agent.analyzer.models import (
    ProjectAnalysis,
    FrameworkType,
    ServiceMetadata,
    Language,
    PackageManager,
)

try:
    from importlib.resources import files
    pkg_templates = Path(str(files("ai_devops_agent").joinpath("templates")))
    if pkg_templates.exists():
        TEMPLATES_DIR = pkg_templates
    else:
        TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
except Exception:
    TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def get_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["to_json"] = lambda v: json.dumps(v.split() if isinstance(v, str) else v)
    return env


def generate_devops_bundle(
    analysis: ProjectAnalysis,
    branch_name: str = "ai-devops-setup",
    extra_env_vars: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Generates all DevOps configuration files from analysis metadata."""
    env = get_jinja_env()
    artifacts: Dict[str, str] = {}

    primary_port = 8080
    primary_health_path = "/"

    # 1. Generate Service-Level Files (Dockerfiles & .dockerignore)
    for service in analysis.services:
        primary_port = service.default_port
        primary_health_path = service.health_check_path

        dockerfile_template_name = None
        if service.framework == FrameworkType.REACT_VITE:
            dockerfile_template_name = "docker/react_vite.Dockerfile.j2"
        elif service.framework == FrameworkType.NEXTJS:
            dockerfile_template_name = "docker/nextjs.Dockerfile.j2"
        elif service.framework in {FrameworkType.EXPRESS, FrameworkType.NESTJS, FrameworkType.NODE_GENERIC}:
            dockerfile_template_name = "docker/express.Dockerfile.j2"
        elif service.framework == FrameworkType.FASTAPI:
            dockerfile_template_name = "docker/fastapi.Dockerfile.j2"
        elif service.framework == FrameworkType.DJANGO:
            dockerfile_template_name = "docker/django.Dockerfile.j2"
        elif service.framework in {FrameworkType.FLASK, FrameworkType.PYTHON_GENERIC}:
            dockerfile_template_name = "docker/fastapi.Dockerfile.j2"

        if dockerfile_template_name:
            template = env.get_template(dockerfile_template_name)
            
            start_args = None
            if service.start_command:
                args = service.start_command.split()
                start_args = ", ".join(f'"{arg}"' for arg in args)

            rendered_dockerfile = template.render(
                **service.model_dump(),
                start_command_args=start_args,
                repo_name=analysis.repo_name,
            )

            prefix = "" if service.path == "." else f"{service.path}/"
            artifacts[f"{prefix}Dockerfile"] = rendered_dockerfile

            dockerignore_template = env.get_template("docker/.dockerignore.j2")
            artifacts[f"{prefix}.dockerignore"] = dockerignore_template.render()

            # Auto-generate requirements.txt if python service lacks a dependency manifest
            if service.language == Language.PYTHON and service.package_manager == PackageManager.PIP:
                if not service.details.get("has_requirements", True) and not service.details.get("has_pyproject", False):
                    req_path = f"{prefix}requirements.txt"
                    if req_path not in artifacts:
                        pkgs: List[str] = []
                        seen_names = set()

                        def add_pkg(spec: str):
                            raw_name = re.split(r"[><=~]", spec)[0].strip().lower().replace("-", "_")
                            if raw_name not in seen_names:
                                seen_names.add(raw_name)
                                pkgs.append(spec)

                        if service.framework == FrameworkType.DJANGO:
                            add_pkg("Django>=4.2,<5.0")
                            add_pkg("gunicorn>=21.2.0")
                            add_pkg("whitenoise>=6.6.0")
                        elif service.framework == FrameworkType.FASTAPI:
                            add_pkg("fastapi>=0.100.0")
                            add_pkg("uvicorn[standard]>=0.22.0")
                        elif service.framework == FrameworkType.FLASK:
                            add_pkg("Flask>=3.0.0")
                            add_pkg("gunicorn>=21.2.0")

                        for p in service.extra_pypi_packages:
                            add_pkg(p)

                        if pkgs:
                            artifacts[req_path] = "\n".join(pkgs) + "\n"

            if service.env_vars_detected:
                env_content = "# Generated environment variables template\n"
                for var in service.env_vars_detected:
                    env_content += f"{var}=\n"
                artifacts[f"{prefix}.env.example"] = env_content

    # 2. Generate Root Docker Compose with unique host ports
    allocated_host_ports = set()
    compose_services = []
    for s in analysis.services:
        s_dict = s.model_dump()
        target_port = s.default_port
        host_port = target_port
        while host_port in allocated_host_ports:
            host_port += 1
        allocated_host_ports.add(host_port)
        s_dict["host_port"] = host_port
        compose_services.append(s_dict)

    has_postgres = any(
        "psycopg" in str(s.extra_pypi_packages).lower()
        or "libpq" in str(s.system_packages).lower()
        or s.framework == FrameworkType.DJANGO
        for s in analysis.services
    )

    compose_template = env.get_template("compose/docker-compose.yml.j2")
    artifacts["docker-compose.yml"] = compose_template.render(
        repo_name=analysis.repo_name,
        services=compose_services,
        has_postgres=has_postgres,
    )

    # Detect language support across services
    has_node = any(s.language in (Language.JAVASCRIPT, Language.TYPESCRIPT) for s in analysis.services)
    has_python = any(s.language == Language.PYTHON for s in analysis.services)
    primary_deploy_target = analysis.suggested_deployment_targets.get(analysis.services[0].name, "Render") if analysis.services else "Render"
    primary_node_version = "20"
    primary_python_version = "3.11"
    for s in analysis.services:
        if s.language in (Language.JAVASCRIPT, Language.TYPESCRIPT) and s.runtime_version:
            primary_node_version = s.runtime_version
        elif s.language == Language.PYTHON and s.runtime_version:
            primary_python_version = s.runtime_version

    # 3. Generate GitHub Actions CI & Delivery Pipeline
    ci_template = env.get_template("github/ci.yml.j2")
    artifacts[".github/workflows/ci.yml"] = ci_template.render(
        repo_name=analysis.repo_name,
        services=[s.model_dump() for s in analysis.services],
        has_node=has_node,
        has_python=has_python,
        primary_node_version=primary_node_version,
        primary_python_version=primary_python_version,
        primary_deploy_target=primary_deploy_target,
        default_port=primary_port,
        health_check_path=primary_health_path,
    )

    # Collect all detected environment variables across all services
    all_env_vars = []
    for s in analysis.services:
        all_env_vars.extend(s.env_vars_detected)
    if extra_env_vars:
        all_env_vars.extend(extra_env_vars)
    all_env_vars = sorted(list(set(all_env_vars)))

    # 4. Generate Cloud Sandbox Verification Workflow (GitHub Actions)
    verify_template = env.get_template("github/ai_devops_verify.yml.j2")
    artifacts[".github/workflows/ai-devops-verify.yml"] = verify_template.render(
        default_port=primary_port,
        health_check_path=primary_health_path,
        branch_name=branch_name,
        all_env_vars=all_env_vars,
    )

    # 5. Generate Release Automation Workflow
    release_template = env.get_template("github/release.yml.j2")
    artifacts[".github/workflows/release.yml"] = release_template.render()

    # 6. Generate Stale Issues & PRs Automation
    stale_template = env.get_template("github/stale.yml.j2")
    artifacts[".github/workflows/stale.yml"] = stale_template.render()

    # 7. Generate Dependabot Dependency Upgrades Configuration
    dependabot_template = env.get_template("github/dependabot.yml.j2")
    artifacts[".github/dependabot.yml"] = dependabot_template.render(
        has_node=has_node,
        has_python=has_python,
    )

    # 8. Generate DEPLOYMENT.md
    artifacts["DEPLOYMENT.md"] = generate_deployment_readme(analysis)

    return artifacts


def generate_deployment_readme(analysis: ProjectAnalysis) -> str:
    md = f"# DevOps Configuration for {analysis.repo_name}\n\n"
    md += "This repository has been configured automatically by the **AI DevOps Agent**.\n\n"
    md += "## Architecture Summary\n\n"
    md += f"- **Repository Type:** {'Monorepo' if analysis.is_monorepo else 'Single Service'}\n"
    for s in analysis.services:
        target = analysis.suggested_deployment_targets.get(s.name, "Render")
        md += f"- **Service `{s.name}`:**\n"
        md += f"  - Framework: `{s.framework.value}`\n"
        md += f"  - Language: `{s.language.value}`\n"
        md += f"  - Package Manager: `{s.package_manager.value}`\n"
        md += f"  - Exposed Port: `{s.default_port}`\n"
        md += f"  - Recommended Deployment Target: **{target.capitalize()}**\n\n"

    md += "## Running Locally with Docker Compose\n\n"
    md += "```bash\n# Build and start all services locally\ndocker compose up --build\n```\n\n"

    md += "## CI/CD & Automation Suite\n\n"
    md += "- `.github/workflows/ci.yml`: Full multi-job CI/CD pipeline (Metadata outputs, Linting, Testing, Trivy Security Scan, GHCR Container Image Build, Push-Based Deployment).\n"
    md += "- `.github/workflows/ai-devops-verify.yml`: Cloud sandbox workflow for automated container verification.\n"
    md += "- `.github/workflows/release.yml`: Automated GitHub release creation and changelog generation on `v*` tags.\n"
    md += "- `.github/workflows/stale.yml`: Automated cleanup of inactive issues and pull requests.\n"
    md += "- `.github/dependabot.yml`: Automated weekly dependency upgrades for package managers, Docker, and GitHub Actions.\n"

    return md
