import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List

from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.generator.engine import generate_devops_bundle
from ai_devops_agent.runner.secrets_manager import GitHubSecretsManager
from ai_devops_agent.runner.git_pusher import GitPusher
from ai_devops_agent.runner.github_runner import GitHubActionsRunner
from ai_devops_agent.utils.diff_viewer import display_bundle_diffs, format_colored_diff
from ai_devops_agent.utils.sanitizer import (
    redact_secrets,
    sanitize_url_for_logging,
    sanitize_app_name,
    derive_default_app_name,
)
from ai_devops_agent.utils.theme import (
    print_powerline_header,
    print_step_box,
    print_box,
    print_success_box,
    print_alert_box,
    prompt_choice,
    CYAN,
    PINK,
    PURPLE,
    INDIGO,
    EMERALD,
    AMBER,
    RED,
    WHITE,
    MUTED,
    BOLD,
    DIM,
    RESET,
)
from .patcher import diagnose_and_patch


def parse_github_slug(target: str) -> Optional[str]:
    """Extracts 'owner/repo' from full URL or slug."""
    target = target.strip()
    # https://github.com/owner/repo or git@github.com:owner/repo
    m = re.search(r"github\.com[/:]([a-zA-Z0-9_-]+)/([a-zA-Z0-9_.-]+)", target)
    if m:
        repo = m.group(2)
        if repo.endswith(".git"):
            repo = repo[:-4]
        return f"{m.group(1)}/{repo}"

    # If already in format 'owner/repo'
    if "/" in target and not Path(target).exists():
        parts = target.split("/")
        if len(parts) == 2 and not target.startswith("."):
            return target

    return None


def open_file_in_editor(file_path: Path) -> bool:
    """Opens a file in the user's preferred editor, waiting for the process to exit."""
    editors = []
    if os.getenv("EDITOR"):
        editors.append(os.getenv("EDITOR"))
    if os.getenv("VISUAL"):
        editors.append(os.getenv("VISUAL"))

    if sys.platform == "win32":
        if shutil.which("code"):
            editors.append("code --wait")
        editors.append("notepad.exe")
    else:
        for ed in ["nano", "vim", "vi"]:
            if shutil.which(ed):
                editors.append(ed)
                break

    for ed_cmd in editors:
        try:
            parts = ed_cmd.split() + [str(file_path)]
            res = subprocess.run(parts, shell=False)
            if res.returncode == 0:
                return True
        except Exception:
            continue

    if sys.platform == "win32":
        try:
            os.startfile(str(file_path))
            input(f"\n[Editor] Opened '{file_path.name}'. Make your edits, save the file, and press [Enter] to continue...")
            return True
        except Exception:
            pass

    return False


def interactive_edit_bundle(target_path: Path, artifacts: Dict[str, str]) -> Dict[str, str]:
    """Provides a terminal menu allowing the user to select and edit any generated file in their editor."""
    file_list = list(artifacts.keys())

    while True:
        print("\n" + "=" * 60)
        print("✏️  Interactive DevOps File Editor")
        print("=" * 60)
        print("Select a file to edit in your editor:")
        for idx, rel_path in enumerate(file_list, 1):
            print(f"  [{idx}] {rel_path}")
        print("  [B] Done editing (return to confirmation menu)")

        choice = input(f"\nEnter choice [1-{len(file_list)} or B]: ").strip().lower()
        if choice in ("b", "back", "done", "q", "exit", ""):
            break

        selected_file = None
        if choice.isdigit():
            num = int(choice)
            if 1 <= num <= len(file_list):
                selected_file = file_list[num - 1]
        else:
            for f in file_list:
                if choice in f.lower():
                    selected_file = f
                    break

        if not selected_file:
            print(f"  [!] Invalid choice: '{choice}'. Please select a valid number or 'B'.")
            continue

        disk_path = target_path / selected_file
        disk_path.parent.mkdir(parents=True, exist_ok=True)
        if not disk_path.exists() or disk_path.read_text(encoding="utf-8", errors="ignore") != artifacts[selected_file]:
            disk_path.write_text(artifacts[selected_file], encoding="utf-8")

        print(f"\nLaunching editor for '{selected_file}'...")
        opened = open_file_in_editor(disk_path)
        if opened:
            try:
                updated_content = disk_path.read_text(encoding="utf-8")
                if updated_content != artifacts[selected_file]:
                    artifacts[selected_file] = updated_content
                    print(f"  [OK] Saved modifications to '{selected_file}'!")
                else:
                    print(f"  (No changes detected for '{selected_file}')")
            except Exception as e:
                print(f"  [!] Error reading updated file: {e}")
        else:
            print(f"  [!] Could not open editor automatically. You can edit the file manually at:\n      {disk_path}")
            input("  Press [Enter] after editing the file...")
            try:
                artifacts[selected_file] = disk_path.read_text(encoding="utf-8")
                print(f"  [OK] Loaded modifications for '{selected_file}'!")
            except Exception as e:
                print(f"  [!] Error: {e}")

    return artifacts


def list_interesting_files(target_path: Path, artifacts: Optional[Dict[str, str]] = None) -> List[Path]:
    """Finds interesting configuration, source, and DevOps files in the repository."""
    interesting_names = {
        "dockerfile", "docker-compose.yml", "docker-compose.yaml",
        "requirements.txt", "package.json", "pyproject.toml",
        ".env", "manage.py", "wsgi.py", "asgi.py"
    }
    found = []
    seen_paths = set()

    # Add known generated artifacts
    if artifacts:
        for rel_str in sorted(artifacts.keys()):
            p = target_path / rel_str
            if p.exists() and p not in seen_paths:
                found.append(p)
                seen_paths.add(p)

    # Scan workspace for key files
    for p in target_path.rglob("*"):
        if any(ign in p.parts for ign in [".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"]):
            continue
        if p.is_file():
            name_lower = p.name.lower()
            if (
                name_lower in interesting_names
                or "settings" in name_lower
                or name_lower.endswith((".yml", ".yaml", ".env", ".toml"))
                or p.name in ("Dockerfile", "manage.py")
            ):
                if p not in seen_paths:
                    found.append(p)
                    seen_paths.add(p)

    return sorted(found, key=lambda p: (len(p.relative_to(target_path).parts), str(p.relative_to(target_path))))


def find_target_file(target_path: Path, instruction: str) -> Optional[Path]:
    """Finds target file path matching user instruction based on filename or semantic keywords."""
    inst_lower = instruction.lower()

    # 1. Check for explicit filenames or relative paths in prompt
    tokens = re.findall(r'[a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9]+|Dockerfile|dockerfile', instruction)
    for token in tokens:
        clean_tok = token.strip("./\\").replace("\\", "/")
        direct = target_path / clean_tok
        if direct.exists() and direct.is_file():
            return direct
        fname = Path(clean_tok).name
        matches = [
            p for p in target_path.rglob(fname)
            if not any(ign in p.parts for ign in [".git", "node_modules", "__pycache__", ".venv", "venv"])
        ]
        if matches:
            return sorted(matches, key=lambda p: len(p.parts))[0]

    # 2. Semantic keywords mapping
    if any(w in inst_lower for w in ["setting", "database", "allowed_hosts", "secret_key", "sqlite", "postgres", "db"]):
        settings = [
            p for p in target_path.rglob("*settings*.py")
            if not any(ign in p.parts for ign in [".git", "node_modules", "__pycache__", ".venv", "venv"])
        ]
        if settings:
            return sorted(settings, key=lambda p: len(p.parts))[0]

    if any(w in inst_lower for w in ["dockerfile", "container", "port", "expose", "cmd", "gunicorn", "entrypoint"]):
        dfs = [
            p for p in target_path.rglob("*Dockerfile*")
            if not any(ign in p.parts for ign in [".git", "node_modules", "__pycache__", ".venv", "venv"])
        ]
        if dfs:
            return sorted(dfs, key=lambda p: len(p.parts))[0]

    if any(w in inst_lower for w in ["compose", "service", "volume", "network"]):
        for comp_name in ["docker-compose.yml", "docker-compose.yaml"]:
            cp = target_path / comp_name
            if cp.exists():
                return cp

    if any(w in inst_lower for w in ["requirement", "pip", "package", "depend", "install", "module", "pypi"]):
        reqs = target_path / "requirements.txt"
        if reqs.exists():
            return reqs
        pkg_json = target_path / "package.json"
        if pkg_json.exists():
            return pkg_json

    if any(w in inst_lower for w in [".env", "environment", "secret"]):
        env_f = target_path / ".env"
        if env_f.exists():
            return env_f

    return None


def interactive_select_file(target_path: Path, artifacts: Optional[Dict[str, str]] = None) -> Optional[Path]:
    """Displays a list of editable files and allows user selection."""
    files = list_interesting_files(target_path, artifacts or {})
    if not files:
        print(f"  {AMBER}[!] No configuration files found in workspace.{RESET}")
        return None

    print(f"\n{BOLD}{CYAN}Select a file to inspect or edit:{RESET}")
    for idx, f in enumerate(files, 1):
        rel = f.relative_to(target_path)
        print(f"  [{idx}] {rel}")
    print(f"  [{len(files) + 1}] Enter custom file path")
    print("  [B] Back to main menu")

    choice = input(f"\nEnter choice [1-{len(files)+1} or B]: ").strip().lower()
    if choice in ("b", "back", "q", "exit", ""):
        return None

    if choice.isdigit():
        num = int(choice)
        if 1 <= num <= len(files):
            return files[num - 1]
        elif num == len(files) + 1:
            custom_rel = input("Enter relative file path: ").strip()
            custom_path = target_path / custom_rel
            if custom_path.exists() and custom_path.is_file():
                return custom_path
            print(f"  {RED}[!] File not found: {custom_rel}{RESET}")
            return None

    for f in files:
        if choice in str(f.relative_to(target_path)).lower():
            return f

    print(f"  {AMBER}[!] Unrecognized selection '{choice}'.{RESET}")
    return None


def apply_ai_instruction_to_file(
    file_path: Path,
    instruction: str,
    error_context: str = "",
) -> Tuple[bool, str, str]:
    """
    Applies user instruction to the file using Gemini LLM (if configured) or deterministic DevOps rules.
    Returns: (success: bool, explanation: str, updated_content: str)
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return False, f"Failed to read {file_path.name}: {e}", ""

    inst_lower = instruction.lower()

    # 1. Attempt Generative AI via Gemini API if API key is provided
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if api_key:
        try:
            import json
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = f"""You are an expert AI DevOps and Software Engineer.
Your task is to modify the provided file according to the user's instruction.

File Name: {file_path.name}
Full Path: {file_path}

=== USER INSTRUCTION ===
{instruction}

=== RECENT RUNTIME / BUILD ERROR CONTEXT (IF RELEVANT) ===
{error_context[-2000:] if error_context else 'None'}

=== CURRENT FILE CONTENT ===
{content}

