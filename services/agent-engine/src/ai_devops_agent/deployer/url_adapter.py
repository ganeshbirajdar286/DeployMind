import os
import re
from pathlib import Path
from typing import Optional, Dict, Any, List, Set, Tuple, Callable
from urllib.parse import urlparse
from ai_devops_agent.utils.theme import (
    CYAN,
    PINK,
    PURPLE,
    EMERALD,
    AMBER,
    WHITE,
    MUTED,
    BOLD,
    DIM,
    RESET,
)


def extract_url_subpath(url: Optional[str]) -> str:
    """Extracts any path prefix/suffix (e.g. '/api' or '/api/v1') from an existing URL string."""
    if not url:
        return ""
    url = url.strip().strip("'\"")
    if not url:
        return ""

    try:
        # If URL doesn't have scheme, prepend http:// for proper urlparse path splitting
        if not url.startswith(("http://", "https://")):
            url_to_parse = f"http://{url}"
        else:
            url_to_parse = url

        parsed = urlparse(url_to_parse)
        path = parsed.path.rstrip("/")
        if path and path != "/":
            return path
    except Exception:
        pass

    return ""


def inspect_frontend_api_conventions(
    frontend_dir: Optional[Path],
    env_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Scans frontend source code to determine how API endpoints and backend base URLs are constructed."""
    report = {
        "files_scanned": 0,
        "explicit_api_concatenations": 0,
        "direct_endpoint_calls": 0,
        "base_url_in_axios": False,
        "code_appends_api": False,
        "code_expects_api_in_base": False,
        "detected_endpoints": [],
    }

    if not frontend_dir:
        return report

    frontend_path = Path(frontend_dir).resolve()
    if not frontend_path.exists() or not frontend_path.is_dir():
        return report

    # Patterns for backend URL references
    keys = env_keys or ["VITE_BACKEND_URL", "VITE_BASE_URL", "VITE_API_URL", "NEXT_PUBLIC_API_URL", "REACT_APP_BACKEND_URL"]
    key_pattern = "|".join(re.escape(k) for k in keys)

    # Regex: ${VITE_BACKEND_URL}/api/... or VITE_BACKEND_URL + '/api'
    appends_api_regex = re.compile(
        rf"(?:\${{[^}}]*?(?:{key_pattern})[^}}]*?}}\s*/api|\b(?:{key_pattern})\b\s*\+\s*['\"]/api)",
        re.IGNORECASE,
    )

    # Regex: ${VITE_BACKEND_URL}/something (where something does NOT start with 'api/')
    direct_call_regex = re.compile(
        rf"\${{[^}}]*?(?:{key_pattern})[^}}]*?}}\s*/(?!api/|api['\"`])([a-zA-Z0-9_\-]+)",
        re.IGNORECASE,
    )

    # Regex: axios.create({ baseURL: VITE_... })
    axios_base_regex = re.compile(
        rf"baseURL\s*:\s*(?:process\.env\.[A-Z0-9_]+|import\.meta\.env\.[A-Z0-9_]+|(?:{key_pattern}))",
        re.IGNORECASE,
    )

    # Direct relative endpoint calls without /api: e.g. api.get('/users') or api.post('/auth/login')
    relative_api_call_regex = re.compile(
        r"(?:axios|api|client|http|fetchClient)\.(?:get|post|put|delete|patch)\(\s*['\"]/(?!api/)([a-zA-Z0-9_\-]+)",
        re.IGNORECASE,
    )

    ignored_dirs = {"node_modules", "dist", "build", ".next", ".git", ".turbo", "coverage"}
    candidate_extensions = {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}

    for root, dirs, files in os.walk(frontend_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        for file in files:
            p = Path(root) / file
            if p.suffix in candidate_extensions:
                report["files_scanned"] += 1
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                if appends_api_regex.search(content):
                    report["explicit_api_concatenations"] += 1

                for m in direct_call_regex.finditer(content):
                    report["direct_endpoint_calls"] += 1
                    ep = m.group(1)
                    if ep and ep not in report["detected_endpoints"]:
                        report["detected_endpoints"].append(ep)

                if axios_base_regex.search(content):
                    report["base_url_in_axios"] = True

                for m in relative_api_call_regex.finditer(content):
                    ep = m.group(1)
                    if ep and ep not in report["detected_endpoints"]:
                        report["detected_endpoints"].append(ep)

    if report["explicit_api_concatenations"] > 0:
        report["code_appends_api"] = True

    if report["direct_endpoint_calls"] > 0 or (report["base_url_in_axios"] and report["detected_endpoints"]):
        report["code_expects_api_in_base"] = True

    return report


def inspect_backend_route_prefixes(backend_dir: Optional[Path]) -> Set[str]:
    """Scans backend source files to see if routes are grouped under /api, /api/v1, etc."""
    prefixes: Set[str] = set()
    if not backend_dir:
        return prefixes

    backend_path = Path(backend_dir).resolve()
    if not backend_path.exists() or not backend_path.is_dir():
        return prefixes

    ignored_dirs = {"node_modules", "dist", "build", ".git", "venv", ".venv", "__pycache__"}
    candidate_extensions = {".js", ".ts", ".py"}

    # Express / NestJS / FastAPI / Flask patterns
    patterns = [
        re.compile(r"app\.use\(\s*['\"](/api(?:/v\d+)?)['\"]", re.IGNORECASE),
        re.compile(r"router\.use\(\s*['\"](/api(?:/v\d+)?)['\"]", re.IGNORECASE),
        re.compile(r"APIRouter\([^)]*?prefix=['\"](/api(?:/v\d+)?)['\"]", re.IGNORECASE),
        re.compile(r"include_router\([^)]*?prefix=['\"](/api(?:/v\d+)?)['\"]", re.IGNORECASE),
        re.compile(r"url_prefix=['\"](/api(?:/v\d+)?)['\"]", re.IGNORECASE),
        re.compile(r"setGlobalPrefix\(['\"]([a-zA-Z0-9_\-]+)['\"]\)", re.IGNORECASE),
    ]

    for root, dirs, files in os.walk(backend_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
        for file in files:
            p = Path(root) / file
            if p.suffix in candidate_extensions:
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    for pat in patterns:
                        for m in pat.finditer(content):
                            found = m.group(1)
                            if found:
                                if not found.startswith("/"):
                                    found = f"/{found}"
                                prefixes.add(found.rstrip("/"))
                except Exception:
                    continue

    return prefixes


def adapt_backend_url_for_frontend(
    live_backend_url: str,
    original_env_value: Optional[str] = None,
    frontend_dir: Optional[Path] = None,
    env_key: str = "VITE_BACKEND_URL",
    backend_dir: Optional[Path] = None,
    log_callback: Optional[Callable[[str], None]] = None,
) -> str:
    """Intelligently adapts the live backend URL for frontend environment injection, preserving path suffixes (like /api)."""
    def emit(msg: str):
        if log_callback:
            log_callback(msg)

    clean_live_url = live_backend_url.strip().rstrip("/")
    if not clean_live_url:
        return ""

    original_val = (original_env_value or "").strip()
    original_subpath = extract_url_subpath(original_val)

    # 1. Inspect Frontend Code Conventions
    fe_report = inspect_frontend_api_conventions(
        frontend_dir=frontend_dir,
        env_keys=[env_key, "VITE_BACKEND_URL", "VITE_BASE_URL", "VITE_API_URL"],
    )

    # 2. Inspect Backend Route Prefixes (if backend directory exists)
    be_prefixes = inspect_backend_route_prefixes(backend_dir)

    chosen_subpath = ""
    reason = ""

    # Decision Matrix:
    # A. User's original env variable already included a subpath (e.g. /api or /api/v1)
    if original_subpath:
        if fe_report.get("code_appends_api") and not fe_report.get("code_expects_api_in_base"):
            # Frontend code already does `${URL}/api/...`, so adding `/api` to base would result in `/api/api/...`
            chosen_subpath = ""
            reason = f"Original env had '{original_subpath}', but frontend code already appends '/api'; omitting subpath to prevent duplication."
        else:
            chosen_subpath = original_subpath
            reason = f"Retained path suffix '{original_subpath}' from original configured {env_key}."

    # B. Original env had NO subpath, but frontend code expects /api or backend routes mount /api
    elif not original_subpath:
        if "/api" in be_prefixes or any(p.startswith("/api") for p in be_prefixes):
            primary_prefix = sorted(list(be_prefixes))[0]
            if fe_report.get("code_appends_api"):
                chosen_subpath = ""
                reason = f"Backend mounts on '{primary_prefix}', and frontend code explicitly appends '/api'."
            elif fe_report.get("code_expects_api_in_base") or fe_report.get("base_url_in_axios"):
                chosen_subpath = primary_prefix
                reason = f"Backend routes are mounted under '{primary_prefix}' and frontend API client makes direct relative calls."
            else:
                # Default to base
                chosen_subpath = ""
                reason = "No subpath required by frontend."

    # Construct final URL
    final_url = f"{clean_live_url}{chosen_subpath}" if chosen_subpath else clean_live_url

    # Emit clean terminal telemetry
    emit(f"\n{CYAN}┌── 🌐 Intelligent Frontend API URL Adaptation ────────────────────{RESET}")
    emit(f"{CYAN}│{RESET} {MUTED}Target Env Key:   {RESET}{BOLD}{WHITE}{env_key}{RESET}")
    emit(f"{CYAN}│{RESET} {MUTED}Live Host URL:    {RESET}{EMERALD}{clean_live_url}{RESET}")
    if original_val:
        emit(f"{CYAN}│{RESET} {MUTED}Original User Env:{RESET}{DIM}{original_val}{RESET} (subpath: '{original_subpath or 'none'}')")
    if fe_report.get("files_scanned", 0) > 0:
        call_style = "appends '/api' in code" if fe_report.get("code_appends_api") else "expects '/api' in base URL" if fe_report.get("code_expects_api_in_base") else "standard"
        emit(f"{CYAN}│{RESET} {MUTED}Codebase Scan:    {RESET}{fe_report['files_scanned']} file(s) checked ({call_style})")
    if be_prefixes:
        emit(f"{CYAN}│{RESET} {MUTED}Backend Prefixes: {RESET}{', '.join(sorted(be_prefixes))}")
    emit(f"{CYAN}│{RESET} {MUTED}Decision:         {RESET}{PURPLE}{reason}{RESET}")
    emit(f"{CYAN}│{RESET} {MUTED}Final Injected:   {RESET}{BOLD}{EMERALD}{final_url}{RESET}")
    emit(f"{CYAN}└───────────────────────────────────────────────────────────────────{RESET}\n")

    return final_url
