from pathlib import Path
from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_generate_react_vite_bundle():
    analysis = analyze_repository(FIXTURES_DIR / "sample-react-vite")
    bundle = generate_devops_bundle(analysis)

    assert "Dockerfile" in bundle
    assert ".dockerignore" in bundle
    assert "docker-compose.yml" in bundle
    assert ".github/workflows/ci.yml" in bundle
    assert "DEPLOYMENT.md" in bundle

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
    from analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager

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
