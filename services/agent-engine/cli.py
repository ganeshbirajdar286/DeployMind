import argparse
import json
import os
import sys
from pathlib import Path
from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle
from agent.graph import run_devops_workflow
from agent.autonomous_loop import run_autonomous_pipeline


def main():
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

    args = parser.parse_args()

    if args.command == "auto":
        run_autonomous_pipeline(
            target=args.target,
            github_token=args.token or os.getenv("GITHUB_TOKEN"),
            branch_name=args.branch,
            max_iterations=args.retries,
        )
        return

    # For commands that require an existing local path:
    target_path = Path(args.path).resolve()
    if not target_path.exists() or not target_path.is_dir():
        print(f"Error: Directory not found: {target_path}", file=sys.stderr)
        sys.exit(1)

    if args.command == "inspect":
        print(f"\nScanning repository at: {target_path} ...\n")
        analysis = analyze_repository(target_path)
        if args.json:
            print(analysis.model_dump_json(indent=2))
        else:
            print("=" * 60)
            print(f" Repository: {analysis.repo_name}")
            print(f" Monorepo:   {'Yes' if analysis.is_monorepo else 'No'}")
            print("=" * 60)
            for idx, svc in enumerate(analysis.services, 1):
                target_prov = analysis.suggested_deployment_targets.get(svc.name, "Render")
                print(f"\n[Service {idx}: '{svc.name}']")
                print(f"  * Framework:          {svc.framework.value}")
                print(f"  * Language:           {svc.language.value}")
                print(f"  * Package Manager:    {svc.package_manager.value}")
                print(f"  * Exposed Port:       {svc.default_port}")
                print(f"  * Start Command:      {svc.start_command or 'None'}")
                print(f"  * Build Command:      {svc.build_command or 'None'}")
                print(f"  * Recommended Deploy: {target_prov.upper()}")
                if svc.env_vars_detected:
                    print(f"  * Detected Env Vars:  {', '.join(svc.env_vars_detected)}")

            if analysis.warnings:
                print("\n[Warnings]")
                for w in analysis.warnings:
                    print(f"  ! {w}")
            if analysis.missing_info:
                print("\n[Missing Info]")
                for m in analysis.missing_info:
                    print(f"  ? {m}")
            print("=" * 60 + "\n")

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
