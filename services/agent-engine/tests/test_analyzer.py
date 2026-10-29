from pathlib import Path
from analyzer.detector import analyze_repository
from analyzer.models import FrameworkType, Language, PackageManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_detect_react_vite():
    target = FIXTURES_DIR / "sample-react-vite"
    analysis = analyze_repository(target)

    assert analysis.repo_name == "sample-react-vite"
    assert not analysis.is_monorepo
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.REACT_VITE
    assert svc.language == Language.TYPESCRIPT
    assert svc.package_manager == PackageManager.NPM
    assert svc.default_port == 80
    assert analysis.suggested_deployment_targets[svc.name] == "vercel"


def test_detect_fastapi():
    target = FIXTURES_DIR / "sample-fastapi"
    analysis = analyze_repository(target)

    assert analysis.repo_name == "sample-fastapi"
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.FASTAPI
    assert svc.language == Language.PYTHON
    assert svc.default_port == 8000
    assert "uvicorn main:app" in svc.start_command
    assert analysis.suggested_deployment_targets[svc.name] == "render"


def test_detect_express():
    target = FIXTURES_DIR / "sample-express"
    analysis = analyze_repository(target)

    assert analysis.repo_name == "sample-express"
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.EXPRESS
    assert svc.language == Language.TYPESCRIPT
    assert svc.default_port == 4000  # scanned from process.env.PORT || 4000
    assert analysis.suggested_deployment_targets[svc.name] == "render"


def test_ast_scanner_and_system_deps(tmp_path):
    from analyzer.ast_scanner import scan_python_imports
    from analyzer.python_detector import detect_python_service

    # Create Python script importing third-party modules and stdlib
    py_code = """import os
import sys
import whisper
from yt_dlp import YoutubeDL
import pydub
"""
    (tmp_path / "main.py").write_text(py_code, encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pydub==0.25.1\n", encoding="utf-8")

    # 1. Direct AST scan
    pypi_pkgs, system_deps = scan_python_imports(tmp_path)
    assert "openai-whisper" in pypi_pkgs
    assert "yt-dlp" in pypi_pkgs
    assert "pydub" in pypi_pkgs
    assert "ffmpeg" in system_deps
    assert "os" not in pypi_pkgs
    assert "sys" not in pypi_pkgs

    # 2. Python detector integration: pydub was in requirements.txt, so whisper & yt-dlp are unlisted
    svc = detect_python_service(tmp_path)
    assert svc is not None
    assert "openai-whisper" in svc.extra_pypi_packages
    assert "yt-dlp" in svc.extra_pypi_packages
    assert "pydub" not in svc.extra_pypi_packages  # already in requirements.txt!
    assert "ffmpeg" in svc.system_packages
