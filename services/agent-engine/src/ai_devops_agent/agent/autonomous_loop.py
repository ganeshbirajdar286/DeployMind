import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.generator.engine import generate_devops_bundle
from ai_devops_agent.runner.secrets_manager import GitHubSecretsManager
from ai_devops_agent.runner.git_pusher import GitPusher
from ai_devops_agent.runner.github_runner import GitHubActionsRunner
from ai_devops_agent.utils.diff_viewer import display_bundle_diffs
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
    max_iterations: int = 3,
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
                print("\n" + "=" * 60)
                print("⚠️ Reached maximum 3 self-healing attempts.")
                print(f"Detailed logs available at: https://github.com/{detected_repo}/actions")
                print("=" * 60 + "\n")

    finally:
        if temp_workspace:
            temp_workspace.cleanup()
