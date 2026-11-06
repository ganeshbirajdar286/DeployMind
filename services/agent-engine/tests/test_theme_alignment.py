import pytest
from ai_devops_agent.utils.theme import str_width, box, CYAN, PINK, INDIGO, EMERALD, AMBER
from ai_devops_agent.utils.banner import get_terminal_banner


def test_str_width_accurately_measures_emojis_and_ansi():
    # Emojis in modern terminal fonts occupy 2 display columns
    assert str_width("🚀") == 2
    assert str_width("🛡️") == 2
    assert str_width("✏️") == 2
    assert str_width("🔍") == 2
    assert str_width("🛑") == 2
    assert str_width("⚡") == 2
    assert str_width("📁") == 2
    assert str_width("🎉") == 2
    assert str_width("⚠️") == 2
    assert str_width("❌") == 2
    assert str_width("🧪") == 2

    # Standard ASCII & text checkmark
    assert str_width("hello world") == 11
    assert str_width("✔") == 1

    # ANSI colored string true visible length
    colored = f"{CYAN}Target Branch: {PINK}ai-devops-setup\033[0m"
    assert str_width(colored) == len("Target Branch: ai-devops-setup")


def test_box_borders_align_with_mathematical_precision():
    test_boxes = [
        (
            "Human-in-the-Loop Review & Confirmation",
            [
                "Choose approval action:",
                "[Y] 🚀 Proceed and push branch 'ai-devops-setup' to 'ganeshbirajdar286/mojito-'",
                "[E] ✏️ Edit generated files in your editor (Dockerfile, Compose, etc.)",
                "[V] 🔍 View syntax-highlighted diffs again",
                "[N] 🛑 Abort and exit",
            ],
            "🛡️",
            PINK,
        ),
        (
            "Step 4/5: Promoting Dedicated Branch to GitHub",
            [
                "Target Branch:     ai-devops-setup",
                "Remote Repository: ganeshbirajdar286/mojito-",
            ],
            "🚀",
            INDIGO,
        ),
        (
            "Step 5/5: Cloud Sandbox Execution on GitHub Actions (Attempt 1/3)",
            [
                "Target Repo:       ganeshbirajdar286/mojito-",
                "Workflow File:     .github/workflows/ai-devops-verify.yml",
                "Target Branch:     ai-devops-setup",
                "Healthcheck Port:  80 (Path: /)",
                "Status:            Dispatched to GitHub Actions runner...",
            ],
            "🧪",
            INDIGO,
        ),
        (
            "100% VERIFIED SUCCESS! All Cloud Sandbox Checks Passed!",
            [
                "Branch 'ai-devops-setup' is fully verified with Docker Compose and Health Checks.",
                "✔ Docker Compose specification validated",
                "✔ Container built and booted successfully",
                "✔ Healthcheck probe accepted traffic with HTTP 200",
                "✔ Safe container teardown complete",
            ],
            "🎉",
            EMERALD,
        ),
    ]

    for title, lines, badge, color in test_boxes:
        rendered = box(title=title, lines=lines, badge=badge, border_color=color, width=84)
        rendered_lines = rendered.splitlines()
        widths = [str_width(line) for line in rendered_lines]
        
        # Every single line (top border, body lines, bottom border) must have the exact same terminal display width
        assert len(set(widths)) == 1, f"Mismatch in box '{title}': line widths = {widths}"


def test_banner_box_lines_align_uniformly():
    banner = get_terminal_banner(box_width=82)
    lines = banner.splitlines()
    widths = [str_width(line) for line in lines]
    assert len(set(widths)) == 1
    assert widths[0] == 82
