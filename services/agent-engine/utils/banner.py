import sys
import os
from utils.theme import str_width

# Ensure Windows terminal standard output uses UTF-8
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 24-bit TrueColor ANSI Escape Sequences
C1 = "\033[38;2;0;235;255m"     # Neon Cyan
C2 = "\033[38;2;40;160;255m"    # Sky Blue
C3 = "\033[38;2;70;110;255m"    # Royal Blue
C4 = "\033[38;2;140;60;255m"    # Deep Violet
C5 = "\033[38;2;190;70;240m"    # Neon Purple
C6 = "\033[38;2;120;40;180m"    # Shadow Violet
BORDER = "\033[38;2;0;180;216m"
SUBTITLE = "\033[38;2;220;230;250m"
PROMPT_CYAN = "\033[38;2;0;229;255m"
BOLD = "\033[1m"
RESET = "\033[0m"

# 3D Isometric ANSI Shadow art for 'AI DEVOPS'
BANNER_LINES = [
    r" █████╗ ██╗    ██████╗ ███████╗██╗   ██╗ ██████╗ ██████╗ ███████╗",
    r"██╔══██╗██║    ██╔══██╗██╔════╝██║   ██║██╔═══██╗██╔══██╗██╔════╝",
    r"███████║██║    ██║  ██║█████╗  ██║   ██║██║   ██║██████╔╝███████╗",
    r"██╔══██║██║    ██║  ██║██╔══╝  ╚██╗ ██╔╝██║   ██║██╔═══╝ ╚════██║",
    r"██║  ██║██║    ██████╔╝███████╗ ╚████╔╝ ╚██████╔╝██║     ███████║",
    r"╚═╝  ╚═╝╚═╝    ╚═════╝ ╚══════╝  ╚═══╝   ╚═════╝ ╚═╝     ╚══════╝",
]

GRADIENT_COLORS = [C1, C2, C3, C4, C5, C6]


def get_terminal_banner(box_width: int = 82) -> str:
    """Returns the formatted 3D gradient terminal banner inside a rounded frame."""
    inner_width = box_width - 2
    out = []

    # Top border
    out.append(f"{BORDER}╭{'─' * inner_width}╮{RESET}")
    out.append(f"{BORDER}│{' ' * inner_width}│{RESET}")

    # Gradient ASCII Typography
    for line, color in zip(BANNER_LINES, GRADIENT_COLORS):
        padded = line.center(inner_width)
        out.append(f"{BORDER}│{RESET}{color}{padded}{RESET}{BORDER}│{RESET}")

    out.append(f"{BORDER}│{' ' * inner_width}│{RESET}")

    # Subtitle with emojis
    sub_text = "🤖  Autonomous Cloud & DevOps Automation Platform  🚀"
    vis_sub_len = str_width(sub_text)
    pad_total = max(0, inner_width - vis_sub_len)
    pad_left = pad_total // 2
    pad_right = pad_total - pad_left
    out.append(f"{BORDER}│{RESET}{' ' * pad_left}{SUBTITLE}{BOLD}{sub_text}{RESET}{' ' * pad_right}{BORDER}│{RESET}")

    out.append(f"{BORDER}│{' ' * inner_width}│{RESET}")

    # Bottom border
    out.append(f"{BORDER}╰{'─' * inner_width}╯{RESET}")

    return "\n".join(out)


def print_banner(box_width: int = 82):
    """Prints the styled terminal banner to stdout."""
    print(get_terminal_banner(box_width=box_width))


def prompt_for_target() -> str:
    """Displays the custom cyan prompt matching the user reference image."""
    try:
        val = input(f"\n{PROMPT_CYAN}Enter GitHub URL or local file path:{RESET} ").strip()
        return val
    except (KeyboardInterrupt, EOFError):
        print(f"\n{RESET}Exiting.")
        sys.exit(0)
