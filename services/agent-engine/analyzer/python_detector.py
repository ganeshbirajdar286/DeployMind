import os
import re
from pathlib import Path
from typing import Optional, List
from .models import ServiceMetadata, FrameworkType, Language, PackageManager
from .ast_scanner import scan_python_imports


def detect_python_service(directory: Path, service_name: str = "api") -> Optional[ServiceMetadata]:
    has_pyproject = (directory / "pyproject.toml").exists()
    has_requirements = (directory / "requirements.txt").exists()
    has_pipfile = (directory / "Pipfile").exists()
    has_setup = (directory / "setup.py").exists()

    if not (has_pyproject or has_requirements or has_pipfile or has_setup):
        # Also check if there's any .py file directly in directory
        py_files = list(directory.glob("*.py"))
        if not py_files:
            return None

    # Read all dependency texts
    dep_text = ""
    if has_requirements:
        try:
            dep_text += (directory / "requirements.txt").read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            pass
    if has_pyproject:
        try:
            dep_text += (directory / "pyproject.toml").read_text(encoding="utf-8", errors="ignore").lower()
        except Exception:
            pass

    # 1. Package Manager Detection
    pkg_manager = PackageManager.PIP
    if (directory / "uv.lock").exists() or "tool.uv" in dep_text:
        pkg_manager = PackageManager.UV
    elif (directory / "poetry.lock").exists() or "tool.poetry" in dep_text:
        pkg_manager = PackageManager.POETRY
    elif has_pipfile:
        pkg_manager = PackageManager.PIP
    elif has_requirements:
        pkg_manager = PackageManager.PIP

    # 2. Framework Detection & Entry Point
    framework = FrameworkType.PYTHON_GENERIC
    default_port = 8000
    start_cmd = None
    health_check_path = "/"

    # Entry point discovery
    entry_file = None
    for candidate in ["main.py", "app.py", "server.py", "api.py", "manage.py"]:
        if (directory / candidate).exists():
            entry_file = candidate
            break

    module_name = entry_file.replace(".py", "") if entry_file else "main"

    # AST Deep Scan for missing/unlisted imports & system dependencies (e.g. ffmpeg)
    ast_pypi_pkgs, ast_system_deps = scan_python_imports(directory)

    # Normalize dep_text to find which packages are already listed
    dep_words = set(re.findall(r"[a-z0-9_\-]+", dep_text.lower()))

    unlisted_pypi = []
    for pkg in sorted(ast_pypi_pkgs):
        pkg_norm = pkg.lower()
        pkg_alt = pkg_norm.replace("-", "_")
        if pkg_norm not in dep_words and pkg_alt not in dep_words:
            unlisted_pypi.append(pkg)

    system_packages = sorted(list(ast_system_deps))

    if "fastapi" in dep_text or "fastapi" in ast_pypi_pkgs:
        framework = FrameworkType.FASTAPI
        default_port = 8000
        health_check_path = "/docs"
        start_cmd = f"uvicorn {module_name}:app --host 0.0.0.0 --port 8000"
    elif "flask" in dep_text or "flask" in ast_pypi_pkgs:
        framework = FrameworkType.FLASK
        default_port = 5000
        health_check_path = "/"
        start_cmd = f"gunicorn --bind 0.0.0.0:5000 {module_name}:app"
    elif "django" in dep_text or "django" in ast_pypi_pkgs or (directory / "manage.py").exists():
        framework = FrameworkType.DJANGO
        default_port = 8000
        health_check_path = "/"
        # Look for wsgi.py
        wsgi_files = list(directory.glob("*/wsgi.py"))
        if wsgi_files:
            app_dir = wsgi_files[0].parent.name
            start_cmd = f"gunicorn {app_dir}.wsgi:application --bind 0.0.0.0:8000"
        else:
            start_cmd = "python manage.py runserver 0.0.0.0:8000"
    else:
        if entry_file:
            start_cmd = f"python {entry_file}"

    # 3. Detect Python Version
    py_version = "3.11"  # Default stable LTS
    py_version_file = directory / ".python-version"
    if py_version_file.exists():
        try:
            ver = py_version_file.read_text(encoding="utf-8").strip()
            if ver:
                py_version = ver
        except Exception:
            pass

    test_cmd = None
    if (directory / "tests").exists() or "pytest" in dep_text:
        test_cmd = "pytest"

    return ServiceMetadata(
        name=service_name,
        path=str(directory),
        framework=framework,
        language=Language.PYTHON,
        package_manager=pkg_manager,
        runtime_version=py_version,
        build_command=None,
        start_command=start_cmd,
        test_command=test_cmd,
        default_port=default_port,
        health_check_path=health_check_path,
        extra_pypi_packages=unlisted_pypi,
        system_packages=system_packages,
        has_existing_dockerfile=(directory / "Dockerfile").exists(),
        has_existing_compose=(directory / "docker-compose.yml").exists() or (directory / "compose.yaml").exists(),
        has_existing_ci=(directory / ".github" / "workflows").exists(),
        details={
            "has_pyproject": has_pyproject,
            "has_requirements": has_requirements,
            "entry_file": entry_file,
            "extra_pypi_packages": unlisted_pypi,
            "system_packages": system_packages,
        }
    )
