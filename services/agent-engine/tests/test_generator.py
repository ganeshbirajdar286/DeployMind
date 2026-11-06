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
