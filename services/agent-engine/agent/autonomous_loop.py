import os
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any

from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle
from runner.secrets_manager import GitHubSecretsManager
from runner.git_pusher import GitPusher
from runner.github_runner import GitHubActionsRunner
from utils.diff_viewer import display_bundle_diffs
from .patcher import diagnose_and_patch


def run_autonomous_pipeline(
    target_path: Path,
    github_repo: Optional[str] = None,
    github_token: Optional[str] = None,
    branch_name: str = "ai-devops-setup",
    max_iterations: int = 3,
):
    """Coordinates the entire autonomous journey: Analysis -> Secrets -> Diff -> Push -> Self-Healing Cloud Tests."""
    target_path = Path(target_path).resolve()
    token = github_token or os.getenv("GITHUB_TOKEN")

    print("\n" + "=" * 60)
    print("🚀 AI DevOps Agent - Autonomous Cloud Automation")
    print("=" * 60)
    print(f"Local Directory:   {target_path}")
    if github_repo:
        print(f"Target GitHub Repo: {github_repo}")
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
        target = analysis.suggested_deployment_targets.get(s.name, "Render")
        primary_port = s.default_port
        primary_health_path = s.health_check_path
        print(f"    - '{s.name}': {s.framework.value} ({s.language.value}) -> Port {s.default_port} -> Deploy: {target.upper()}")
        all_env_vars.extend(s.env_vars_detected)

    all_env_vars = sorted(list(set(all_env_vars)))

    # 2. Interactive Secrets Configuration
    if all_env_vars and github_repo and token:
        print("\n[Step 2/5] Configuring Repository Secrets...")
        secrets_mgr = GitHubSecretsManager(token=token)
        secrets_mgr.interactive_configure_secrets(repo=github_repo, detected_vars=all_env_vars)
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
    if github_repo:
        confirm = input(f"Ready to write files and push branch '{branch_name}' to '{github_repo}'? [Y/n]: ").strip().lower()
    else:
        confirm = input(f"Ready to write these files into your local project '{target_path.name}'? [Y/n]: ").strip().lower()

    if confirm in ("n", "no"):
        print("\n[Aborted] No files written. Exiting cleanly.\n")
        return

    # Write files to disk
    print(f"\nWriting verified configuration files to {target_path} ...")
    for rel_path, content in artifacts.items():
        dest_file = target_path / rel_path
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        dest_file.write_text(content, encoding="utf-8")
    print("  [OK] All files written to disk.")

    if not github_repo or not token:
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
        github_repo=github_repo,
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
            repo=github_repo,
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
            print(f"👉 https://github.com/{github_repo}/pull/new/{branch_name}\n")
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
                github_repo=github_repo,
            )
            print("  * Patch pushed. Re-dispatching Cloud Sandbox...")
            time.sleep(3)
        else:
            print("\n" + "=" * 60)
            print("⚠️ Reached maximum 3 self-healing attempts.")
            print(f"Detailed logs available at: https://github.com/{github_repo}/actions")
            print("=" * 60 + "\n")