Instructions:
1. Apply the user's requested modifications accurately.
2. Preserve all existing functionality, comments, and structure not related to the requested change.
3. If modifying Python code, ensure valid syntax and appropriate imports (e.g. 'import os').
4. Return ONLY a valid JSON object in this exact schema:
{{
  "explanation": "Brief description of changes made",
  "updated_content": "The full revised file content"
}}
"""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            data = json.loads(response.text)
            updated = data.get("updated_content", "")
            explanation = data.get("explanation", "Applied AI modifications.")
            if updated and updated.strip() != content.strip():
                return True, explanation, updated
        except Exception:
            pass

    # 2. Robust Deterministic DevOps Rule Engine Fallback
    updated = content
    explanations = []

    # Target: Django settings.py
    if file_path.name.endswith(".py") and ("settings" in file_path.name or "settings" in str(file_path)):
        # ALLOWED_HOSTS
        if "allowed_hosts" in inst_lower or "allowed host" in inst_lower or ("host" in inst_lower and "allow" in inst_lower):
            if re.search(r"ALLOWED_HOSTS\s*=\s*\[.*?\]", updated, flags=re.DOTALL):
                updated = re.sub(
                    r"ALLOWED_HOSTS\s*=\s*\[.*?\]",
                    "ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',') if os.environ.get('ALLOWED_HOSTS') else ['*']",
                    updated,
                    flags=re.DOTALL,
                )
                if "import os" not in updated:
                    updated = "import os\n" + updated
                explanations.append("Configured ALLOWED_HOSTS to allow all hosts and read from environment.")

        # DATABASES / SQLite Fallback
        if any(w in inst_lower for w in ["sqlite", "database", "postgres", "db", "fallback", "connection"]):
            db_block = (
                "# Database configuration with dynamic environment & SQLite fallback\n"
                "DB_HOST = os.environ.get('DB_HOST', 'db')\n"
                "DB_NAME = os.environ.get('DB_NAME', 'postgres')\n"
                "DB_USER = os.environ.get('DB_USER', 'postgres')\n"
                "DB_PASSWORD = os.environ.get('DB_PASSWORD', '1234')\n"
                "DB_PORT = os.environ.get('DB_PORT', '5432')\n\n"
                "if os.environ.get('USE_SQLITE', 'false').lower() in ('true', '1') or not DB_HOST:\n"
                "    DATABASES = {\n"
                "        'default': {\n"
                "            'ENGINE': 'django.db.backends.sqlite3',\n"
                "            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),\n"
                "        }\n"
                "    }\n"
                "else:\n"
                "    DATABASES = {\n"
                "        'default': {\n"
                "            'ENGINE': 'django.db.backends.postgresql',\n"
                "            'NAME': DB_NAME,\n"
                "            'USER': DB_USER,\n"
                "            'PASSWORD': DB_PASSWORD,\n"
                "            'HOST': DB_HOST,\n"
                "            'PORT': DB_PORT,\n"
                "        }\n"
                "    }"
            )
            if re.search(r"DATABASES\s*=\s*\{.*?\n\}", updated, flags=re.DOTALL):
                updated = re.sub(r"DATABASES\s*=\s*\{.*?\n\}", db_block, updated, flags=re.DOTALL)
                if "import os" not in updated:
                    updated = "import os\n" + updated
                explanations.append("Updated DATABASES with dynamic environment parameters and SQLite fallback.")

        # DEBUG
        if "debug" in inst_lower:
            if re.search(r"DEBUG\s*=\s*(?:True|False)", updated):
                updated = re.sub(r"DEBUG\s*=\s*(?:True|False)", "DEBUG = os.environ.get('DEBUG', 'True').lower() in ('true', '1')", updated)
                explanations.append("Set DEBUG to be driven by environment variable with True default.")

    # Target: requirements.txt
    elif file_path.name == "requirements.txt":
        packages_to_check = {
            "pillow": "Pillow>=9.0.0",
            "psycopg2": "psycopg2-binary>=2.9.9",
            "psycopg2-binary": "psycopg2-binary>=2.9.9",
            "gunicorn": "gunicorn>=21.2.0",
            "whitenoise": "whitenoise>=6.6.0",
            "django": "django>=4.2.0",
        }
        added = []
        for pkg_key, pkg_spec in packages_to_check.items():
            if pkg_key in inst_lower:
                base_name = pkg_spec.split(">=")[0]
                if base_name.lower() not in updated.lower():
                    if updated and not updated.endswith("\n"):
                        updated += "\n"
                    updated += f"{pkg_spec}\n"
                    added.append(pkg_spec)
        if added:
            explanations.append(f"Added {', '.join(added)} to requirements.txt.")

    # Target: Dockerfile
    elif "dockerfile" in file_path.name.lower():
        # Port change
        if any(w in inst_lower for w in ["port", "expose"]):
            port_match = re.search(r"\b(\d{2,5})\b", inst_lower)
            if port_match:
                new_port = port_match.group(1)
                updated = re.sub(r"EXPOSE\s+\d+", f"EXPOSE {new_port}", updated)
                updated = re.sub(r"ENV PORT=\d+", f"ENV PORT={new_port}", updated)
                explanations.append(f"Updated exposed port to {new_port}.")

        # Migrations
        if "migrate" in inst_lower or "migration" in inst_lower:
            if "migrate" not in updated:
                updated = re.sub(
                    r'CMD\s*\[\s*"gunicorn"',
                    'CMD ["sh", "-c", "python manage.py migrate --noinput || true; exec gunicorn"',
                    updated,
                )
                explanations.append("Configured database migration execution before application boot.")

    # Target: docker-compose.yml
    elif "docker-compose" in file_path.name.lower():
        if any(w in inst_lower for w in ["postgres", "database", "db"]):
            if "image: postgres" not in updated and "services:\n" in updated:
                pg_block = (
                    "  db:\n"
                    "    image: postgres:15-alpine\n"
                    "    container_name: app-db\n"
                    "    restart: unless-stopped\n"
                    "    environment:\n"
                    "      POSTGRES_DB: postgres\n"
                    "      POSTGRES_USER: postgres\n"
                    "      POSTGRES_PASSWORD: \"1234\"\n"
                    "    ports:\n"
                    "      - \"5432:5432\"\n"
                    "    healthcheck:\n"
                    "      test: [\"CMD-SHELL\", \"pg_isready -U postgres\"]\n"
                    "      interval: 3s\n"
                    "      timeout: 3s\n"
                    "      retries: 5\n\n"
                )
                updated = updated.replace("services:\n", f"services:\n{pg_block}", 1)
                explanations.append("Added PostgreSQL database service to docker-compose.yml.")

    if updated.strip() != content.strip():
        explanation_text = " ".join(explanations) if explanations else "Applied changes based on instruction."
        return True, explanation_text, updated

    return False, "Could not apply automated rule without Gemini API key.", content


@dataclass
class CodebaseAlignmentResult:
    diagnosis: str
    misalignments: List[str]
    file_changes: Dict[str, str]  # rel_path -> new_content
    explanations: Dict[str, str]  # rel_path -> explanation


def align_project_codebase(
    target_path: Path,
    analysis: Any,
    error_context: str = "",
    artifacts: Optional[Dict[str, str]] = None,
    primary_port: int = 8000,
    primary_health_path: str = "/",
) -> CodebaseAlignmentResult:
    """
    Replit-style whole-codebase alignment engine.
    Scans and cross-analyzes all configuration and source files across the project
    to resolve discrepancies between application code, container settings, ports, and database environment.
    Supports Express.js / Node.js, Django / Python, and generic containerized web architectures.
    """
    import json
    artifacts = artifacts or {}
    file_changes: Dict[str, str] = {}
    explanations: Dict[str, str] = {}
    misalignments: List[str] = []

    err_lower = error_context.lower()
    is_db_refused = any(tok in err_lower for tok in [
        "connection refused", "could not connect to server", "operationalerror",
        "connection to server at \"localhost\"", "http 500"
    ])

    framework = analysis.services[0].framework.value if (analysis and analysis.services) else ""
    is_django = "django" in framework.lower() or any(target_path.rglob("manage.py"))
    is_node = (
        any(k in framework.lower() for k in ["express", "node", "nest", "fastify", "koa", "next"])
        or (target_path / "package.json").exists()
        or any(target_path.rglob("package.json"))
    )

    app_port = 8000
    if analysis and analysis.services and analysis.services[0].default_port:
        app_port = analysis.services[0].default_port
    elif is_node:
        app_port = 3000
    elif is_django:
        app_port = 8000

    health_port = primary_port or app_port

    # 1. Inspect and Align Django settings.py
    if is_django:
        settings_files = [
            p for p in target_path.rglob("*settings*.py")
            if not any(ign in p.parts for ign in [".git", "node_modules", "__pycache__", ".venv", "venv"])
        ]
        if settings_files:
            settings_file = sorted(settings_files, key=lambda p: len(p.parts))[0]
            rel_settings = str(settings_file.relative_to(target_path)).replace("\\", "/")
            try:
                content = settings_file.read_text(encoding="utf-8", errors="ignore")
                updated = content
                changed = False

                # Check ALLOWED_HOSTS
                if "ALLOWED_HOSTS = []" in updated or "ALLOWED_HOSTS=[]" in updated or "ALLOWED_HOSTS = ['localhost']" in updated:
                    updated = re.sub(
                        r"ALLOWED_HOSTS\s*=\s*\[.*?\]",
                        "ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',') if os.environ.get('ALLOWED_HOSTS') else ['*']",
                        updated,
                        flags=re.DOTALL,
                    )
                    if "import os" not in updated:
                        updated = "import os\n" + updated
                    changed = True
                    misalignments.append(f"{rel_settings}: ALLOWED_HOSTS was empty or restricted to localhost.")
                    explanations[rel_settings] = "Configured ALLOWED_HOSTS = ['*'] for container networking."

                # Check DATABASES
                has_localhost_db = "'HOST': 'localhost'" in updated or '"HOST": "localhost"' in updated or "'HOST': ''" in updated
                has_no_sqlite_fallback = "USE_SQLITE" not in updated and "sqlite3" not in updated
                if is_db_refused or has_localhost_db or has_no_sqlite_fallback:
                    db_block = (
                        "# Database configuration with dynamic environment & SQLite fallback\n"
                        "DB_HOST = os.environ.get('DB_HOST', 'db')\n"
                        "DB_NAME = os.environ.get('DB_NAME', 'postgres')\n"
                        "DB_USER = os.environ.get('DB_USER', 'postgres')\n"
                        "DB_PASSWORD = os.environ.get('DB_PASSWORD', '1234')\n"
                        "DB_PORT = os.environ.get('DB_PORT', '5432')\n\n"
                        "if os.environ.get('USE_SQLITE', 'false').lower() in ('true', '1') or not DB_HOST:\n"
                        "    DATABASES = {\n"
                        "        'default': {\n"
                        "            'ENGINE': 'django.db.backends.sqlite3',\n"
                        "            'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),\n"
                        "        }\n"
                        "    }\n"
                        "else:\n"
                        "    DATABASES = {\n"
                        "        'default': {\n"
                        "            'ENGINE': 'django.db.backends.postgresql',\n"
                        "            'NAME': DB_NAME,\n"
                        "            'USER': DB_USER,\n"
                        "            'PASSWORD': DB_PASSWORD,\n"
                        "            'HOST': DB_HOST,\n"
                        "            'PORT': DB_PORT,\n"
                        "        }\n"
                        "    }"
                    )
                    if re.search(r"DATABASES\s*=\s*\{.*?\n\}", updated, flags=re.DOTALL):
                        updated = re.sub(r"DATABASES\s*=\s*\{.*?\n\}", db_block, updated, flags=re.DOTALL)
                        if "import os" not in updated:
                            updated = "import os\n" + updated
                        changed = True
                        misalignments.append(f"{rel_settings}: Database 'HOST' was hardcoded to 'localhost' with no SQLite fallback.")
                        prev_exp = explanations.get(rel_settings, "")
                        explanations[rel_settings] = (prev_exp + " " if prev_exp else "") + "Updated DATABASES with dynamic DB_HOST and SQLite fallback."

                if changed and updated != content:
                    file_changes[rel_settings] = updated
            except Exception:
                pass

    # 2. Inspect and Align Node.js / Express Codebase Files
    if is_node:
        # Check package.json for start script
        pkg_file = target_path / "package.json"
        if pkg_file.exists():
            try:
                pkg_data = json.loads(pkg_file.read_text(encoding="utf-8", errors="ignore"))
                scripts = pkg_data.get("scripts", {})
                if not scripts or "start" not in scripts:
                    entry_f = pkg_data.get("main")
                    if not entry_f:
                        for cand in ["server.js", "app.js", "index.js", "src/server.js", "src/index.js", "src/app.js"]:
                            if (target_path / cand).exists():
                                entry_f = cand
                                break
                    entry_f = entry_f or "server.js"
                    if "scripts" not in pkg_data:
                        pkg_data["scripts"] = {}
                    pkg_data["scripts"]["start"] = f"node {entry_f}"
                    file_changes["package.json"] = json.dumps(pkg_data, indent=2) + "\n"
                    misalignments.append("package.json: Missing 'start' script for Docker container startup.")
                    explanations["package.json"] = f"Configured 'start': 'node {entry_f}' in package.json."
            except Exception:
                pass

        # Check server files for 0.0.0.0 host binding & dynamic port
        server_candidates = [
            p for p in target_path.rglob("*.js")
            if not any(ign in p.parts for ign in [".git", "node_modules", "dist", "build"])
            and p.name in ("server.js", "app.js", "index.js", "main.js")
        ]
        for s_file in server_candidates:
            rel_s = str(s_file.relative_to(target_path)).replace("\\", "/")
            try:
                s_content = s_file.read_text(encoding="utf-8", errors="ignore")
                s_updated = s_content
                s_changed = False

                # Ensure 0.0.0.0 host binding instead of localhost / 127.0.0.1
                if ("localhost" in s_updated or "127.0.0.1" in s_updated) and "listen" in s_updated:
                    if re.search(r"listen\(([^,\)]+),\s*['\"](?:localhost|127\.0\.0\.1)['\"]", s_updated):
                        s_updated = re.sub(
                            r"listen\(([^,\)]+),\s*['\"](?:localhost|127\.0\.0\.1)['\"]",
                            r"listen(\1, '0.0.0.0'",
                            s_updated,
                        )
                        s_changed = True
                        misalignments.append(f"{rel_s}: Server was bound to 'localhost'/'127.0.0.1' which prevents container traffic.")
                        explanations[rel_s] = "Updated server.listen to bind to '0.0.0.0' for container networking."

                # Ensure dynamic process.env.PORT support
                if "process.env.PORT" not in s_updated and "app.listen(" in s_updated:
                    match_p = re.search(r"app\.listen\((\d+)", s_updated)
                    if match_p:
                        hardcoded_p = match_p.group(1)
                        s_updated = s_updated.replace(
                            f"app.listen({hardcoded_p}",
                            f"const PORT = process.env.PORT || {hardcoded_p};\napp.listen(PORT",
                            1,
                        )
                        s_changed = True
                        misalignments.append(f"{rel_s}: Port was hardcoded to {hardcoded_p} without reading process.env.PORT.")
                        prev_e = explanations.get(rel_s, "")
                        explanations[rel_s] = (prev_e + " " if prev_e else "") + f"Added dynamic process.env.PORT fallback to {hardcoded_p}."

                if s_changed and s_updated != s_content:
                    file_changes[rel_s] = s_updated
            except Exception:
                pass

    # 3. Inspect and Align docker-compose.yml
    compose_path = target_path / "docker-compose.yml"
    compose_content = ""
    if compose_path.exists():
        try:
            compose_content = compose_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            compose_content = ""
    elif "docker-compose.yml" in artifacts:
        compose_content = artifacts["docker-compose.yml"]

    if compose_content:
        updated_compose = compose_content
        changed_compose = False

        # Django: PostgreSQL Service Injection
        if is_django and "image: postgres" not in updated_compose and "services:\n" in updated_compose:
            app_repo_name = analysis.repo_name if analysis else "app"
            pg_block = (
                "  db:\n"
                "    image: postgres:15-alpine\n"
                f"    container_name: {app_repo_name}-db\n"
                "    restart: unless-stopped\n"
                "    environment:\n"
                "      POSTGRES_DB: postgres\n"
                "      POSTGRES_USER: postgres\n"
                "      POSTGRES_PASSWORD: \"1234\"\n"
                "    ports:\n"
                "      - \"5432:5432\"\n"
                "    healthcheck:\n"
                "      test: [\"CMD-SHELL\", \"pg_isready -U postgres\"]\n"
                "      interval: 3s\n"
                "      timeout: 3s\n"
                "      retries: 5\n\n"
            )
            updated_compose = updated_compose.replace("services:\n", f"services:\n{pg_block}", 1)
            if "depends_on:" not in updated_compose:
                updated_compose = re.sub(
                    r"(container_name:[^\n]+\n)",
                    r"\1    depends_on:\n      db:\n        condition: service_healthy\n",
                    updated_compose,
                    count=1
                )
            if "DB_HOST" not in updated_compose:
                updated_compose = re.sub(
                    r"(environment:\s*\n)",
                    r"\1      DB_HOST: db\n      DB_PASSWORD: \"1234\"\n",
                    updated_compose,
                    count=1
                )
            changed_compose = True
            misalignments.append("docker-compose.yml: Missing dedicated PostgreSQL ('db') service container.")
            explanations["docker-compose.yml"] = "Provisioned PostgreSQL database container ('db') with healthcheck."

        # Port Alignment: Ensure health_port maps to app_port in compose
        if health_port != app_port:
            target_port_str = f'"{health_port}:{app_port}"'
            mismatch_pattern = rf'["\']?{app_port}:{app_port}["\']?'
            if re.search(mismatch_pattern, updated_compose):
                updated_compose = re.sub(mismatch_pattern, target_port_str, updated_compose)
                changed_compose = True
                misalignments.append(f"docker-compose.yml: Port mapping was '{app_port}:{app_port}' while healthcheck verifies port {health_port}.")
                prev_e = explanations.get("docker-compose.yml", "")
                explanations["docker-compose.yml"] = (prev_e + " " if prev_e else "") + f"Updated port mapping to '{health_port}:{app_port}'."

        if changed_compose and updated_compose != compose_content:
            file_changes["docker-compose.yml"] = updated_compose

    # 4. Inspect and Align Dockerfile
    dockerfile_path = target_path / "Dockerfile"
    dockerfile_content = ""
    if dockerfile_path.exists():
        try:
            dockerfile_content = dockerfile_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            dockerfile_content = ""
    elif "Dockerfile" in artifacts:
        dockerfile_content = artifacts["Dockerfile"]

    if dockerfile_content:
        updated_df = dockerfile_content
        changed_df = False

        # Django: Database migration before gunicorn
        if is_django and "migrate" not in updated_df and 'gunicorn' in updated_df:
            updated_df = re.sub(
                r'CMD\s*\[\s*"gunicorn"',
                'CMD ["sh", "-c", "python manage.py migrate --noinput || true; exec gunicorn"',
                updated_df,
            )
            changed_df = True
            misalignments.append("Dockerfile: Missing database migration execution before application boot.")
            explanations["Dockerfile"] = "Added 'python manage.py migrate --noinput || true' before gunicorn start."

        # Node.js: Expose port and start script
        if is_node:
            if f"EXPOSE {app_port}" not in updated_df and "EXPOSE" in updated_df:
                updated_df = re.sub(r"EXPOSE\s+\d+", f"EXPOSE {app_port}", updated_df)
                changed_df = True
                misalignments.append(f"Dockerfile: Container port did not expose application port {app_port}.")
                explanations["Dockerfile"] = f"Aligned EXPOSE {app_port} in Dockerfile."

        if changed_df and updated_df != dockerfile_content:
            file_changes["Dockerfile"] = updated_df

    # 5. Inspect and Align requirements.txt (Django / Python)
    reqs_path = target_path / "requirements.txt"
    if reqs_path.exists() and is_django:
        try:
            reqs_content = reqs_path.read_text(encoding="utf-8", errors="ignore")
            updated_reqs = reqs_content
            needed = {
                "psycopg2": "psycopg2-binary>=2.9.9",
                "pillow": "Pillow>=9.0.0",
                "gunicorn": "gunicorn>=21.2.0",
                "whitenoise": "whitenoise>=6.6.0",
            }
            added = []
            for k, spec in needed.items():
                if k not in updated_reqs.lower():
                    if updated_reqs and not updated_reqs.endswith("\n"):
                        updated_reqs += "\n"
                    updated_reqs += f"{spec}\n"
                    added.append(spec.split(">=")[0])
            if added and updated_reqs != reqs_content:
                file_changes["requirements.txt"] = updated_reqs
                misalignments.append(f"requirements.txt: Missing production dependencies ({', '.join(added)}).")
                explanations["requirements.txt"] = f"Added {', '.join(added)} to requirements.txt."
        except Exception:
            pass

    diagnosis = "Analyzed codebase files and detected configuration misalignments."
    if health_port != app_port and ("docker-compose.yml" in file_changes or "Dockerfile" in file_changes):
        diagnosis = f"Port configuration mismatch between cloud healthcheck probe (Port {health_port}) and service runtime (Port {app_port})."
    elif is_db_refused:
        diagnosis = "Database connection refusal (HTTP 500) caused by localhost database binding and missing container networking."
    elif file_changes:
        diagnosis = f"Detected cross-file configuration misalignments across {len(file_changes)} file(s)."

    return CodebaseAlignmentResult(
        diagnosis=diagnosis,
        misalignments=misalignments,
        file_changes=file_changes,
        explanations=explanations,
    )


def classify_user_intent(user_input: str, has_pending_fix: bool = False) -> str:
    """Classifies user input into action intents."""
    cleaned = user_input.strip()
    lower = cleaned.lower()

    # 1. Affirmative answer to a pending fix
    if has_pending_fix and lower in ("y", "yes", "do it", "fix it", "apply", "apply it", "sure", "ok", "go ahead", "proceed", "yes please"):
        return "APPLY_PENDING_FIX"

    # 2. Rejection of a pending fix
    if has_pending_fix and lower in ("n", "no", "cancel", "skip", "discard", "nevermind"):
        return "DISCARD_PENDING_FIX"

    # 3. Direct shortcuts
    if lower in ("q", "quit", "exit"):
        return "QUIT"
    if lower in ("l", "logs", "log"):
        return "LOGS"
    if lower in ("s", "status", "diff"):
        return "STATUS"
    if lower in ("p", "push"):
        return "PUSH"
    if lower in ("r", "rerun", "verify"):
        return "RERUN"
    if lower in ("d", "deploy"):
        return "DEPLOY"
    if lower in ("help", "?", "commands"):
        return "HELP"

    # 4. Whole-codebase alignment trigger (Replit-style)
    if lower in ("a", "align", "algin", "fix", "align codebase", "auto fix", "autofix", "sync"):
        return "ALIGN_CODEBASE"

    # 5. Interactive Edit
    if lower in ("e", "edit"):
        return "EDIT"

    # 6. Configure Gemini API key on the fly
    if lower.startswith("set key ") or lower.startswith("set gemini ") or lower.startswith("gemini key "):
        return "SET_API_KEY"

    if (lower.startswith("open ") or lower.startswith("edit ")) and len(cleaned.split()) >= 2:
        return "EDIT_SPECIFIC_FILE"

    # 7. Questions and analytical queries
    question_triggers = [
        "what", "why", "how", "explain", "tell me", "show me", "where",
        "is there", "can you", "could you", "help", "diagnose", "check", "describe",
        "who are you", "who", "hi", "hello"
    ]
    diagnostic_keywords = [
        "issue", "error", "problem", "fail", "failed", "failure", "broken",
        "crash", "crashed", "bug", "wrong", "500", "404", "status", "traceback",
        "reason", "why", "what is", "name of", "who are you"
    ]

    is_question = cleaned.endswith("?") or any(lower.startswith(w + " ") or lower == w for w in question_triggers)
    is_diagnostic = any(w in lower for w in diagnostic_keywords)
    mentions_direct_file_action = any(w in lower for w in ["change ", "update ", "add ", "remove ", "replace ", "write ", "create ", "delete "]) and any(ext in lower for ext in [".py", ".txt", ".yml", ".yaml", ".json", "dockerfile", "compose", ".js", ".ts"])

    if (is_question or is_diagnostic) and not mentions_direct_file_action:
        return "QUESTION_OR_DIAGNOSIS"

    return "MODIFY_FILE"


def handle_conversational_query(
    user_query: str,
    target_path: Path,
    analysis: Any,
    detected_repo: Optional[str] = None,
    branch_name: str = "ai-devops-setup",
    last_error_logs: str = "",
    artifacts: Optional[Dict[str, str]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    known_secret_vals: Optional[List[str]] = None,
    primary_port: int = 8000,
    primary_health_path: str = "/",
) -> Optional[Dict[str, Any]]:
    """
    Answers user questions using full terminal context (logs, repo metadata, files)
    and returns an optional proposed fix dict: {"file_path": Path, "instruction": str, "description": str}.
    """
    artifacts = artifacts or {}
    known_secret_vals = known_secret_vals or []
    if last_error_logs:
        sanitized_logs = redact_secrets(last_error_logs, custom_secrets=known_secret_vals)
        if len(sanitized_logs) > 4000:
            sanitized_logs = sanitized_logs[:2500] + "\n...[truncated]...\n" + sanitized_logs[-1500:]
    else:
        sanitized_logs = "No failure logs recorded. Verification has not failed."
    framework_name = analysis.services[0].framework.value if (analysis and analysis.services) else "Unknown"
    default_port = analysis.services[0].default_port if (analysis and analysis.services) else primary_port

    # 1. Check environment and local .env files for GEMINI_API_KEY
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        for env_path in [target_path / ".env", Path.cwd() / ".env", Path.home() / ".ai_devops" / ".env"]:
            if env_path.exists():
                try:
                    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                        if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                            val = line.split("=", 1)[1].strip().strip("'\"")
                            if val:
                                api_key = val
                                os.environ["GEMINI_API_KEY"] = val
                                break
                except Exception:
                    pass
            if api_key:
                break

    if api_key:
        try:
            import json
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = f"""You are an expert AI DevOps pair programmer interacting with a software engineer directly in their terminal.
You have FULL CONTEXT of their terminal session: repository analysis, generated CI/CD and Docker files, and recent build/runtime verification failure logs.

