from pathlib import Path
from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.generator.engine import generate_devops_bundle

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_generate_react_vite_bundle():
    analysis = analyze_repository(FIXTURES_DIR / "sample-react-vite")
    bundle = generate_devops_bundle(analysis)

    assert "Dockerfile" in bundle
    assert ".dockerignore" in bundle
    assert "docker-compose.yml" in bundle
    assert ".github/workflows/ci.yml" in bundle
    assert ".github/workflows/release.yml" in bundle
    assert ".github/workflows/stale.yml" in bundle
    assert ".github/dependabot.yml" in bundle
    assert "DEPLOYMENT.md" in bundle

    ci_yml = bundle[".github/workflows/ci.yml"]
    assert "prepare:" in ci_yml
    assert "image_tag:" in ci_yml
    assert "lint:" in ci_yml
    assert "test:" in ci_yml
    assert "security-scan:" in ci_yml
    assert "container-build:" in ci_yml
    assert "ghcr.io" in ci_yml
    assert "deploy:" in ci_yml

    dockerfile = bundle["Dockerfile"]
    assert "FROM node:20-alpine AS builder" in dockerfile
    assert "FROM nginx:alpine AS runner" in dockerfile
    assert "EXPOSE 80" in dockerfile


def test_generate_fastapi_bundle():
    analysis = analyze_repository(FIXTURES_DIR / "sample-fastapi")
    bundle = generate_devops_bundle(analysis)

    assert "Dockerfile" in bundle
    assert "docker-compose.yml" in bundle

    dockerfile = bundle["Dockerfile"]
    assert "FROM python:3.11-slim AS builder" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "uvicorn" in dockerfile


def test_generate_bundle_with_ast_packages():
    from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager

    analysis = ProjectAnalysis(
        repo_name="rag-video-test",
        services=[
            ServiceMetadata(
                name="api",
                path=".",
                framework=FrameworkType.FASTAPI,
                language=Language.PYTHON,
                package_manager=PackageManager.PIP,
                runtime_version="3.11",
                start_command="python main.py",
                extra_pypi_packages=["openai-whisper", "yt-dlp"],
                system_packages=["ffmpeg"],
            )
        ]
    )
    bundle = generate_devops_bundle(analysis)
    dockerfile = bundle["Dockerfile"]

    # Verify system packages in builder and runner
    assert "ffmpeg" in dockerfile
    assert "pip install --no-cache-dir openai-whisper yt-dlp" in dockerfile


def test_generate_django_bundle():
    from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager

    analysis = ProjectAnalysis(
        repo_name="real-estate-test",
        services=[
            ServiceMetadata(
                name="web",
                path=".",
                framework=FrameworkType.DJANGO,
                language=Language.PYTHON,
                package_manager=PackageManager.PIP,
                runtime_version="3.11",
                start_command='gunicorn btre.wsgi:application --bind 0.0.0.0:8000',
                default_port=8000,
                extra_pypi_packages=["pillow", "psycopg2-binary"],
                details={"has_requirements": False, "has_pyproject": False},
            )
        ]
    )
    bundle = generate_devops_bundle(analysis)

    assert "Dockerfile" in bundle
    assert "requirements.txt" in bundle
    assert "docker-compose.yml" in bundle

    dockerfile = bundle["Dockerfile"]
    assert "gunicorn" in dockerfile
    assert "btre.wsgi:application" in dockerfile
    assert "libpq-dev" in dockerfile

    reqs = bundle["requirements.txt"]
    assert "Django" in reqs
    assert "gunicorn" in reqs
    assert "whitenoise" in reqs
    assert "pillow" in reqs
    assert "psycopg2-binary" in reqs


def test_generate_multiservice_distinct_ports():
    from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager

    analysis = ProjectAnalysis(
        repo_name="two-services",
        is_monorepo=True,
        services=[
            ServiceMetadata(
                name="service1",
                path="service1",
                framework=FrameworkType.FASTAPI,
                language=Language.PYTHON,
                package_manager=PackageManager.PIP,
                default_port=8000,
            ),
            ServiceMetadata(
                name="service2",
                path="service2",
                framework=FrameworkType.FASTAPI,
                language=Language.PYTHON,
                package_manager=PackageManager.PIP,
                default_port=8000,
            ),
        ]
    )
    bundle = generate_devops_bundle(analysis)
    compose = bundle["docker-compose.yml"]

    # Ports must not collide
    assert '"8000:8000"' in compose
    assert '"8001:8000"' in compose
