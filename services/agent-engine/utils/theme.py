import os
import re
import sys
import shutil
import unicodedata
from typing import List, Tuple, Optional, Dict, Any

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 24-bit TrueColor Palette matching modern Powerline / Oh-My-Posh theme (Image 2)
CYAN    = "\033[38;2;34;211;238m"     # #22d3ee - Bright Cyan
TEAL    = "\033[38;2;45;212;191m"     # #2dd4bf - Teal
PINK    = "\033[38;2;244;114;182m"    # #f472b6 - Soft Pink/Coral
ROSE    = "\033[38;2;251;113;133m"    # #fb7185 - Rose
PURPLE  = "\033[38;2;168;85;247m"    # #a855f7 - Violet Purple
INDIGO  = "\033[38;2;129;140;248m"    # #818cf8 - Indigo
EMERALD = "\033[38;2;52;211;153m"     # #34d399 - Mint Emerald
AMBER   = "\033[38;2;251;191;36m"     # #fbbf24 - Amber/Gold
RED     = "\033[38;2;248;113;113m"    # #f87171 - Soft Red
WHITE   = "\033[38;2;248;250;252m"    # #f8fafc - Bright White
MUTED   = "\033[38;2;148;163;184m"    # #94a3b8 - Slate Muted
DARK    = "\033[38;2;71;85;105m"      # #475569 - Dark Slate

BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"

# Powerline Backgrounds
BG_PURPLE = "\033[48;2;168;85;247m\033[38;2;255;255;255m\033[1m"
BG_PINK   = "\033[48;2;244;114;182m\033[38;2;15;23;42m\033[1m"
BG_ORANGE = "\033[48;2;251;146;60m\033[38;2;15;23;42m\033[1m"
BG_CYAN   = "\033[48;2;34;211;238m\033[38;2;15;23;42m\033[1m"
BG_BLUE   = "\033[48;2;59;130;246m\033[38;2;255;255;255m\033[1m"
BG_GREEN  = "\033[48;2;52;211;153m\033[38;2;15;23;42m\033[1m"


def strip_ansi(text: str) -> str:
    """Removes all ANSI escape sequences from a string."""
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", text)


def str_width(text: str) -> int:
    """
    Computes exact terminal column display width for a string, accounting for:
    - Stripping ANSI escape sequences
    - Zero-width joiners, spaces, variation selectors, and combining marks (0 width)
    - Fullwidth / Wide East Asian characters (2 width)
    - Terminal emojis and symbols (2 width)
    - Text checkmarks and symbols (1 width)
    """
    clean = strip_ansi(text)
    width = 0
    prev_code = 0
    for ch in clean:
        code = ord(ch)
        if 0xFE00 <= code <= 0xFE0F or code in (0x200D, 0x200B):
            if code == 0xFE0F and width > 0:
                if prev_code in (0x270F, 0x26A0, 0x2708, 0x2709, 0x270A, 0x270B, 0x270C, 0x270D):
                    width += 1
            prev_code = code
            continue
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Cf"):
            prev_code = code
            continue
        eaw = unicodedata.east_asian_width(ch)
        if eaw in ("W", "F"):
            width += 2
        elif 0x1F300 <= code <= 0x1FAFF:
            width += 2
        else:
            width += 1
        prev_code = code
    return width


def get_terminal_width(default: int = 86) -> int:
    """Gets current terminal width clamped between 74 and 96 characters."""
    try:
        cols = shutil.get_terminal_size((default, 20)).columns
        return max(74, min(cols, 96))
    except Exception:
        return default


def powerline_bar(segments: List[Tuple[str, str, str]]) -> str:
    """
    Renders powerline segments like:
    [ ai-devops ] [ mojito- ] (main) [ Step 1/5 ]
    Each tuple is (bg_color, fg_text, symbol)
    """
    parts = []
    for bg, text, symbol in segments:
        parts.append(f"{bg} {symbol} {text} {RESET}")
    return " ".join(parts)