Repository: {detected_repo or 'Local Repository'}
Branch: {branch_name}
Framework: {framework_name} (Port: {default_port})
Cloud Healthcheck Port: {primary_port} (Path: {primary_health_path})

=== RECENT RUNTIME / BUILD ERROR LOGS FROM TERMINAL ===
{sanitized_logs}

=== AVAILABLE CONFIGURATION ARTIFACTS ===
{", ".join(sorted(artifacts.keys()))}

=== USER QUESTION ===
{user_query}

Task:
1. Answer the user's question directly, clearly, concisely, and accurately based on the logs and terminal context.
2. If diagnosing a failure or error:
   - Identify the exact root cause from the logs (quote relevant line).
   - Explain why it failed in this Docker/cloud environment.
   - Propose a specific, concrete fix.
3. If a file modification is needed, specify the target file and instruction.

Return ONLY a valid JSON object matching this schema:
{{
  "answer": "Clear, markdown-formatted response explaining the issue and solution directly to the user",
  "proposed_fix": {{
    "file": "relative/path/to/file",
    "instruction": "Exact instruction of what to modify in the file",
    "description": "Short 1-line description of the fix"
  }}
}}
If no file fix is needed, set "proposed_fix": null.
"""
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )
            data = json.loads(response.text)
            answer = data.get("answer", "")
            if answer:
                print(f"\n{BOLD}{CYAN}🤖 AI Agent Diagnosis:{RESET}\n{answer}\n")
            pfix = data.get("proposed_fix")
            if pfix and isinstance(pfix, dict) and pfix.get("file"):
                target_f = find_target_file(target_path, pfix["file"]) or (target_path / pfix["file"])
                if target_f.exists():
                    return {
                        "file_path": target_f,
                        "instruction": pfix.get("instruction", user_query),
                        "description": pfix.get("description", "Apply proposed fix"),
                    }
            return None
        except Exception:
            pass

    # Deterministic Context-Aware Diagnostic Engine Fallback
    err_lower = sanitized_logs.lower()
    q_lower = user_query.lower().strip()
    proposed_fix = None

    # 1. Identity & Capability Queries (e.g. "what is name of this ai", "who are you", "what can you do")
    if any(tok in q_lower for tok in ["name of", "who are you", "what are you", "what is this ai", "what can you do", "who made", "hello", "hi", "hey"]):
        print_box(
            title="🤖 AI DevOps Agent",
            lines=[
                f"I am {BOLD}AI DevOps Agent{RESET}, your autonomous AI pair programmer for DevOps and cloud verification.",
                "",
                f"{BOLD}Active Session Context:{RESET}",
                f"  {MUTED}• Repository: {RESET} {BOLD}{WHITE}{detected_repo or 'Local'}{RESET}",
                f"  {MUTED}• Framework:  {RESET} {CYAN}{framework_name}{RESET} (Port: {default_port})",
                f"  {MUTED}• Health Port:{RESET} {AMBER}{primary_port}{RESET} (Path: {primary_health_path})",
                f"  {MUTED}• Branch:     {RESET} {PINK}{branch_name}{RESET}",
                "",
                f"{BOLD}What I can do for you:{RESET}",
                f"  • {EMERALD}[A] Align:{RESET}    Inspect all files and auto-align ports, Dockerfile, compose, and scripts",
                f"  • {PINK}[E] Edit:{RESET}     Interactive codebase editor (plain English instruction or file opening)",
                f"  • {WHITE}'what is issue':{RESET} Diagnose cloud sandbox and container failures from real logs",
                f"  • {CYAN}[R] Re-run:{RESET}   Push changes to GitHub and re-verify container in Cloud Sandbox",
                f"  • {PURPLE}[P] Push:{RESET}     Commit and push your changes to branch '{branch_name}'",
                "",
                f"{MUTED}💡 Tip: Run 'set key <GEMINI_API_KEY>' to enable live multi-turn AI reasoning!{RESET}",
            ],
            border_color=CYAN,
            badge="⚡",
        )
        return None

    # 2. Project Architecture Queries (e.g. "what is this app", "explain this repo", "tell me about this project")
    if any(tok in q_lower for tok in ["what is this app", "explain this repo", "explain the project", "about this project", "tell me about", "what does this do"]):
        arch_lines = [
            f"{BOLD}Project Architecture Summary:{RESET}",
            f"  {MUTED}• Detected Framework:{RESET} {CYAN}{framework_name}{RESET}",
            f"  {MUTED}• Internal Port:{RESET}      {AMBER}{default_port}{RESET}",
            f"  {MUTED}• Healthcheck Path:{RESET}   {CYAN}{primary_health_path}{RESET}",
            "",
            f"{BOLD}Key Identified Files:{RESET}",
        ]
        key_files = list_interesting_files(target_path, artifacts)
        for kf in key_files[:6]:
            rel_k = str(kf.relative_to(target_path)).replace("\\", "/")
            arch_lines.append(f"  {EMERALD}•{RESET} {rel_k}")

        arch_lines.extend([
            "",
            f"{BOLD}DevOps Status:{RESET}",
            f"  • Dockerfile: {'Configured' if (target_path / 'Dockerfile').exists() or 'Dockerfile' in artifacts else 'Not generated'}",
            f"  • Docker Compose: {'Configured' if (target_path / 'docker-compose.yml').exists() or 'docker-compose.yml' in artifacts else 'Not generated'}",
            f"  • Cloud Sandbox: {'Configured' if (target_path / '.github/workflows/ai-devops-verify.yml').exists() or '.github/workflows/ai-devops-verify.yml' in artifacts else 'Not generated'}",
        ])
        print_box(
            title="📂 Project & Architecture Overview",
            lines=arch_lines,
            border_color=PINK,
            badge="🏗️",
        )
        return None

    # 3. Port Mismatch Diagnosis
    if primary_port != default_port:
        compose_f = target_path / "docker-compose.yml"
        has_mapped_health_port = False
        if compose_f.exists():
            try:
                comp_txt = compose_f.read_text(encoding="utf-8", errors="ignore")
                has_mapped_health_port = f'"{primary_port}:' in comp_txt or f"'{primary_port}:" in comp_txt or f"{primary_port}:{default_port}" in comp_txt
            except Exception:
                pass

        if not has_mapped_health_port:
            print_box(
                title="🔍 Diagnosis: Port Configuration Mismatch",
                lines=[
                    f"{BOLD}Root Cause:{RESET}",
                    f"  The Cloud Sandbox healthcheck is probing {CYAN}http://localhost:{primary_port}{primary_health_path}{RESET},",
                    f"  but your {framework_name} application service listens on internal port {AMBER}{default_port}{RESET}.",
                    "",
                    f"{BOLD}Why this fails:{RESET}",
                    f"  Docker Compose must map host port {primary_port} to container port {default_port} (e.g. '{primary_port}:{default_port}'),",
                    f"  or the application container must be configured with PORT={primary_port}.",
                    "",
                    f"{BOLD}Recommended Solution:{RESET}",
                    f"  Type {EMERALD}[A]{RESET} to auto-align all ports across docker-compose.yml and Dockerfile.",
                ],
                border_color=AMBER,
                badge="🚨",
            )
            return None

    # 4. Database connection refusal (e.g. Django postgres connection to localhost failed: Connection refused)
    if any(token in err_lower for token in ["connection refused", "could not connect to server", "operationalerror", "connection to server at \"localhost\" failed"]) or ("http 500" in err_lower and "psycopg2" in err_lower):
        settings_file = find_target_file(target_path, "settings.py")
        rel_str = str(settings_file.relative_to(target_path)) if settings_file else "settings.py"
        print_box(
            title="🔍 Diagnosis: Database Connection Refusal (HTTP 500)",
            lines=[
                f"{BOLD}Root Cause:{RESET}",
                f"  The container built and booted, but when probing {CYAN}http://localhost:{default_port}/{RESET},",
                f"  the healthcheck probe failed with {RED}HTTP 500 Internal Server Error{RESET}.",
                "",
                f"{BOLD}Log Evidence:{RESET}",
                f"  {RED}psycopg2.OperationalError: connection to server at 'localhost', port 5432 failed: Connection refused{RESET}",
                "",
                f"{BOLD}Why this happened:{RESET}",
                f"  In {BOLD}{rel_str}{RESET}, the database HOST is configured as {AMBER}'localhost'{RESET}.",
                f"  Inside Docker, 'localhost' refers to the application container itself, where no PostgreSQL server is running.",
                "",
                f"{BOLD}Recommended Fix:{RESET}",
                f"  Update {BOLD}{rel_str}{RESET} to read {CYAN}DB_HOST{RESET} from environment ({CYAN}os.environ.get('DB_HOST', 'db'){RESET})",
                f"  with an automatic SQLite fallback when PostgreSQL is unreachable.",
            ],
            border_color=AMBER,
            badge="🚨",
        )
        if settings_file and settings_file.exists():
            proposed_fix = {
                "file_path": settings_file,
                "instruction": "Update settings.py with SQLite fallback and dynamic DB_HOST and allow all hosts",
                "description": f"Configure dynamic DB_HOST with SQLite fallback in '{rel_str}'",
            }

    elif "modulenotfounderror" in err_lower or "importerror" in err_lower:
        match = re.search(r"ModuleNotFoundError:\s+No module named\s+['\"]([a-zA-Z0-9_\-]+)['\"]", sanitized_logs, re.IGNORECASE)
        mod_name = match.group(1).strip() if match else "missing-module"
        req_file = find_target_file(target_path, "requirements.txt") or (target_path / "requirements.txt")
        print_box(
            title="🔍 Diagnosis: Missing Python Module",
            lines=[
                f"{BOLD}Root Cause:{RESET}",
                f"  Application crashed on startup: {RED}ModuleNotFoundError: No module named '{mod_name}'{RESET}",
                "",
                f"{BOLD}Recommended Fix:{RESET}",
                f"  Add {CYAN}{mod_name}{RESET} to requirements.txt and rebuild the container.",
            ],
            border_color=RED,
            badge="📦",
        )
        if req_file and req_file.exists():
            proposed_fix = {
                "file_path": req_file,
                "instruction": f"Add {mod_name} to requirements",
                "description": f"Add '{mod_name}' to requirements.txt",
            }

    elif "healthcheck probe failed" in err_lower or "http 000000" in err_lower:
        print_box(
            title="🔍 Diagnosis: Healthcheck Probe Failed (Port / Startup Issue)",
            lines=[
                f"{BOLD}Root Cause:{RESET}",
                f"  The healthcheck probe at {CYAN}http://localhost:{primary_port}{primary_health_path}{RESET} timed out or failed to connect.",
                "",
                f"{BOLD}Possible Reasons:{RESET}",
                f"  1. The server is binding to 127.0.0.1 instead of {BOLD}0.0.0.0{RESET}.",
                f"  2. The application port inside the container does not match {CYAN}{default_port}{RESET}.",
                f"  3. The application crashed during startup.",
                "",
                f"Type {PINK}[L]{RESET} to view full container logs, or type {EMERALD}[A]{RESET} to align codebase ports.",
            ],
            border_color=AMBER,
            badge="⚠️",
        )

    else:
        sample_inst = "Update settings.py to fallback to sqlite3" if "django" in framework_name.lower() else f"Change port to {default_port} in Dockerfile"
        print_box(
            title="💡 AI DevOps Guidance",
            lines=[
                f"{BOLD}Context:{RESET} {CYAN}{framework_name}{RESET} on port {default_port} (Healthcheck: port {primary_port})",
                "",
                f"You can instruct the agent to modify any file, inspect logs, re-run verification, or push:",
                f"  • {CYAN}Natural language:{RESET} e.g. '{sample_inst}'",
                f"  • {EMERALD}[A] Align:{RESET}          ⚡ Auto-align all files across codebase",
                f"  • {PINK}[E] Edit:{RESET}           Interactive codebase editor or file opener",
                f"  • {CYAN}[R] Re-run:{RESET}         Re-verify container on GitHub Actions",
                f"  • {WHITE}[L] Logs:{RESET}           View recent failure logs",
            ],
            border_color=CYAN,
            badge="💡",
        )

    return proposed_fix


def run_interactive_agent_session(
    target_path: Path,
    analysis: Any,
    detected_repo: Optional[str] = None,
    branch_name: str = "ai-devops-setup",
    token: Optional[str] = None,
    primary_port: int = 8000,
    primary_health_path: str = "/",
    known_secret_vals: Optional[List[str]] = None,
    last_error_logs: str = "",
    artifacts: Optional[Dict[str, str]] = None,
    app_name: Optional[str] = None,
) -> None:
    """
    Maintains a persistent, interactive terminal DevOps agent session.
    Allows user to ask questions ("what is issue"), give instructions in natural language,
    inspect colorized diffs, re-run verification, push to GitHub, or deploy.
    """
    pusher = GitPusher(token=token)
    runner = GitHubActionsRunner(token=token)
    known_secret_vals = known_secret_vals or []
    artifacts = artifacts or {}
    seen_run_ids = set()
    if runner.last_run_id:
        seen_run_ids.add(runner.last_run_id)

    pending_fix: Optional[Dict[str, Any]] = None
    conversation_history: List[Dict[str, str]] = []

    def trigger_rerun(commit_msg: str = "fix(devops): update configurations for verification re-run") -> bool:
        nonlocal last_error_logs
        if not detected_repo or not token:
            print(f"\n{AMBER}[!] Cannot re-run cloud sandbox: GitHub repository or token not configured.{RESET}")
            return False

        print(f"\n{INDIGO}Pushing latest changes and dispatching Cloud Sandbox Verification...{RESET}")
        pusher.push_branch(
            repo_dir=target_path,
            branch_name=branch_name,
            commit_message=commit_msg,
            github_repo=detected_repo,
        )
        head_sha = pusher.get_head_sha(target_path)

        print_step_box(
            step_num=1,
            total_steps=1,
            title="Cloud Sandbox Re-Verification on GitHub Actions",
            lines=[
                f"{MUTED}Target Repo:       {RESET} {BOLD}{WHITE}{detected_repo}{RESET}",
                f"{MUTED}Target Branch:     {RESET} {PINK}{branch_name}{RESET}",
                f"{MUTED}Healthcheck Port:  {RESET} {AMBER}{primary_port}{RESET} (Path: {primary_health_path})",
                f"{MUTED}Status:            {RESET} {INDIGO}Dispatched to GitHub Actions runner...{RESET}",
            ],
            badge="🧪",
            border_color=INDIGO,
        )

        res = runner.dispatch_and_wait(
            repo=detected_repo,
            branch=branch_name,
            workflow_file="ai-devops-verify.yml",
            health_port=primary_port,
            health_path=primary_health_path,
            commit_sha=head_sha,
            log_callback=lambda msg: print(redact_secrets(msg, custom_secrets=known_secret_vals)),
            exclude_run_ids=seen_run_ids,
        )
        if runner.last_run_id:
            seen_run_ids.add(runner.last_run_id)

        if res.success:
            print_success_box(
                title="100% VERIFIED SUCCESS! All Cloud Sandbox Checks Passed!",
                lines=[
                    f"Branch {BOLD}'{branch_name}'{RESET} is verified with Docker Compose and Health Checks.",
                    f"{EMERALD}✔ Container built and booted successfully{RESET}",
                    f"{EMERALD}✔ Healthcheck probe accepted traffic with HTTP 200{RESET}",
                ],
            )
            last_error_logs = ""
            return True
        else:
            last_error_logs = (res.error_tail or "") + "\n" + "\n".join(res.logs[-40:])
            print_alert_box(
                "Re-Verification Failed",
                [
                    f"Service did not accept traffic on port {primary_port}.",
                    "You can ask 'what is issue', instruct the agent to make more changes, or view logs [L].",
                ],
                is_error=True,
            )
            return False

    framework_name = analysis.services[0].framework.value if (analysis and analysis.services) else "Unknown"
    default_port = analysis.services[0].default_port if (analysis and analysis.services) else primary_port

    sample_example = "e.g. 'Update settings.py to fallback to sqlite3'" if "django" in framework_name.lower() else f"e.g. 'Change port to {primary_port} in Dockerfile'"

    print("\n" + "=" * 65)
    print_box(
        title="🤖 AI DevOps Interactive Terminal Agent",
        lines=[
            f"{MUTED}Workspace:     {RESET} {CYAN}{target_path}{RESET}",
            f"{MUTED}Branch:        {RESET} {PINK}{branch_name}{RESET}",
            f"{MUTED}Remote Repo:   {RESET} {BOLD}{WHITE}{detected_repo or 'Local'}{RESET}",
            f"{MUTED}Health Port:   {RESET} {AMBER}{primary_port}{RESET} (Path: {primary_health_path})",
            "",
            f"{BOLD}{WHITE}Session remains active. Ask questions or enter commands below:{RESET}",
            f"  • {BOLD}Ask anything:{RESET}   e.g. 'what is issue', 'why did it fail', 'how to fix this'",
            f"  • {EMERALD}[A] Align:{RESET}      ⚡ Auto-align all files across codebase (Replit-style)",
            f"  • {BOLD}Plain English:{RESET}  {sample_example}",
            f"  • {PINK}[E] Edit:{RESET}       Auto-align codebase or open interactive editor",
            f"  • {CYAN}[R] Re-run:{RESET}     Commit, push, and re-run Cloud Sandbox Verification",
            f"  • {PURPLE}[P] Push:{RESET}     Commit and push changes to GitHub branch",
            f"  • {EMERALD}[D] Deploy:{RESET}   Deploy verified app to Cloud (Render/Vercel)",
            f"  • {AMBER}[S] Status:{RESET}   View git status and modified files",
            f"  • {WHITE}[L] Logs:{RESET}     View recent failure logs",
            f"  • {RED}[Q] Quit:{RESET}     Exit application",
        ],
        border_color=PINK,
        badge="💬",
    )

    while True:
        try:
            user_input = input(f"\n{BOLD}{PINK}ai-devops> {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n[Session interrupted. Type 'q' to quit.]")
            continue

        if not user_input:
            continue

        intent = classify_user_intent(user_input, has_pending_fix=pending_fix is not None)

        # 1. Apply Pending Fix (e.g. user answered "yes" or "do it")
        if intent == "APPLY_PENDING_FIX" and pending_fix:
            target_file = pending_fix["file_path"]
            fix_instruction = pending_fix["instruction"]
            rel_path_str = str(target_file.relative_to(target_path)).replace("\\", "/")
            print(f"\n{INDIGO}Applying proposed fix to {BOLD}{CYAN}{rel_path_str}{RESET}...")

            success, explanation, new_content = apply_ai_instruction_to_file(
                file_path=target_file,
                instruction=fix_instruction,
                error_context=last_error_logs,
            )
            pending_fix = None
            if not success:
                print(f"  {RED}[!] {explanation}{RESET}")
                continue

            old_content = target_file.read_text(encoding="utf-8", errors="ignore")
            diff_str = format_colored_diff(
                rel_path_str,
                old_content,
                new_content,
                custom_secrets=known_secret_vals,
            )
            print(diff_str)
            print(f"{BOLD}{EMERALD}AI Plan:{RESET} {explanation}")

            apply_choice = prompt_choice("Apply these changes to disk? [Y]es / [E]dit in editor / [N]o", default="Y").lower()
            if apply_choice in ("y", "yes", ""):
                target_file.write_text(new_content, encoding="utf-8")
                if rel_path_str in artifacts:
                    artifacts[rel_path_str] = new_content
                print(f"  {EMERALD}✔ Saved changes to '{rel_path_str}'!{RESET}")

                if detected_repo and token:
                    rerun_choice = prompt_choice("Push changes and re-run Cloud Sandbox Verification now? [Y/n]", default="Y").lower()
                    if rerun_choice in ("y", "yes", ""):
                        trigger_rerun(commit_msg=f"fix: {fix_instruction[:60]}")
            elif apply_choice in ("e", "edit"):
                target_file.write_text(new_content, encoding="utf-8")
                open_file_in_editor(target_file)
            else:
                print(f"  {MUTED}[Changes discarded]{RESET}")
            continue

        # 2. Discard Pending Fix
        if intent == "DISCARD_PENDING_FIX":
            pending_fix = None
            print(f"  {MUTED}[Pending fix cancelled]{RESET}")
            continue

        # 3. Help Command
        if intent == "HELP":
            print_box(
                title="📖 AI DevOps Terminal Agent Help & Commands",
                lines=[
                    f"{BOLD}{WHITE}Ask questions or diagnose issues in natural language:{RESET}",
                    f"  • {CYAN}'what is issue'{RESET}        -> Diagnoses error logs and explains failure",
                    f"  • {CYAN}'why did it fail'{RESET}      -> Root cause analysis of container / probe",
                    f"  • {CYAN}'how to fix this'{RESET}      -> Proposes code changes and can apply them",
                    f"  • {CYAN}'who are you'{RESET}          -> AI Agent capabilities and session status",
                    "",
                    f"{BOLD}{WHITE}Direct action shortcuts:{RESET}",
                    f"  • {EMERALD}[A] Align:{RESET}   Auto-align all files across codebase (Replit-style)",
                    f"  • {PINK}[E] Edit:{RESET}    Interactive codebase editor or file opener",
                    f"  • {CYAN}[R] Re-run:{RESET}  Commit, push, and re-run Cloud Sandbox Verification",
                    f"  • {PURPLE}[P] Push:{RESET}    Commit and push changes to GitHub branch",
                    f"  • {EMERALD}[D] Deploy:{RESET}  Deploy verified app to Cloud (Render / Vercel)",
                    f"  • {AMBER}[S] Status:{RESET}  View git status and modified files",
                    f"  • {WHITE}[L] Logs:{RESET}    View recent container and probe failure logs",
                    f"  • {RED}[Q] Quit:{RESET}    Exit application cleanly",
                ],
                border_color=PINK,
                badge="💡",
            )
            continue

        # 4. Set Gemini API Key on the fly
        if intent == "SET_API_KEY":
            parts = user_input.split()
            if len(parts) >= 3:
                key_val = parts[2].strip()
                os.environ["GEMINI_API_KEY"] = key_val
                print(f"  {EMERALD}✔ Gemini API Key configured! Live conversational reasoning is now enabled.{RESET}")
            else:
                print(f"  {AMBER}Usage: set key <YOUR_GEMINI_API_KEY>{RESET}")
            continue

        # 5. Whole-Codebase Alignment (Triggered by 'a', 'align', 'fix', etc.)
        if intent == "ALIGN_CODEBASE":
            conversation_history.append({"role": "user", "content": user_input})
            alignment = align_project_codebase(
                target_path=target_path,
                analysis=analysis,
                error_context=last_error_logs,
                artifacts=artifacts,
                primary_port=primary_port,
                primary_health_path=primary_health_path,
            )

            if alignment.file_changes:
                diag_lines = [
                    f"{BOLD}Diagnosis:{RESET}",
                    f"  {alignment.diagnosis}",
                    "",
                    f"{BOLD}Identified Codebase Discrepancies across {len(alignment.file_changes)} file(s):{RESET}",
                ]
                for mis in alignment.misalignments:
                    diag_lines.append(f"  {EMERALD}•{RESET} {mis}")

                print_box(
                    title="🧠 Replit-Style Codebase Alignment & Solution",
                    lines=diag_lines,
                    border_color=CYAN,
                    badge="⚡",
                )

                print(f"\n{BOLD}{WHITE}Proposed Changes Across Your Project:{RESET}")
                for rel_p, new_text in alignment.file_changes.items():
                    target_f = target_path / rel_p
                    old_text = target_f.read_text(encoding="utf-8", errors="ignore") if target_f.exists() else ""
                    print(format_colored_diff(rel_p, old_text, new_text, custom_secrets=known_secret_vals))

                apply_choice = prompt_choice(
                    f"🚀 Apply all {len(alignment.file_changes)} aligned changes across your project and re-verify? [Y/n]",
                    default="Y"
                ).lower()

                if apply_choice in ("y", "yes", ""):
                    for rel_p, new_text in alignment.file_changes.items():
                        target_f = target_path / rel_p
                        target_f.parent.mkdir(parents=True, exist_ok=True)
                        target_f.write_text(new_text, encoding="utf-8")
                        artifacts[rel_p] = new_text
                        print(f"  {EMERALD}✔ Updated {rel_p}{RESET}")

                    print(f"\n{BOLD}{EMERALD}All project files aligned successfully!{RESET}")
                    if detected_repo and token:
                        trigger_rerun(commit_msg="fix(codebase): align ports, Dockerfile, and configurations")
                else:
                    print(f"  {MUTED}[Codebase alignment changes discarded]{RESET}")
            else:
                framework_str = analysis.services[0].framework.value if (analysis and analysis.services) else "application"
                print(f"\n{EMERALD}✨ Codebase configuration files are already aligned for {framework_str} (Port: {primary_port}).{RESET}")
                print(f"{MUTED}No pending discrepancies detected between Dockerfile, docker-compose.yml, and environment.{RESET}")
            continue

        # 6. Interactive Codebase Editor (Triggered by 'e' or 'edit')
        if intent == "EDIT":
            # First check if there are pending misalignments across files
            alignment = align_project_codebase(
                target_path=target_path,
                analysis=analysis,
                error_context=last_error_logs,
                artifacts=artifacts,
                primary_port=primary_port,
                primary_health_path=primary_health_path,
            )
            if alignment.file_changes:
                diag_lines = [
                    f"{BOLD}Diagnosis:{RESET}",
                    f"  {alignment.diagnosis}",
                    "",
                    f"{BOLD}Identified Discrepancies across {len(alignment.file_changes)} file(s):{RESET}",
                ]
                for mis in alignment.misalignments:
                    diag_lines.append(f"  {EMERALD}•{RESET} {mis}")

                print_box(
                    title="⚡ Codebase Alignment Changes Available",
                    lines=diag_lines,
                    border_color=CYAN,
                    badge="🧠",
                )
                print(f"\n{BOLD}{WHITE}Proposed Changes Across Your Project:{RESET}")
                for rel_p, new_text in alignment.file_changes.items():
                    target_f = target_path / rel_p
                    old_text = target_f.read_text(encoding="utf-8", errors="ignore") if target_f.exists() else ""
                    print(format_colored_diff(rel_p, old_text, new_text, custom_secrets=known_secret_vals))

                apply_choice = prompt_choice(
                    f"Apply these {len(alignment.file_changes)} aligned changes? [Y/n]",
                    default="Y"
                ).lower()
                if apply_choice in ("y", "yes", ""):
                    for rel_p, new_text in alignment.file_changes.items():
                        target_f = target_path / rel_p
                        target_f.parent.mkdir(parents=True, exist_ok=True)
                        target_f.write_text(new_text, encoding="utf-8")
                        artifacts[rel_p] = new_text
                        print(f"  {EMERALD}✔ Updated {rel_p}{RESET}")
                    print(f"\n{BOLD}{EMERALD}All project files aligned successfully!{RESET}")
                    if detected_repo and token:
                        trigger_rerun(commit_msg="fix(codebase): align configurations across project")
                    continue

            # If no automated alignment changes, provide the Interactive Codebase Editor menu
            sample_cmd = f"Change port to {primary_port} in Dockerfile" if "express" in framework_name.lower() else "Update settings.py"
            print_box(
                title="✏️  Interactive Codebase Editor",
                lines=[
                    f"{BOLD}How would you like to edit?{RESET}",
                    f"  • {CYAN}Enter plain English instruction{RESET} (e.g. '{sample_cmd}')",
                    f"  • {PINK}Enter filename to open{RESET} in your editor (e.g. 'Dockerfile', 'package.json')",
                    f"  • {AMBER}Type 'list'{RESET} to browse all repository files",
                    f"  • {MUTED}Type 'back'{RESET} to return to main menu",
                ],
                border_color=PINK,
                badge="📝",
            )
            try:
                sub_edit = input(f"{BOLD}{PINK}edit> {RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                continue

            if not sub_edit or sub_edit.lower() in ("b", "back", "cancel", "q"):
                continue
            if sub_edit.lower() in ("l", "list"):
                chosen_f = interactive_select_file(target_path, artifacts=artifacts)
                if chosen_f:
                    open_file_in_editor(chosen_f)
                continue

            # Check if sub_edit matches an existing file directly
            cand_f = target_path / sub_edit
            if cand_f.exists() and cand_f.is_file():
                open_file_in_editor(cand_f)
                continue

            # Otherwise, execute user instruction using agent file modification
            target_f = find_target_file(target_path, sub_edit)
            if not target_f:
                target_f = interactive_select_file(target_path, artifacts=artifacts)
            if target_f:
                rel_f_str = str(target_f.relative_to(target_path)).replace("\\", "/")
                print(f"\n{INDIGO}Target File:{RESET} {BOLD}{CYAN}{rel_f_str}{RESET}")
                ok_mod, exp_mod, new_content = apply_ai_instruction_to_file(
                    file_path=target_f,
                    instruction=sub_edit,
                    error_context=last_error_logs,
                )
                if ok_mod:
                    old_c = target_f.read_text(encoding="utf-8", errors="ignore")
                    print(format_colored_diff(rel_f_str, old_c, new_content, custom_secrets=known_secret_vals))
                    print(f"{BOLD}{EMERALD}AI Plan:{RESET} {exp_mod}")
                    app_c = prompt_choice("Apply these changes to disk? [Y/n]", default="Y").lower()
                    if app_c in ("y", "yes", ""):
                        target_f.write_text(new_content, encoding="utf-8")
                        if rel_f_str in artifacts:
                            artifacts[rel_f_str] = new_content
                        print(f"  {EMERALD}✔ Saved changes to '{rel_f_str}'!{RESET}")
                        if detected_repo and token:
                            r_c = prompt_choice("Push changes and re-verify now? [Y/n]", default="Y").lower()
                            if r_c in ("y", "yes", ""):
                                trigger_rerun(commit_msg=f"fix: {sub_edit[:60]}")
                else:
                    print(f"  {AMBER}[!] {exp_mod}{RESET}")
            continue

        # 7. Conversational Query / Diagnosis ("what is issue", "who are you", etc.)
        if intent == "QUESTION_OR_DIAGNOSIS":
            conversation_history.append({"role": "user", "content": user_input})
            proposed = handle_conversational_query(
                user_query=user_input,
                target_path=target_path,
                analysis=analysis,
                detected_repo=detected_repo,
                branch_name=branch_name,
                last_error_logs=last_error_logs,
                artifacts=artifacts,
                conversation_history=conversation_history,
                known_secret_vals=known_secret_vals,
                primary_port=primary_port,
                primary_health_path=primary_health_path,
            )

            if proposed and proposed.get("file_path") and proposed["file_path"].exists():
                pending_fix = proposed
                rel_f = str(proposed["file_path"].relative_to(target_path)).replace("\\", "/")
                confirm_fix = prompt_choice(f"Would you like me to apply this fix to '{rel_f}' now? [Y/n]", default="Y").lower()
                if confirm_fix in ("y", "yes", ""):
                    target_file = proposed["file_path"]
                    rel_path_str = rel_f
                    success, explanation, new_content = apply_ai_instruction_to_file(
                        file_path=target_file,
                        instruction=proposed["instruction"],
                        error_context=last_error_logs,
                    )
                    pending_fix = None
                    if success:
                        old_content = target_file.read_text(encoding="utf-8", errors="ignore")
                        diff_str = format_colored_diff(
                            rel_path_str,
                            old_content,
                            new_content,
                            custom_secrets=known_secret_vals,
                        )
                        print(diff_str)
                        print(f"{BOLD}{EMERALD}AI Plan:{RESET} {explanation}")

                        apply_choice = prompt_choice("Apply these changes to disk? [Y]es / [E]dit in editor / [N]o", default="Y").lower()
                        if apply_choice in ("y", "yes", ""):
                            target_file.write_text(new_content, encoding="utf-8")
                            if rel_path_str in artifacts:
                                artifacts[rel_path_str] = new_content
                            print(f"  {EMERALD}✔ Saved changes to '{rel_path_str}'!{RESET}")

                            if detected_repo and token:
                                rerun_choice = prompt_choice("Push changes and re-run Cloud Sandbox Verification now? [Y/n]", default="Y").lower()
                                if rerun_choice in ("y", "yes", ""):
                                    trigger_rerun(commit_msg=f"fix: {proposed['instruction'][:60]}")
                        elif apply_choice in ("e", "edit"):
                            target_file.write_text(new_content, encoding="utf-8")
                            open_file_in_editor(target_file)
                        else:
                            print(f"  {MUTED}[Changes discarded]{RESET}")
            continue

        # Command: Open specific file in editor
        if intent == "EDIT_SPECIFIC_FILE":
            parts = user_input.split(None, 1)
            target_str = parts[1].strip() if len(parts) > 1 else ""
            target_f = find_target_file(target_path, target_str) or (target_path / target_str)
            if target_f.exists() and target_f.is_file():
                print(f"\nLaunching editor for '{target_f.relative_to(target_path)}'...")
                old_text = target_f.read_text(encoding="utf-8", errors="ignore")
                opened = open_file_in_editor(target_f)
                if opened:
                    new_text = target_f.read_text(encoding="utf-8", errors="ignore")
                    if new_text != old_text:
                        print(f"  {EMERALD}✔ Saved modifications to '{target_f.relative_to(target_path)}'!{RESET}")
                        diff_str = format_colored_diff(
                            str(target_f.relative_to(target_path)),
                            old_text,
                            new_text,
                            custom_secrets=known_secret_vals,
                        )
                        print(diff_str)
            else:
                print(f"  {RED}[!] Could not locate file: '{target_str}'{RESET}")
            continue

        cmd_lower = user_input.lower()

        # Command: Quit / Exit
        if intent == "QUIT":
            status_res = subprocess.run(["git", "status", "--porcelain"], cwd=target_path, capture_output=True, text=True)
            if status_res.stdout.strip():
                print(f"\n{AMBER}⚠️ You have uncommitted or unpushed changes in the workspace.{RESET}")
                choice = input("Would you like to commit and push to GitHub before exiting? [y/N/cancel]: ").strip().lower()
                if choice in ("cancel", "c"):
                    continue
                if choice in ("y", "yes"):
                    pusher.push_branch(
                        repo_dir=target_path,
                        branch_name=branch_name,
                        commit_message="feat(devops): save changes before exiting interactive session",
                        github_repo=detected_repo,
                    )
                    print(f"  {EMERALD}✔ Pushed changes to GitHub.{RESET}")
            print(f"\n{CYAN}Session ended. Thank you for using AI DevOps Agent! 👋{RESET}\n")
            break

        # Command: View Failure Logs
        if intent == "LOGS":
            if last_error_logs:
                print(f"\n{BOLD}{AMBER}=== Recent Cloud Sandbox Logs ==={RESET}")
                print(redact_secrets(last_error_logs, custom_secrets=known_secret_vals))
                print(f"{BOLD}{AMBER}================================={RESET}\n")
            else:
                print(f"\n{MUTED}[Notice] No failure logs available for this session.{RESET}")
            continue

        # Command: Git Status / Diffs
        if intent == "STATUS":
            status_res = subprocess.run(["git", "status", "--short"], cwd=target_path, capture_output=True, text=True)
            print(f"\n{BOLD}{CYAN}=== Git Status ({branch_name}) ==={RESET}")
            if status_res.stdout.strip():
                print(status_res.stdout)
                diff_res = subprocess.run(["git", "diff"], cwd=target_path, capture_output=True, text=True)
                if diff_res.stdout.strip():
                    print(f"{BOLD}{CYAN}=== Working Tree Diff ==={RESET}")
                    print(format_colored_diff("Working Tree", "", diff_res.stdout, custom_secrets=known_secret_vals))
            else:
                print(f"  {EMERALD}Working tree clean. All changes committed.{RESET}")
            print(f"{BOLD}{CYAN}==================================={RESET}\n")
            continue

        # Command: Commit & Push
        if intent == "PUSH":
            msg = input("Commit message (press Enter for 'fix(devops): interactive agent updates'): ").strip()
            msg = msg or "fix(devops): interactive agent updates"
            print(f"\n{INDIGO}Pushing changes to '{branch_name}' on GitHub...{RESET}")
            pushed, push_msg = pusher.push_branch(
                repo_dir=target_path,
                branch_name=branch_name,
                commit_message=msg,
                github_repo=detected_repo,
            )
            if pushed:
                print(f"  {EMERALD}✔ {push_msg}{RESET}")
            else:
                print(f"  {RED}[!] {push_msg}{RESET}")
            continue

        # Command: Re-run Cloud Sandbox Verification
        if intent == "RERUN":
            trigger_rerun()
            continue

        # Command: Cloud Deployment
        if intent == "DEPLOY":
            default_branch = pusher.get_default_branch(detected_repo) if detected_repo else "main"
            print(f"\n{BOLD}{CYAN}Launching Cloud Deployment...{RESET}")
            try:
                from ai_devops_agent.deployer import interactive_cloud_deployment
                interactive_cloud_deployment(
                    analysis=analysis,
                    detected_repo=detected_repo,
                    branch_name=default_branch,
                    local_env_dir=target_path,
                    default_env_values={},
                    app_name=app_name,
                )
            except Exception as e:
                print(f"  {RED}[!] Cloud deployment failed: {e}{RESET}")
            continue

        # Command: Manual file edit fallback if requested
        if intent == "EDIT":
            print(f"\n{AMBER}Please specify which file to open, e.g.: 'open btre/settings.py' or 'open Dockerfile'{RESET}")
            continue

        # Natural Language Agent File Modification (e.g. "Update settings.py to allow all hosts")
        target_file = find_target_file(target_path, user_input)
        if not target_file:
            print(f"\n{AMBER}Could not determine which file to modify from: '{user_input}'. Please specify the file name (e.g. 'update settings.py') or ask 'what is issue'.{RESET}")
            continue
            target_file = interactive_select_file(target_path, artifacts=artifacts)
            if not target_file:
                continue

        rel_path_str = str(target_file.relative_to(target_path))
        print(f"\n{INDIGO}Target File:{RESET} {BOLD}{CYAN}{rel_path_str}{RESET}")
        print(f"{INDIGO}Analyzing and applying instruction...{RESET}")

        success, explanation, new_content = apply_ai_instruction_to_file(
            file_path=target_file,
            instruction=user_input,
            error_context=last_error_logs,
        )

        if not success:
            print(f"\n  {AMBER}[!] {explanation}{RESET}")
            edit_now = input(f"Would you like to open '{rel_path_str}' in your editor to make the change manually? [Y/n]: ").strip().lower()
            if edit_now not in ("n", "no"):
                open_file_in_editor(target_file)
            continue

        # Show syntax-highlighted diff
        old_content = target_file.read_text(encoding="utf-8", errors="ignore")
        diff_str = format_colored_diff(
            rel_path_str,
            old_content,
            new_content,
            custom_secrets=known_secret_vals,
        )
        print(diff_str)
        print(f"{BOLD}{EMERALD}AI Plan:{RESET} {explanation}")

        # Ask user for approval
        apply_choice = prompt_choice("Apply these changes to disk? [Y]es / [E]dit in editor / [N]o", default="Y").lower()
        if apply_choice in ("y", "yes", ""):
            target_file.write_text(new_content, encoding="utf-8")
            if rel_path_str in artifacts:
                artifacts[rel_path_str] = new_content
            print(f"  {EMERALD}✔ Saved changes to '{rel_path_str}'!{RESET}")

            # Ask if user wants to push and verify immediately
            if detected_repo and token:
                rerun_choice = prompt_choice("Push changes and re-run Cloud Sandbox Verification now? [Y/n]", default="Y").lower()
                if rerun_choice in ("y", "yes", ""):
                    trigger_rerun(commit_msg=f"fix: {user_input[:60]}")

        elif apply_choice in ("e", "edit"):
            target_file.write_text(new_content, encoding="utf-8")
            open_file_in_editor(target_file)
            print(f"  {EMERALD}✔ Finished editing '{rel_path_str}'.{RESET}")
        else:
            print(f"  {MUTED}[Changes discarded]{RESET}")


def resolve_target_workspace(
    target: str, token: Optional[str] = None, repo: Optional[str] = None
) -> Tuple[Path, Optional[str], Optional[tempfile.TemporaryDirectory]]:
    """Resolves target: if it's a GitHub URL or slug, clones it into a temp workspace."""
    github_slug = parse_github_slug(target) or (parse_github_slug(repo) if repo else None)

    if github_slug and (parse_github_slug(target) or not Path(target).exists()):
        temp_dir = tempfile.TemporaryDirectory()
        work_path = Path(temp_dir.name)
        print(f"\n[Auto-Clone] Detected GitHub repository: {github_slug}")
        print(f"[Auto-Clone] Cloning into isolated workspace: {work_path} ...")

        clone_url = f"https://github.com/{github_slug}.git"
        if token:
            clone_url = f"https://x-access-token:{token}@github.com/{github_slug}.git"

        res = subprocess.run(
            ["git", "clone", clone_url, str(work_path)],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if res.returncode != 0:
            clean_err = sanitize_url_for_logging(redact_secrets(res.stderr, custom_secrets=[token] if token else None))
            print(f"ERROR: Failed to clone {github_slug}: {clean_err}")
            sys.exit(1)

        print("[Auto-Clone] Repository cloned successfully.\n")
        return work_path, github_slug, temp_dir

    local_path = Path(target).resolve()
    if not local_path.exists() or not local_path.is_dir():
        print(f"Error: Target path not found: {target}", file=sys.stderr)
        sys.exit(1)

    return local_path, github_slug, None


def run_autonomous_pipeline(
    target: str,
    github_token: Optional[str] = None,
    branch_name: str = "ai-devops-setup",
    max_iterations: int = 1,
    repo: Optional[str] = None,
    app_name: Optional[str] = None,
):
    """Coordinates the entire autonomous journey: GitHub URL -> Clone -> Analysis -> Secrets -> Diff -> Push -> Self-Healing."""
    token = github_token or os.getenv("GITHUB_TOKEN")
    target_path, detected_repo, temp_workspace = resolve_target_workspace(target, token=token, repo=repo)

    # Resolve idiomatic application name (never use random temp dir names like tmpv85pzgsk)
    if not app_name:
        default_app_name = derive_default_app_name(target, repo=detected_repo or repo)
        if sys.stdin.isatty():
            try:
                entered = input(f"\033[38;2;160;174;192mEnter Application Name (press Enter for '{default_app_name}'):\033[0m ").strip()
                app_name = sanitize_app_name(entered) if entered else default_app_name
            except (KeyboardInterrupt, EOFError):
                app_name = default_app_name
        else:
            app_name = default_app_name
    else:
        app_name = sanitize_app_name(app_name)

    try:
        print_powerline_header(app_name or detected_repo or target_path.name, branch_name, "Initializing")
        print_box(
            title="🚀 Autonomous Cloud DevOps Engine",
            lines=[
                f"{MUTED}Application Name:  {RESET}  {BOLD}{WHITE}{app_name}{RESET}",
                f"{MUTED}Target GitHub Repo:{RESET}  {BOLD}{WHITE}{detected_repo or 'Local Repository'}{RESET}",
                f"{MUTED}Local Workspace:   {RESET}  {CYAN}{target_path}{RESET}",
                f"{MUTED}Setup Branch:      {RESET}  {PINK}{branch_name}{RESET}",
                f"{MUTED}Max Self-Healings: {RESET}  {AMBER}{max_iterations} iterations{RESET}",
            ],
            border_color=CYAN,
        )

        # 1. Deterministic Analysis with application name override
        analysis = analyze_repository(target_path, project_name=app_name)
        all_env_vars = []
        primary_port = 8080
        primary_health_path = "/"

        service_lines = [f"{EMERALD}✔ Detected {len(analysis.services)} service(s):{RESET}"]
        for s in analysis.services:
            target_prov = analysis.suggested_deployment_targets.get(s.name, "Render")
            primary_port = s.default_port
            primary_health_path = s.health_check_path
            service_lines.append(
                f"  {MUTED}•{RESET} {BOLD}{s.name}{RESET} {MUTED}({s.framework.value}, {s.language.value}){RESET} -> Port {CYAN}{s.default_port}{RESET} -> Deploy: {PURPLE}{target_prov.upper()}{RESET}"
            )
            all_env_vars.extend(s.env_vars_detected)

        all_env_vars = sorted(list(set(all_env_vars)))
        print_step_box(
            step_num=1,
            total_steps=5,
            title="Repository Structure & Dependency Analysis",
            lines=service_lines,
            badge="🔍",
            border_color=CYAN,
        )

        # 2. Interactive Secrets & Environment Variables Configuration
        configured_secrets = {}
        target_repo_name = detected_repo or analysis.repo_name
        secrets_mgr = GitHubSecretsManager(token=token)
        configured_secrets = secrets_mgr.interactive_configure_secrets(
            repo=target_repo_name,
            detected_vars=all_env_vars,
            local_env_dir=target_path,
        )
        if configured_secrets:
            for k in configured_secrets.keys():
                if k not in all_env_vars:
                    all_env_vars.append(k)
            all_env_vars.sort()

        # 3. Generate DevOps Configuration Bundle
        artifacts = generate_devops_bundle(analysis, branch_name=branch_name, extra_env_vars=all_env_vars)
        artifact_preview_lines = [
            f"{MUTED}Generated {len(artifacts)} production-ready configuration files:{RESET}",
        ]
        for rel_p in sorted(artifacts.keys()):
            artifact_preview_lines.append(f"  {EMERALD}✔{RESET} {BOLD}{rel_p}{RESET}")

        print_step_box(
            step_num=3,
            total_steps=5,
            title="DevOps Configuration Bundle Generated",
            lines=artifact_preview_lines,
            badge="📄",
            border_color=PINK,
        )

        # 4. Display Syntax-Highlighted Terminal Diffs
        known_secret_vals = list(configured_secrets.values()) if configured_secrets else []
        display_bundle_diffs(target_path, artifacts, custom_secrets=known_secret_vals)

        # 5. Human-in-the-Loop Approval & Interactive Editing
        while True:
            review_lines = [
                f"{BOLD}{WHITE}Choose approval action:{RESET}",
                f"  {PINK}[Y]{RESET} 🚀 Proceed and push branch '{branch_name}' to '{detected_repo or target_path.name}'",
                f"  {CYAN}[E]{RESET} ✏️ Edit generated files in your editor (Dockerfile, Compose, etc.)",
                f"  {PURPLE}[V]{RESET} 🔍 View syntax-highlighted diffs again",
                f"  {RED}[N]{RESET} 🛑 Abort and exit",
            ]
            print_box(
                title="Human-in-the-Loop Review & Confirmation",
                lines=review_lines,
                border_color=PINK,
                badge="🛡️",
            )

            confirm = prompt_choice("Choose an option [Y/e/v/n]", default="Y").lower()

            if confirm in ("e", "edit"):
                artifacts = interactive_edit_bundle(target_path, artifacts)
                display_bundle_diffs(target_path, artifacts, custom_secrets=known_secret_vals)
                continue
            elif confirm in ("v", "view", "diff"):
                display_bundle_diffs(target_path, artifacts, custom_secrets=known_secret_vals)
                continue
            elif confirm in ("n", "no", "q", "quit", "abort"):
                print("\n[Aborted] No changes committed. Exiting cleanly.\n")
                return
            elif confirm in ("y", "yes", ""):
                break
            else:
                print(f"  [!] Unrecognized option '{confirm}'. Please enter Y, E, V, or N.")

        # Write final files to workspace
        print(f"\nWriting verified configuration files...")
        for rel_path, content in artifacts.items():
            dest_file = target_path / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            dest_file.write_text(content, encoding="utf-8")
        print("  [OK] All files written to workspace.")

        if not detected_repo or not token:
            print("\nNo GitHub remote repository or token specified.")
            print(f"Local files are ready! To launch locally, run: docker compose up --build\n")
            return

        # Push to GitHub Branch
        print_step_box(
            step_num=4,
            total_steps=5,
            title="Promoting Dedicated Branch to GitHub",
            lines=[
                f"{MUTED}Target Branch:     {RESET} {PINK}{branch_name}{RESET}",
                f"{MUTED}Remote Repository: {RESET} {BOLD}{WHITE}{detected_repo}{RESET}",
            ],
            badge="🚀",
            border_color=PURPLE,
        )
        pusher = GitPusher(token=token)
        pushed, push_msg = pusher.push_branch(
            repo_dir=target_path,
            branch_name=branch_name,
            commit_message="feat(devops): automated containerization, docker-compose, and CI/CD setup",
            github_repo=detected_repo,
        )

        if not pushed:
            print_alert_box("GitHub Branch Push Error", [f"Could not push to GitHub: {push_msg}"], is_error=True)
            return
        print(f"  {EMERALD}✔ {push_msg}{RESET}")
        head_sha = pusher.get_head_sha(target_path)

        # 6. Autonomous Cloud Verification & Self-Healing Loop (Max iterations)
        runner = GitHubActionsRunner(token=token)
        current_artifacts = dict(artifacts)
        seen_run_ids = set()
        seen_diagnoses = set()

        for iteration in range(1, max_iterations + 1):
            print_step_box(
                step_num=5,
                total_steps=5,
                title=f"Cloud Sandbox Execution on GitHub Actions (Attempt {iteration}/{max_iterations})",
                lines=[
                    f"{MUTED}Target Repo:       {RESET} {BOLD}{WHITE}{detected_repo}{RESET}",
                    f"{MUTED}Workflow File:     {RESET} {CYAN}.github/workflows/ai-devops-verify.yml{RESET}",
                    f"{MUTED}Target Branch:     {RESET} {PINK}{branch_name}{RESET}",
                    f"{MUTED}Healthcheck Port:  {RESET} {AMBER}{primary_port}{RESET} (Path: {primary_health_path})",
                    f"{MUTED}Status:            {RESET} {INDIGO}Dispatched to GitHub Actions runner...{RESET}",
                ],
                badge="🧪",
                border_color=INDIGO,
            )

            result = runner.dispatch_and_wait(
                repo=detected_repo,
                branch=branch_name,
                workflow_file="ai-devops-verify.yml",
                health_port=primary_port,
                health_path=primary_health_path,
                commit_sha=head_sha,
                log_callback=lambda msg: print(redact_secrets(msg, custom_secrets=known_secret_vals)),
                exclude_run_ids=seen_run_ids,
            )

            if runner.last_run_id:
                seen_run_ids.add(runner.last_run_id)

            if result.success:
                print_success_box(
                    title="100% VERIFIED SUCCESS! All Cloud Sandbox Checks Passed!",
                    lines=[
                        f"Branch {BOLD}'{branch_name}'{RESET} is fully verified with Docker Compose and Health Checks.",
                        f"{EMERALD}✔ Docker Compose specification validated{RESET}",
                        f"{EMERALD}✔ Container built and booted successfully{RESET}",
                        f"{EMERALD}✔ Healthcheck probe accepted traffic with HTTP 200{RESET}",
                        f"{EMERALD}✔ Safe container teardown complete{RESET}",
                    ],
                )

                # Determine base default branch (main/master)
                default_branch = "main"
                if detected_repo:
                    default_branch = pusher.get_default_branch(detected_repo)

                # Create Pull Request to merge verified setup branch into main
                pr_url = f"https://github.com/{detected_repo}/pull/new/{branch_name}" if detected_repo else ""
                pr_num = None
                if detected_repo and token:
                    print(f"\n{MUTED}[Pull Request]{RESET} Creating Pull Request: '{branch_name}' -> '{default_branch}'...")
                    pr_ok, fetched_url, fetched_num, pr_msg = pusher.create_pull_request(
                        repo=detected_repo,
                        head_branch=branch_name,
                        base_branch=default_branch,
                    )
                    if fetched_url:
                        pr_url = fetched_url
                        pr_num = fetched_num
                        print(f"  {EMERALD}✔ {pr_msg}{RESET}")
                        print(f"  👉 Pull Request: {CYAN}{pr_url}{RESET}")

                # Production Deployment Guard: Merge into 'main' required
                guard_lines = [
                    f"{AMBER}⚠️  All code must be merged into '{default_branch}' before deploying to production.{RESET}",
                ]
                if pr_url:
                    guard_lines.append(f"{MUTED}Pull Request URL:{RESET}  {CYAN}{pr_url}{RESET}")
                guard_lines.extend([
                    "",
                    f"{BOLD}{WHITE}Choose deployment promotion action:{RESET}",
                    f"  {PINK}[A]{RESET} ⚡ Auto-merge PR #{pr_num or 1} via GitHub API now -> Deploy to Cloud",
                    f"  {CYAN}[M]{RESET} 👤 I have merged the PR manually on GitHub -> Deploy to Cloud",
                    f"  {MUTED}[S]{RESET} ⏭️  Skip deployment (deploy anytime with 'uv run python cli.py deploy')",
                ])

                print_box(
                    title="Production Cloud Deployment Guard",
                    lines=guard_lines,
                    border_color=AMBER,
                    badge="🛡️ ",
                )

                deploy_choice = prompt_choice("Enter choice [A/M/S]", default="A").upper()

                if not deploy_choice:
                    deploy_choice = "M"

                if deploy_choice in ("S", "SKIP", "N", "NO"):
                    print(f"\n[Notice] Cloud deployment skipped. Merge the PR on GitHub when ready!\n")
                    run_interactive_agent_session(
                        target_path=target_path,
                        analysis=analysis,
                        detected_repo=detected_repo,
                        branch_name=branch_name,
                        token=token,
                        primary_port=primary_port,
                        primary_health_path=primary_health_path,
                        known_secret_vals=known_secret_vals,
                        last_error_logs="",
                        artifacts=current_artifacts,
                        app_name=app_name,
                    )
                    return

                # If user selected auto-merge
                if deploy_choice == "A" and detected_repo and pr_num:
                    print(f"\n  -> Merging PR #{pr_num} into '{default_branch}' via GitHub API...")
                    merge_ok, merge_msg = pusher.merge_pull_request(detected_repo, pr_num)
                    if merge_ok:
                        print(f"  [OK] {merge_msg}")
                    else:
                        print(f"  [!] {merge_msg}. Please merge on GitHub directly.")

                # Verify PR merge status before deploying
                if detected_repo and pr_num:
                    is_merged = pusher.is_pull_request_merged(detected_repo, pr_num)
                    if not is_merged:
                        print(f"  -> Verifying PR #{pr_num} merge status on GitHub...")
                        for _ in range(3):
                            if pusher.is_pull_request_merged(detected_repo, pr_num):
                                is_merged = True
                                break
                            time.sleep(2)

                    if not is_merged:
                        confirm = input(f"\n⚠️ PR #{pr_num} does not appear merged into '{default_branch}' yet. Deploy anyway? [y/N]: ").strip().lower()
                        if confirm not in ("y", "yes"):
                            print(f"\n[Halted] Deployment paused until PR is merged into '{default_branch}'.\n")
                            return

                print(f"\n✅ Ready! Launching Cloud Deployment from '{default_branch}'...")
                try:
                    from ai_devops_agent.deployer import interactive_cloud_deployment
                    interactive_cloud_deployment(
                        analysis=analysis,
                        detected_repo=detected_repo,
                        branch_name=default_branch,
                        local_env_dir=target_path,
                        default_env_values=configured_secrets,
                        app_name=app_name,
                    )
                except Exception as e:
                    print(f"\n[Notice] Cloud deployment skipped: {e}\n")
                run_interactive_agent_session(
                    target_path=target_path,
                    analysis=analysis,
                    detected_repo=detected_repo,
                    branch_name=branch_name,
                    token=token,
                    primary_port=primary_port,
                    primary_health_path=primary_health_path,
                    known_secret_vals=known_secret_vals,
                    last_error_logs="",
                    artifacts=current_artifacts,
                    app_name=app_name,
                )
                return

            # Handle failure & Self-Healing
            if iteration < max_iterations:
                print(f"\n[AI Self-Healing]: Cloud build failed on attempt {iteration}. Diagnosing failure...")
                
                # Identify which service in monorepo failed from logs
                target_service = analysis.services[0] if analysis.services else None
                error_combined = (result.error_tail or "") + "\n" + "\n".join(result.logs[-40:])

                for s in analysis.services:
                    if f"-{s.name}" in error_combined or f"/{s.name}" in error_combined or f"_{s.name}" in error_combined:
                        target_service = s
                        break

                dockerfile_rel_path = "Dockerfile"
                if target_service and target_service.path != ".":
                    dockerfile_rel_path = f"{target_service.path}/Dockerfile"

                current_df = current_artifacts.get(dockerfile_rel_path, "")
                if not current_df and (target_path / dockerfile_rel_path).exists():
                    current_df = (target_path / dockerfile_rel_path).read_text(encoding="utf-8")

                patch_result = diagnose_and_patch(
                    current_dockerfile=current_df,
                    error_tail=error_combined,
                    framework=target_service.framework.value if target_service else "unknown",
                )

                # Anti-repetition check: Prevent repeating an identical failing diagnosis across attempts
                if patch_result.diagnosis in seen_diagnoses and ("libpq-dev" in patch_result.diagnosis or "database" in patch_result.diagnosis.lower() or "connection refused" in error_combined.lower()):
                    patch_result.diagnosis = "Application failed to connect to database at runtime; escalating to containerized Postgres and SQLite fallback."
                    patch_result.patch_explanation = "Provisioned database service in docker-compose.yml and set DB_HOST=db."

                seen_diagnoses.add(patch_result.diagnosis)

                # If runtime database connection was refused, ensure docker-compose.yml has PostgreSQL
                if "connection refused" in error_combined.lower() or "database" in patch_result.diagnosis.lower():
                    compose_path = target_path / "docker-compose.yml"
                    if compose_path.exists():
                        compose_text = compose_path.read_text(encoding="utf-8")
                        if "image: postgres" not in compose_text:
                            pg_block = (
                                "  db:\n"
                                "    image: postgres:15-alpine\n"
                                f"    container_name: {analysis.repo_name}-db\n"
                                "    restart: unless-stopped\n"
                                "    environment:\n"
                                "      POSTGRES_DB: postgres\n"
                                "      POSTGRES_USER: postgres\n"
                                "      POSTGRES_PASSWORD: \"1234\"\n"
                                "    ports:\n"
                                "      - \"5432:5432\"\n"
                                "    healthcheck:\n"
                                "      test: [\"CMD-SHELL\", \"pg_isready -U postgres\"]\n"
                                "      interval: 3s\n"
                                "      timeout: 3s\n"
                                "      retries: 5\n\n"
                            )
                            if "services:\n" in compose_text:
                                compose_text = compose_text.replace("services:\n", f"services:\n{pg_block}", 1)
                                if "depends_on:" not in compose_text:
                                    compose_text = re.sub(
                                        r"(container_name:[^\n]+\n)",
                                        r"\1    depends_on:\n      db:\n        condition: service_healthy\n",
                                        compose_text,
                                        count=1
                                    )
                                compose_path.write_text(compose_text, encoding="utf-8")
                                current_artifacts["docker-compose.yml"] = compose_text
                                patch_result.patch_explanation += " and provisioned PostgreSQL container in docker-compose.yml."

                if target_service:
                    print(f"  * Failing Service: {target_service.name} ({dockerfile_rel_path})")
                print(f"  * Diagnosis:       {patch_result.diagnosis}")
                print(f"  * AI Patch:        {patch_result.patch_explanation}")

                # Apply patch to the correct Dockerfile on disk
                patch_dest = target_path / dockerfile_rel_path
                patch_dest.parent.mkdir(parents=True, exist_ok=True)
                patch_dest.write_text(patch_result.updated_dockerfile, encoding="utf-8")
                current_artifacts[dockerfile_rel_path] = patch_result.updated_dockerfile

                # Push patch to GitHub branch
                print(f"  * Pushing AI patch to '{branch_name}' on GitHub...")
                pusher.push_branch(
                    repo_dir=target_path,
                    branch_name=branch_name,
                    commit_message=f"fix(devops): apply AI self-healing patch (Attempt {iteration})",
                    github_repo=detected_repo,
                )
                head_sha = pusher.get_head_sha(target_path)
                print("  * Patch pushed. Re-verifying Cloud Sandbox...")
                time.sleep(3)
            else:
                print(f"\n⚠️ Cloud sandbox verification finished (Attempt {iteration}/{max_iterations}).")
                if not result.success:
                    print(f"Detailed logs available at: https://github.com/{detected_repo}/actions\n")

        # Once verification loop completes without 100% success, start interactive terminal agent!
        if not result.success:
            last_logs = (result.error_tail or "") + "\n" + "\n".join(result.logs[-40:])
            run_interactive_agent_session(
                target_path=target_path,
                analysis=analysis,
                detected_repo=detected_repo,
                branch_name=branch_name,
                token=token,
                primary_port=primary_port,
                primary_health_path=primary_health_path,
                known_secret_vals=known_secret_vals,
                last_error_logs=last_logs,
                artifacts=current_artifacts,
                app_name=app_name,
            )

    finally:
        if temp_workspace:
            temp_workspace.cleanup()
