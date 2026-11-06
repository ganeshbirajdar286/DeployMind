import argparse
import json
import os
import sys
from pathlib import Path
from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle
from agent.graph import run_devops_workflow
from agent.autonomous_loop import run_autonomous_pipeline
from utils.banner import print_banner, prompt_for_target
from utils.theme import (
    print_box,
    print_powerline_header,
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


def main():
    print_banner()

    # Interactive direct launch when called without arguments: e.g. `uv run python cli.py`
    if len(sys.argv) == 1:
        target = prompt_for_target()
        if not target:
            print("No repository provided. Exiting.")
            return

        token = os.getenv("GITHUB_TOKEN")
        if "github.com" in target and not token:
            try:
                entered_token = input("\033[38;2;160;174;192mEnter GitHub Token (optional, press Enter to skip):\033[0m ").strip()
                token = entered_token or None
            except (KeyboardInterrupt, EOFError):
                print()
                return

        run_autonomous_pipeline(
            target=target,
            github_token=token,
            branch_name="ai-devops-setup",
            max_iterations=3,
            repo=None,
        )
        return

    parser = argparse.ArgumentParser(
        description="AI DevOps Agent - Autonomous Cloud & Local DevOps Automation Platform"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. Autonomous End-to-End Command (Takes GitHub URL or Local Path)
    auto_parser = subparsers.add_parser(
        "auto",
        help="Run autonomous DevOps pipeline on a GitHub URL or local repo",
    )
    auto_parser.add_argument("target", type=str, help="GitHub URL (https://github.com/owner/repo), slug (owner/repo), or local path")
    auto_parser.add_argument("--repo", type=str, default=None, help="GitHub repository (owner/repo) if target is a local path")
    auto_parser.add_argument("--branch", type=str, default="ai-devops-setup", help="Dedicated branch name to push")
    auto_parser.add_argument("--token", type=str, default=None, help="GitHub Personal Access Token")
    auto_parser.add_argument("--retries", type=int, default=3, help="Max self-healing iterations (default: 3)")

    # 2. Inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a repository and display analysis")
    inspect_parser.add_argument("path", type=str, help="Path to repository directory")
    inspect_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # 3. Generate command
    gen_parser = subparsers.add_parser("generate", help="Generate Docker and CI/CD configuration")
    gen_parser.add_argument("path", type=str, help="Path to repository directory")
    gen_parser.add_argument("--out", type=str, default=None, help="Output directory (defaults to target repo)")
    gen_parser.add_argument("--dry-run", action="store_true", help="Print generated files without writing")

    # 4. Run agent command (LangGraph full loop)
    run_parser = subparsers.add_parser("run", help="Run LangGraph analysis, Dockerfile, and Compose verification")
    run_parser.add_argument("path", type=str, help="Path to repository directory")
    run_parser.add_argument("--retries", type=int, default=2, help="Max AI repair attempts")
    run_parser.add_argument("--sandbox", choices=["auto", "local", "github"], default="auto", help="Verification sandbox")
    run_parser.add_argument("--repo", type=str, default=None, help="GitHub repository (owner/repo) for cloud sandbox")
    run_parser.add_argument("--branch", type=str, default="main", help="Target branch for cloud sandbox")
    run_parser.add_argument("--token", type=str, default=None, help="GitHub Personal Access Token")
    run_parser.add_argument("--write", action="store_true", help="Automatically write verified files into target repo upon SUCCESS")

    # 5. Cloud Deployment Command (Frontend -> Vercel, Backend -> Render)
    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Deploy verified services to the cloud (Frontend to Vercel, Backend to Render)",
    )
    deploy_parser.add_argument("target", type=str, help="GitHub URL (https://github.com/owner/repo), slug, or local path")
    deploy_parser.add_argument("--repo", type=str, default=None, help="GitHub repository (owner/repo) if target is a local path")
    deploy_parser.add_argument("--branch", type=str, default="main", help="Dedicated branch name to deploy (default: main)")
    deploy_parser.add_argument("--vercel-token", type=str, default=None, help="Vercel Access Token (or set VERCEL_TOKEN env var)")
    deploy_parser.add_argument("--render-token", type=str, default=None, help="Render API Key (or set RENDER_API_KEY env var)")
    deploy_parser.add_argument("--token", type=str, default=None, help="GitHub Personal Access Token")

    args = parser.parse_args()

    if args.command == "auto":
        run_autonomous_pipeline(
            target=args.target,
            github_token=args.token or os.getenv("GITHUB_TOKEN"),
            branch_name=args.branch,
            max_iterations=args.retries,
            repo=args.repo,
        )
        return

    if args.command == "deploy":
        import shutil
        from agent.autonomous_loop import resolve_target_workspace
        from deployer import CloudDeployer, interactive_cloud_deployment

        token = args.token or os.getenv("GITHUB_TOKEN")
        target_path, detected_repo, temp_workspace = resolve_target_workspace(args.target, token=token, repo=args.repo)
        try:
            analysis = analyze_repository(target_path)
            # If tokens provided via flags, inject into environment
            if args.vercel_token:
                os.environ["VERCEL_TOKEN"] = args.vercel_token
            if args.render_token:
                os.environ["RENDER_API_KEY"] = args.render_token

            interactive_cloud_deployment(
                analysis=analysis,
                detected_repo=detected_repo or args.target,
                branch_name=args.branch,
                local_env_dir=target_path,
            )
        finally:
            if temp_workspace:
                shutil.rmtree(temp_workspace, ignore_errors=True)
        return

    # For commands that require an existing local path:
    target_path = Path(args.path).resolve()
    if not target_path.exists() or not target_path.is_dir():
        print(f"Error: Directory not found: {target_path}", file=sys.stderr)
        sys.exit(1)

    if args.command == "inspect":
        analysis = analyze_repository(target_path)
        if args.json:
            print(analysis.model_dump_json(indent=2))
        else:
            print_powerline_header(analysis.repo_name, "local", "Inspect")
            box_lines = [
                f"{MUTED}Repository: {RESET} {BOLD}{WHITE}{analysis.repo_name}{RESET}  {MUTED}(Monorepo: {'Yes' if analysis.is_monorepo else 'No'}){RESET}",
                f"{MUTED}Location:   {RESET} {CYAN}{target_path}{RESET}",
                f"{MUTED}Services:   {RESET} {len(analysis.services)} service(s) identified",
                "",
                f"{BOLD}{WHITE}Detected Services:{RESET}",
            ]
            for idx, svc in enumerate(analysis.services, 1):
                target_prov = analysis.suggested_deployment_targets.get(svc.name, "Render")
                box_lines.append(
                    f"  {CYAN}[{idx}]{RESET} {BOLD}{WHITE}{svc.name}{RESET} {MUTED}({svc.framework.value}, {svc.language.value}){RESET}"
                )
                box_lines.append(
                    f"      Port: {EMERALD}{svc.default_port}{RESET} | Package: {MUTED}{svc.package_manager.value}{RESET} | Deploy Target: {PURPLE}{target_prov.upper()}{RESET}"
                )
                if svc.env_vars_detected:
                    box_lines.append(f"      Env Vars: {DIM}{', '.join(svc.env_vars_detected[:5])}{'...' if len(svc.env_vars_detected) > 5 else ''}{RESET}")

            if analysis.warnings:
                box_lines.extend(["", f"{AMBER}⚠️  Warnings:{RESET}"])
                for w in analysis.warnings:
                    box_lines.append(f"  {AMBER}!{RESET} {MUTED}{w}{RESET}")

            print_box(
                title=f"🔍 Repository Analysis: {analysis.repo_name}",
                lines=box_lines,
                border_color=CYAN,
            )
            print()

    elif args.command == "generate":
        print(f"\nScanning repository at: {target_path} ...\n")
        analysis = analyze_repository(target_path)
        artifacts = generate_devops_bundle(analysis)
        out_dir = Path(args.out).resolve() if args.out else target_path

        print(f"Generated {len(artifacts)} DevOps configuration files:")
        for rel_path, content in artifacts.items():
            print(f"  -> {rel_path}")

        if args.dry_run:
            print("\n[DRY RUN: No files written to disk]\n")
        else:
            for rel_path, content in artifacts.items():
                dest_file = out_dir / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_text(content, encoding="utf-8")
            print(f"\nSuccessfully written all configuration files to: {out_dir}\n")

    elif args.command == "run":
        print("=" * 60)
        print("  AI DevOps Agent - Autonomous LangGraph Verification Workflow")
        print("=" * 60)
        print(f"Target Path:    {target_path}")
        print(f"Sandbox Mode:   {args.sandbox.upper()}")
        if args.repo:
            print(f"Cloud Repo:     {args.repo} (Branch: {args.branch})")
        print(f"Max AI Repairs: {args.retries}\n")

        final_state = run_devops_workflow(
            repo_path=str(target_path),
            max_retries=args.retries,
            sandbox_mode=args.sandbox,
            github_repo=args.repo,
            github_branch=args.branch,
            github_token=args.token or os.getenv("GITHUB_TOKEN"),
        )

        print("\n" + "=" * 60)
        print(f" Final Stage:            {final_state.get('current_stage')}")
        print(f" Dockerfile Build Check: {'PASSED' if final_state.get('build_succeeded') else 'FAILED'}")
        print(f" Docker Compose Check:   {'PASSED' if final_state.get('compose_succeeded') else 'FAILED'}")
        print(f" AI Repairs Applied:     {final_state.get('retry_count')}")
        print(f" Verification Status:    {final_state.get('status_message')}")
        print("=" * 60)

        if final_state.get("compose_logs"):
            print("\n[Verification Execution Report]")
            for log_line in final_state["compose_logs"]:
                print(f"  {log_line}")

        if final_state.get("patch_history"):
            print("\n[AI Patch History]")
            for patch in final_state["patch_history"]:
                print(f"  Attempt {patch['attempt']}:")
                print(f"    - Diagnosis:   {patch['diagnosis']}")
                print(f"    - Fix Applied: {patch['patch_explanation']}")

        artifacts = final_state.get("artifacts", {})
        print(f"\nVerified Artifacts ({len(artifacts)}):")
        for key in artifacts.keys():
            print(f"  - {key}")

        if args.write:
            if final_state.get("build_succeeded", False) or final_state.get("current_stage") == "SUCCESS":
                print(f"\nWriting verified files to {target_path} ...")
                for rel_path, content in artifacts.items():
                    dest_file = target_path / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    dest_file.write_text(content, encoding="utf-8")
                print(f"Successfully saved all {len(artifacts)} verified DevOps files into your project!\n")
            else:
                print("\n[Skipped Writing]: Verification did not pass. Files were not written.\n")
        else:
            print(f"\n(Note: Files kept in memory. To write them to disk, pass '--write' or run 'generate')\n")


if __name__ == "__main__":
    main()
