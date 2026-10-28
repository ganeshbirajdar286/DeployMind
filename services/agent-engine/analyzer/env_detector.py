import os
import re
from pathlib import Path
from typing import List, Set

IGNORE_VARS = {
    "NODE_ENV", "PATH", "PWD", "HOME", "USER", "SHELL",
    "PYTHONPATH", "PORT", "HOSTNAME", "TERM"
}

ENV_PATTERNS = [
    # Node process.env.MY_VAR
    re.compile(r'process\.env\.([A-Z0-9_]{2,})'),
    # Vite / client import.meta.env.VITE_MY_VAR
    re.compile(r'import\.meta\.env\.([A-Z0-9_]{2,})'),
    # Python os.environ.get("MY_VAR") or os.environ["MY_VAR"]
    re.compile(r'os\.environ(?:\[|\.get\()\s*[\'"]([A-Z0-9_]{2,})[\'"]'),
    # Python getenv("MY_VAR")
    re.compile(r'getenv\(\s*[\'"]([A-Z0-9_]{2,})[\'"]'),
    # decouple / environs config("MY_VAR")
    re.compile(r'config\(\s*[\'"]([A-Z0-9_]{2,})[\'"]'),
]


def detect_environment_variables(directory: Path) -> List[str]:
    detected: Set[str] = set()

    # 1. Check for .env.example / .env.sample / .env.template
    for sample_name in [".env.example", ".env.sample", ".env.template", ".env.defaults"]:
        sample_path = directory / sample_name
        if sample_path.exists():
            try:
                for line in sample_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        var_name = line.split("=", 1)[0].strip()
                        if var_name and var_name not in IGNORE_VARS:
                            detected.add(var_name)
            except Exception:
                pass

    extensions = {".js", ".ts", ".jsx", ".tsx", ".py"}
    candidate_files = [
        f for f in directory.rglob("*")
        if f.is_file() and f.suffix in extensions
        and not any(ignored in str(f) for ignored in ["node_modules", ".venv", "dist", "build", ".next", ".git"])
    ]

    # Limit to maximum 30 files for instantaneous performance
    for file_path in candidate_files[:30]:
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for pattern in ENV_PATTERNS:
                for match in pattern.findall(content):
                    if match not in IGNORE_VARS:
                        detected.add(match)
        except Exception:
            continue

    return sorted(list(detected))
