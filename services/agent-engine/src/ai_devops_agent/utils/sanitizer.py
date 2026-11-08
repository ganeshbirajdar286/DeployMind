import re
from pathlib import Path
from typing import List, Optional, Set

# Regex patterns for common API keys, tokens, and credentials
TOKEN_PATTERNS = [
    # GitHub Classic Token (ghp_...)
    (re.compile(r"ghp_[a-zA-Z0-9]{30,45}"), "ghp_****..."),
    # GitHub Fine-Grained Token (github_pat_...)
    (re.compile(r"github_pat_[a-zA-Z0-9_]{60,100}"), "github_pat_****..."),
    # Vercel Access Token (vcp_...)
    (re.compile(r"vcp_[a-zA-Z0-9]{24,70}"), "vcp_****..."),
    # Render API Key (rnd_...)
    (re.compile(r"rnd_[a-zA-Z0-9]{24,70}"), "rnd_****..."),
    # Google AI / Gemini API Keys (AIza... or AQ....)
    (re.compile(r"AIza[0-9A-Za-z-_]{35}"), "AIza****..."),
    (re.compile(r"AQ\.[a-zA-Z0-9_-]{40,80}"), "AQ.****..."),
    # JWT Tokens (header.payload.signature)
    (re.compile(r"ey[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "ey****[REDACTED_JWT]"),
    # Database URIs with credentials: protocol://user:pass@host
    (re.compile(r"((?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)://[^:]+:)(.+)(@[a-zA-Z0-9_\-\.]+)"), r"\1****\3"),
    # URLs with embedded basic auth: https://user:token@domain
    (re.compile(r"(https?://[^:/@\s]+:)(.+)(@[a-zA-Z0-9_\-\.]+)"), r"\1****\3"),
]

SENSITIVE_KEY_KEYWORDS: Set[str] = {
    "SECRET", "PASSWORD", "PASS", "PWD", "TOKEN", "KEY",
    "CREDENTIAL", "AUTH", "PRIVATE", "DATABASE", "MONGODB",
    "MONGO", "POSTGRES", "MYSQL", "REDIS", "DSN", "APIKEY",
    "BREVO", "SENDGRID", "MAIL", "SMTP"
}


def is_sensitive_key(key_name: str) -> bool:
    """Returns True if the environment variable name indicates sensitive credential data."""
    upper = key_name.upper()
    if upper in ("PORT", "HOSTNAME", "HOST", "NODE_ENV", "PUBLIC_URL"):
        return False
    return any(keyword in upper for keyword in SENSITIVE_KEY_KEYWORDS)


def sanitize_url_for_logging(url: str) -> str:
    """Removes tokens, passwords, and embedded credentials from URLs before logging."""
    if not url:
        return ""
    # Replace https://x-access-token:<token>@github.com with https://x-access-token:****@github.com
    sanitized = re.sub(r"(https?://[^:/@\s]+:)([^@\s]+)(@)", r"\1****\3", url)
    return sanitized


def redact_secrets(text: str, custom_secrets: Optional[List[str]] = None) -> str:
    """Redacts known token patterns and custom secret values from log output or text."""
    if not text:
        return ""

    redacted = text

    # 1. Apply regex token patterns
    for pattern, replacement in TOKEN_PATTERNS:
        redacted = pattern.sub(replacement, redacted)

    # 2. Redact any known custom secrets provided (if length >= 4)
    if custom_secrets:
        for secret in set(custom_secrets):
            if secret and len(secret.strip()) >= 4:
                secret_clean = secret.strip()
                # Don't redact common short strings or booleans
                if secret_clean.lower() in ("true", "false", "none", "null", "undefined", "localhost"):
                    continue
                redacted = redacted.replace(secret_clean, "******")

    return redacted


def sanitize_app_name(name: str) -> str:
    """Sanitizes an application or project name for compatibility with Vercel and Render."""
    if not name:
        return ""
    # Lowercase, replace non-alphanumeric (except hyphens and underscores) with hyphens
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "-", name.strip().lower())
    # Collapse multiple consecutive hyphens or underscores to a single hyphen
    cleaned = re.sub(r"[_\-]+", "-", cleaned)
    # Strip leading and trailing hyphens
    cleaned = cleaned.strip("-")
    # Cap length to 80 chars for cloud provider compatibility
    return cleaned[:80]


def derive_default_app_name(target: str, repo: Optional[str] = None) -> str:
    """Extracts an idiomatic, clean application name from target URL, repo slug, or local directory path."""
    # 1. If explicit repo slug provided (e.g. "owner/3w-test1")
    if repo:
        slug_part = repo.strip().split("/")[-1].replace(".git", "")
        if slug_part and not slug_part.lower().startswith("tmp"):
            return sanitize_app_name(slug_part)

    # 2. If target is a GitHub URL or slug
    if target:
        t = target.strip().rstrip("/")
        if "github.com" in t or ("/" in t and not Path(t).exists()):
            slug_part = t.split("/")[-1].replace(".git", "")
            if slug_part and not slug_part.lower().startswith("tmp"):
                return sanitize_app_name(slug_part)

        # 3. If target is a local path
        try:
            p = Path(t).resolve()
            if p.name and not p.name.lower().startswith("tmp"):
                return sanitize_app_name(p.name)
        except Exception:
            pass

    return "app"
