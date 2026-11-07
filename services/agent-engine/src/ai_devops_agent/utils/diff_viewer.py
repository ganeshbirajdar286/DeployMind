import difflib
from pathlib import Path
from typing import Dict, Optional, List
from ai_devops_agent.utils.sanitizer import redact_secrets

# ANSI Color Codes
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[32m"
COLOR_RED = "\033[31m"
COLOR_CYAN = "\033[36m"
COLOR_YELLOW = "\033[33m"
COLOR_BOLD = "\033[1m"


def format_colored_diff(
    filename: str,
    old_content: str,
    new_content: str,
    custom_secrets: Optional[List[str]] = None,
) -> str:
    """Produces syntax-highlighted terminal diff with automated secret redaction."""
    # Redact secrets before computing or displaying diff
    old_sanitized = redact_secrets(old_content, custom_secrets=custom_secrets)
    new_sanitized = redact_secrets(new_content, custom_secrets=custom_secrets)

    old_lines = old_sanitized.splitlines(keepends=True)
    new_lines = new_sanitized.splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm="",
        )
    )

    if not diff:
        return f"{COLOR_CYAN}[Unchanged] {filename}{COLOR_RESET}\n"

    output = []
    output.append(f"\n{COLOR_BOLD}{COLOR_YELLOW}============================================================{COLOR_RESET}")
    output.append(f"{COLOR_BOLD}{COLOR_YELLOW} File: {filename}{COLOR_RESET}")
    output.append(f"{COLOR_BOLD}{COLOR_YELLOW}============================================================{COLOR_RESET}")

    for line in diff:
        line_clean = line.rstrip()
        if line.startswith("+++") or line.startswith("---"):
            output.append(f"{COLOR_CYAN}{line_clean}{COLOR_RESET}")
        elif line.startswith("@@"):
            output.append(f"{COLOR_CYAN}{line_clean}{COLOR_RESET}")
        elif line.startswith("+"):
            output.append(f"{COLOR_GREEN}{line_clean}{COLOR_RESET}")
        elif line.startswith("-"):
            output.append(f"{COLOR_RED}{line_clean}{COLOR_RESET}")
        else:
            output.append(line_clean)

    diff_str = "\n".join(output) + "\n"
    return redact_secrets(diff_str, custom_secrets=custom_secrets)


def display_bundle_diffs(
    target_dir: Path,
    artifacts: Dict[str, str],
    custom_secrets: Optional[List[str]] = None,
):
    """Displays terminal diff for all files in the generated DevOps bundle with secret masking."""
    target_dir = Path(target_dir).resolve()
    print("\n" + "=" * 60)
    print(f"📄 Proposed DevOps Configuration Diffs ({len(artifacts)} files)")
    print("=" * 60)

    for rel_path, new_content in artifacts.items():
        dest_file = target_dir / rel_path
        old_content = ""
        if dest_file.exists():
            try:
                old_content = dest_file.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                old_content = ""

        diff_str = format_colored_diff(rel_path, old_content, new_content, custom_secrets=custom_secrets)
        print(diff_str)