def print_powerline_header(repo: str, branch: str = "main", step: str = "Ready"):
    """Displays top powerline bar matching Image 2."""
    clean_repo = repo.replace(".git", "").split("/")[-1] if repo else "devops-agent"
    segments = [
        (BG_PURPLE, "ai-devops", "⚡"),
        (BG_PINK, clean_repo, "📁"),
        (BG_ORANGE, branch, ""),
        (BG_CYAN, step, "🚀"),
    ]
    print("\n" + powerline_bar(segments) + "\n")


def box(
    title: str,
    lines: List[str],
    border_color: str = CYAN,
    badge: Optional[str] = None,
    width: Optional[int] = None,
) -> str:
    """
    Draws a styled rounded terminal box with mathematical column alignment:
    ╭─[ 🚀 Title ]────────────────────────────────────────────────────╮
    │  Content line 1                                                 │
    │  Content line 2                                                 │
    ╰─────────────────────────────────────────────────────────────────╯
    """
    clean_lines = [l.expandtabs(4) for l in lines]
    clean_badge = badge.strip() if badge else ""

    if clean_badge:
        title_str = f" {clean_badge} {BOLD}{WHITE}{title}{RESET} "
        vis_title_len = str_width(f" {clean_badge} {title} ")
    else:
        title_str = f" {BOLD}{WHITE}{title}{RESET} "
        vis_title_len = str_width(f" {title} ")

    max_body_w = max((str_width(l) for l in clean_lines), default=0)
    term_w = width or get_terminal_width()
    min_required_w = max(vis_title_len + 7, max_body_w + 6)
    w = max(term_w, min_required_w)

    top_dashes = max(2, w - 5 - vis_title_len)
    w = 5 + vis_title_len + top_dashes
    inner_body_w = w - 6

    top_line = f"{border_color}╭─[{RESET}{title_str}{border_color}]{'─' * top_dashes}╮{RESET}"

    body_lines = []
    for line in clean_lines:
        vis_len = str_width(line)
        padding = max(0, inner_body_w - vis_len)
        body_lines.append(f"{border_color}│{RESET}  {line}{' ' * padding}  {border_color}│{RESET}")

    bottom_line = f"{border_color}╰{'─' * (w - 2)}╯{RESET}"

    return "\n".join([top_line] + body_lines + [bottom_line])


def print_box(
    title: str,
    lines: List[str],
    border_color: str = CYAN,
    badge: Optional[str] = None,
    width: Optional[int] = None,
):
    print(box(title=title, lines=lines, border_color=border_color, badge=badge, width=width))


def print_step_box(
    step_num: int,
    total_steps: int,
    title: str,
    lines: List[str],
    badge: str = "🚀",
    border_color: str = CYAN,
):
    header = f"{badge} Step {step_num}/{total_steps}: {title}"
    print("\n" + box(title=header, lines=lines, border_color=border_color))


def prompt_choice(question: str, default: str = "") -> str:
    """Styled terminal input prompt inside powerline accent."""
    prompt_str = f"\n{PINK}❯{RESET} {BOLD}{question}{RESET}"
    if default:
        prompt_str += f" {MUTED}[default: {default}]{RESET}"
    prompt_str += f": "
    try:
        ans = input(prompt_str).strip()
        return ans or default
    except (KeyboardInterrupt, EOFError):
        print(f"\n{MUTED}Aborted by user.{RESET}")
        sys.exit(0)


def print_success_box(title: str, lines: List[str]):
    """Displays celebrating green rounded box for passed sandboxes and deployments."""
    print("\n" + box(title=title, lines=lines, border_color=EMERALD, badge="🎉"))


def print_alert_box(title: str, lines: List[str], is_error: bool = False):
    """Displays warning or error alert box."""
    color = RED if is_error else AMBER
    badge = "❌" if is_error else "⚠️"
    print("\n" + box(title=title, lines=lines, border_color=color, badge=badge))
