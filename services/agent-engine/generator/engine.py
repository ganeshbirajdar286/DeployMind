import json
from pathlib import Path
from typing import Dict
from jinja2 import Environment, FileSystemLoader
from analyzer.models import ProjectAnalysis, FrameworkType, ServiceMetadata

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


def generate_devops_bundle(analysis: ProjectAnalysis) -> Dict[str, str]:
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
        elif service.framework in {FrameworkType.FLASK, FrameworkType.DJANGO, FrameworkType.PYTHON_GENERIC}:
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

            if service.env_vars_detected:
                env_content = "# Generated environment variables template\n"
                for var in service.env_vars_detected:
                    env_content += f"{var}=\n"
                artifacts[f"{prefix}.env.example"] = env_content

    # 2. Generate Root Docker Compose
    compose_template = env.get_template("compose/docker-compose.yml.j2")
    artifacts["docker-compose.yml"] = compose_template.render(
        repo_name=analysis.repo_name,
        services=[s.model_dump() for s in analysis.services],
    )

    # 3. Generate GitHub Actions CI Workflow
    ci_template = env.get_template("github/ci.yml.j2")
    artifacts[".github/workflows/ci.yml"] = ci_template.render(
        repo_name=analysis.repo_name,
        services=[s.model_dump() for s in analysis.services],
    )

    # 4. Generate Cloud Sandbox Verification Workflow (GitHub Actions)
    verify_template = env.get_template("github/ai_devops_verify.yml.j2")
    artifacts[".github/workflows/ai-devops-verify.yml"] = verify_template.render(
        default_port=primary_port,
        health_check_path=primary_health_path,
    )

    # 5. Generate DEPLOYMENT.md
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

    md += "## CI/CD & Cloud Sandbox Testing\n\n"
    md += "- `.github/workflows/ci.yml`: Standard pull request test & build workflow.\n"
    md += "- `.github/workflows/ai-devops-verify.yml`: Cloud sandbox workflow for automated container verification.\n"

    return md
