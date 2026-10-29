import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle
from runner.secrets_manager import GitHubSecretsManager
from runner.git_pusher import GitPusher
from runner.github_runner import GitHubActionsRunner
from utils.diff_viewer import display_bundle_diffs
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


def resolve_target_workspace(
    target: str, token: Optional[str] = None
) -> Tuple[Path, Optional[str], Optional[tempfile.TemporaryDirectory]]:
    """Resolves target: if it's a GitHub URL or slug, clones it into a temp workspace."""
    github_slug = parse_github_slug(target)

    if github_slug:
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
            print(f"ERROR: Failed to clone {github_slug}: {res.stderr}")
            sys.exit(1)

        print("[Auto-Clone] Repository cloned successfully.\n")
        return work_path, github_slug, temp_dir

    local_path = Path(target).resolve()
    if not local_path.exists() or not local_path.is_dir():
        print(f"Error: Target path not found: {target}", file=sys.stderr)
        sys.exit(1)

    return local_path, None, None


def run_autonomous_pipeline(
    target: str,
    github_token: Optional[str] = None,
    branch_name: str = "ai-devops-setup",
    max_iterations: int = 3,
):
    """Coordinates the entire autonomous journey: GitHub URL -> Clone -> Analysis -> Secrets -> Diff -> Push -> Self-Healing."""
    token = github_token or os.getenv("GITHUB_TOKEN")
    target_path, detected_repo, temp_workspace = resolve_target_workspace(target, token=token)

    try:
        print("=" * 60)
        print("🚀 AI DevOps Agent - Autonomous Cloud Automation")
        print("=" * 60)
        if detected_repo:
            print(f"Target GitHub Repo: {detected_repo}")
        print(f"Local Workspace:   {target_path}")
        print(f"Setup Branch:      {branch_name}")
        print(f"Max Self-Healings: {max_iterations}")
        print("=" * 60)

        # 1. Deterministic Analysis
        print("\n[Step 1/5] Analyzing repository structure and dependencies...")
        analysis = analyze_repository(target_path)
        print(f"  * Detected {len(analysis.services)} service(s):")
        all_env_vars = []
        primary_port = 8080
        primary_health_path = "/"

        for s in analysis.services:
            target_prov = analysis.suggested_deployment_targets.get(s.name, "Render")
            primary_port = s.default_port
            primary_health_path = s.health_check_path
            print(f"    - '{s.name}': {s.framework.value} ({s.language.value}) -> Port {s.default_port} -> Deploy: {target_prov.upper()}")
            all_env_vars.extend(s.env_vars_detected)

        all_env_vars = sorted(list(set(all_env_vars)))

        # 2. Interactive Secrets Configuration
        if all_env_vars and detected_repo and token:
            print("\n[Step 2/5] Configuring Repository Secrets...")
            secrets_mgr = GitHubSecretsManager(token=token)
            secrets_mgr.interactive_configure_secrets(repo=detected_repo, detected_vars=all_env_vars)
        else:
            print("\n[Step 2/5] Checking Environment Variables...")
            if all_env_vars:
                print(f"  Found {len(all_env_vars)} variable reference(s): {', '.join(all_env_vars[:4])}")

        # 3. Generate DevOps Configuration Bundle
        print("\n[Step 3/5] Generating DevOps configuration files...")
        artifacts = generate_devops_bundle(analysis)
        print(f"  Generated {len(artifacts)} configuration files.")

        # 4. Display Syntax-Highlighted Terminal Diffs
        display_bundle_diffs(target_path, artifacts)

        # 5. Human-in-the-Loop Approval
        print("=" * 60)
        print("  Human-in-the-Loop Confirmation")
        print("=" * 60)
        prompt_text = f"Ready to write files and push branch '{branch_name}' to '{detected_repo}'? [Y/n]: " if detected_repo else f"Ready to write files into '{target_path.name}'? [Y/n]: "
        confirm = input(prompt_text).strip().lower()

        if confirm in ("n", "no"):
            print("\n[Aborted] No files written. Exiting cleanly.\n")
            return

        # Write files to workspace
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
        print(f"\n[Step 4/5] Pushing dedicated branch '{branch_name}' to GitHub...")
        pusher = GitPusher(token=token)
        pushed, push_msg = pusher.push_branch(
            repo_dir=target_path,
            branch_name=branch_name,
            commit_message="feat(devops): automated containerization, docker-compose, and CI/CD setup",
            github_repo=detected_repo,
        )

        if not pushed:
            print(f"  [ERROR] Could not push to GitHub: {push_msg}")
            return
        print(f"  [OK] {push_msg}")

        # 6. Autonomous Cloud Verification & Self-Healing Loop (Max iterations)
        print(f"\n[Step 5/5] Launching Cloud Sandbox on GitHub Actions (Max {max_iterations} iterations)...")
        runner = GitHubActionsRunner(token=token)
        current_artifacts = dict(artifacts)

        for iteration in range(1, max_iterations + 1):
            print("\n" + "-" * 60)
            print(f"  Cloud Sandbox Execution - Iteration {iteration}/{max_iterations}")
            print("-" * 60)

            result = runner.dispatch_and_wait(
                repo=detected_repo,
                branch=branch_name,
                workflow_file="ai-devops-verify.yml",
                health_port=primary_port,
                health_path=primary_health_path,
            )

            for log_line in result.logs:
                print(f"  {log_line}")

            if result.success:
                print("\n" + "=" * 60)
                print("🎉 100% VERIFIED SUCCESS! All Cloud Sandbox Checks Passed!")
                print("=" * 60)
                print(f"Branch '{branch_name}' is fully verified with Docker Compose and Health Checks.")
                print(f"\nOpen your Pull Request now on GitHub:")
                print(f"👉 https://github.com/{detected_repo}/pull/new/{branch_name}\n")
                return

            # Handle failure & Self-Healing
            if iteration < max_iterations:
                print(f"\n[AI Self-Healing]: Cloud build failed on attempt {iteration}. Diagnosing failure...")
                patch_result = diagnose_and_patch(
                    current_dockerfile=current_artifacts.get("Dockerfile", ""),
                    error_tail=result.error_tail or "\n".join(result.logs[-20:]),
                    framework=analysis.services[0].framework.value if analysis.services else "unknown",
                )

                print(f"  * Diagnosis:   {patch_result.diagnosis}")
                print(f"  * AI Patch:    {patch_result.patch_explanation}")

                # Apply patch to disk
                (target_path / "Dockerfile").write_text(patch_result.updated_dockerfile, encoding="utf-8")
                current_artifacts["Dockerfile"] = patch_result.updated_dockerfile

                # Push patch to GitHub branch
                print(f"  * Pushing AI patch to '{branch_name}' on GitHub...")
                pusher.push_branch(
                    repo_dir=target_path,
                    branch_name=branch_name,
                    commit_message=f"fix(devops): apply AI self-healing patch (Attempt {iteration})",
                    github_repo=detected_repo,
                )
                print("  * Patch pushed. Re-dispatching Cloud Sandbox...")
                time.sleep(3)
            else:
                print("\n" + "=" * 60)
                print("⚠️ Reached maximum 3 self-healing attempts.")
                print(f"Detailed logs available at: https://github.com/{detected_repo}/actions")
                print("=" * 60 + "\n")

    finally:
        if temp_workspace:
            temp_workspace.cleanup()
