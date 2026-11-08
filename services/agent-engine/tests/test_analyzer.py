from pathlib import Path
from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.analyzer.models import FrameworkType, Language, PackageManager

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
    from ai_devops_agent.analyzer.ast_scanner import scan_python_imports
    from ai_devops_agent.analyzer.python_detector import detect_python_service

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


def test_detect_client_backend_monorepo(tmp_path):
    # Setup client subdirectory (React/Vite)
    client_dir = tmp_path / "client"
    client_dir.mkdir()
    (client_dir / "package.json").write_text('{"name": "my-client", "dependencies": {"vite": "^5.0.0", "react": "^18.0.0"}}', encoding="utf-8")

    # Setup backend subdirectory (Express) with .env.example
    backend_dir = tmp_path / "backend"
    backend_dir.mkdir()
    (backend_dir / "package.json").write_text('{"name": "my-backend", "dependencies": {"express": "^4.18.0"}}', encoding="utf-8")
    (backend_dir / ".env.example").write_text("MOGODB_URL=\nPORT=5000\nJWT_SECRET=\n", encoding="utf-8")

    analysis = analyze_repository(tmp_path)

    assert analysis.is_monorepo is True
    assert len(analysis.services) == 2

    service_names = {s.name for s in analysis.services}
    assert "client" in service_names
    assert "backend" in service_names

    backend_svc = next(s for s in analysis.services if s.name == "backend")
    assert "MOGODB_URL" in backend_svc.env_vars_detected
    assert "JWT_SECRET" in backend_svc.env_vars_detected
    assert analysis.suggested_deployment_targets["client"] == "vercel"
    assert analysis.suggested_deployment_targets["backend"] == "render"


def test_detect_django_monolith_with_internal_apps(tmp_path):
    # Setup root manage.py
    (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport os\n", encoding="utf-8")

    # Setup inner project folder with wsgi.py and settings.py
    inner_dir = tmp_path / "myproject"
    inner_dir.mkdir()
    (inner_dir / "wsgi.py").write_text("application = None\n", encoding="utf-8")
    (inner_dir / "settings.py").write_text("DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}\n", encoding="utf-8")

    # Setup Django apps as subdirectories with apps.py and models.py
    for app_name in ["accounts", "listings", "contacts"]:
        app_dir = tmp_path / app_name
        app_dir.mkdir()
        (app_dir / "apps.py").write_text(f"class {app_name.capitalize()}Config:\n    pass\n", encoding="utf-8")
        (app_dir / "models.py").write_text(
            f"from django.db import models\nimport contacts\nphoto = models.ImageField()\n",
            encoding="utf-8"
        )

    analysis = analyze_repository(tmp_path)

    assert analysis.is_monorepo is False
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.DJANGO
    assert svc.path == "."
    assert "gunicorn myproject.wsgi:application" in svc.start_command
    # Internal apps must NOT be treated as third-party PyPI packages
    assert "accounts" not in svc.extra_pypi_packages
    assert "contacts" not in svc.extra_pypi_packages
    assert "listings" not in svc.extra_pypi_packages
    # Pillow must be detected from ImageField
    assert "pillow" in svc.extra_pypi_packages
    # PostgreSQL driver must be detected
    assert "psycopg2-binary" in svc.extra_pypi_packages
