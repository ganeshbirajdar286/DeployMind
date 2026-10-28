import json
import os
import re
from pathlib import Path
from typing import Optional, Tuple
from .models import ServiceMetadata, FrameworkType, Language, PackageManager


def detect_node_service(directory: Path, service_name: str = "web") -> Optional[ServiceMetadata]:
    pkg_json_path = directory / "package.json"
    if not pkg_json_path.exists():
        return None

    try:
        with open(pkg_json_path, "r", encoding="utf-8") as f:
            pkg_data = json.load(f)
    except Exception:
        return None

    deps = pkg_data.get("dependencies", {})
    dev_deps = pkg_data.get("devDependencies", {})
    all_deps = {**deps, **dev_deps}
    scripts = pkg_data.get("scripts", {})

    # 1. Detect Package Manager
    pkg_manager = PackageManager.NPM
    if (directory / "pnpm-lock.yaml").exists():
        pkg_manager = PackageManager.PNPM
    elif (directory / "yarn.lock").exists():
        pkg_manager = PackageManager.YARN
    elif (directory / "bun.lockb").exists() or (directory / "bun.lock").exists():
        pkg_manager = PackageManager.BUN
    elif (directory / "package-lock.json").exists():
        pkg_manager = PackageManager.NPM
    elif "packageManager" in pkg_data:
        pm_str = pkg_data["packageManager"].lower()
        if "pnpm" in pm_str:
            pkg_manager = PackageManager.PNPM
        elif "yarn" in pm_str:
            pkg_manager = PackageManager.YARN
        elif "bun" in pm_str:
            pkg_manager = PackageManager.BUN

    # 2. Detect Language
    is_ts = (
        (directory / "tsconfig.json").exists()
        or "typescript" in all_deps
        or any(directory.glob("src/**/*.ts"))
        or any(directory.glob("src/**/*.tsx"))
    )
    language = Language.TYPESCRIPT if is_ts else Language.JAVASCRIPT

    # 3. Detect Framework & Defaults
    framework = FrameworkType.NODE_GENERIC
    default_port = 3000
    output_dir = "dist"
    build_cmd = None
    start_cmd = None
    health_check_path = "/"

    # Run command prefix (e.g. npm run, pnpm, yarn)
    run_prefix = "npm run"
    if pkg_manager == PackageManager.PNPM:
        run_prefix = "pnpm"
    elif pkg_manager == PackageManager.YARN:
        run_prefix = "yarn"
    elif pkg_manager == PackageManager.BUN:
        run_prefix = "bun run"

    if "next" in all_deps:
        framework = FrameworkType.NEXTJS
        default_port = 3000
        output_dir = ".next"
        build_cmd = f"{run_prefix} build" if "build" in scripts else "npx next build"
        start_cmd = "npx next start" if "start" not in scripts else f"{run_prefix} start"
    elif "vite" in all_deps:
        if "vue" in all_deps:
            framework = FrameworkType.VUE_VITE
        else:
            framework = FrameworkType.REACT_VITE
        default_port = 80  # production Nginx static container
        output_dir = "dist"
        build_cmd = f"{run_prefix} build" if "build" in scripts else "npx vite build"
        start_cmd = "nginx -g 'daemon off;'"  # Served via Nginx in Docker
    elif "react-scripts" in all_deps:
        framework = FrameworkType.REACT_CRA
        default_port = 80
        output_dir = "build"
        build_cmd = f"{run_prefix} build" if "build" in scripts else "npx react-scripts build"
        start_cmd = "nginx -g 'daemon off;'"
    elif "@nestjs/core" in all_deps:
        framework = FrameworkType.NESTJS
        default_port = 3000
        output_dir = "dist"
        build_cmd = f"{run_prefix} build" if "build" in scripts else "nest build"
        start_cmd = f"{run_prefix} start:prod" if "start:prod" in scripts else "node dist/main"
        health_check_path = "/health"
    elif "express" in all_deps:
        framework = FrameworkType.EXPRESS
        default_port = 3000
        output_dir = "dist" if is_ts else None
        if is_ts and "build" in scripts:
            build_cmd = f"{run_prefix} build"
            start_cmd = f"{run_prefix} start" if "start" in scripts else "node dist/index.js"
        elif "start" in scripts:
            start_cmd = f"{run_prefix} start"
        else:
            main_file = pkg_data.get("main", "index.js")
            start_cmd = f"node {main_file}"
        health_check_path = "/health"

    # Scan for custom port in entry files
    custom_port = scan_for_port(directory)
    if custom_port:
        default_port = custom_port

    test_cmd = None
    if "test" in scripts and "no test specified" not in scripts["test"]:
        test_cmd = f"{run_prefix} test"

    return ServiceMetadata(
        name=service_name,
        path=str(directory),
        framework=framework,
        language=language,
        package_manager=pkg_manager,
        runtime_version="20",  # Default stable LTS
        build_command=build_cmd,
        start_command=start_cmd,
        test_command=test_cmd,
        default_port=default_port,
        health_check_path=health_check_path,
        output_dir=output_dir,
        has_existing_dockerfile=(directory / "Dockerfile").exists(),
        has_existing_compose=(directory / "docker-compose.yml").exists() or (directory / "compose.yaml").exists(),
        has_existing_ci=(directory / ".github" / "workflows").exists(),
        details={
            "package_name": pkg_data.get("name", service_name),
            "version": pkg_data.get("version", "0.0.1"),
            "scripts": list(scripts.keys()),
        }
    )


def scan_for_port(directory: Path) -> Optional[int]:
    """Scan source files for explicit port declarations e.g. listen(3000) or PORT = 5000"""
    extensions = {".js", ".ts", ".jsx", ".tsx"}
    candidates = [
        f for f in directory.rglob("*")
        if f.is_file() and f.suffix in extensions and "node_modules" not in str(f)
    ]
    port_patterns = [
        re.compile(r'app\.listen\(\s*(?:process\.env\.PORT\s*\|\|\s*)?(\d{4,5})\s*[\),]', re.IGNORECASE),
        re.compile(r'PORT\s*[:=]\s*(?:process\.env\.PORT\s*\|\|\s*)?(\d{4,5})', re.IGNORECASE),
        re.compile(r'port:\s*(\d{4,5})', re.IGNORECASE),
    ]

    for file_path in candidates[:20]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for pattern in port_patterns:
                match = pattern.search(content)
                if match:
                    port = int(match.group(1))
                    if 1024 <= port <= 65535:
                        return port
        except Exception:
            continue
    return None
