import argparse
import json
import sys
from pathlib import Path
from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle
from agent.graph import run_devops_workflow


def main():
    parser = argparse.ArgumentParser(
        description="AI DevOps Agent - Core Repository Analysis & DevOps Generator"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Inspect command
    inspect_parser = subparsers.add_parser("inspect", help="Inspect a repository and display analysis")
    inspect_parser.add_argument("path", type=str, help="Path to repository directory")
    inspect_parser.add_argument("--json", action="store_true", help="Output raw JSON")

    # Generate command
    gen_parser = subparsers.add_parser("generate", help="Generate Docker and CI/CD configuration")
    gen_parser.add_argument("path", type=str, help="Path to repository directory")
    gen_parser.add_argument("--out", type=str, default=None, help="Output directory (defaults to target repo)")
    gen_parser.add_argument("--dry-run", action="store_true", help="Print generated files without writing")

    # Run agent command (LangGraph full loop)
    run_parser = subparsers.add_parser("run", help="Run full LangGraph analysis, generation, and validation loop")
    run_parser.add_argument("path", type=str, help="Path to repository directory")
    run_parser.add_argument("--retries", type=int, default=2, help="Max AI repair attempts")

    args = parser.parse_args()
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
                target = analysis.suggested_deployment_targets.get(svc.name, "Render")
                print(f"\n[Service {idx}: '{svc.name}']")
                print(f"  * Framework:          {svc.framework.value}")
                print(f"  * Language:           {svc.language.value}")
                print(f"  * Package Manager:    {svc.package_manager.value}")
                print(f"  * Exposed Port:       {svc.default_port}")
                print(f"  * Start Command:      {svc.start_command or 'None'}")
                print(f"  * Build Command:      {svc.build_command or 'None'}")
                print(f"  * Recommended Deploy: {target.upper()}")
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
        print("  AI DevOps Agent - Autonomous LangGraph Workflow")
        print("=" * 60)
        print(f"Target: {target_path}")
        print(f"Max AI Repairs: {args.retries}\n")

        final_state = run_devops_workflow(
            repo_path=str(target_path),
            max_retries=args.retries,
        )

        print("\n" + "=" * 60)
        print(f" Final Stage:     {final_state.get('current_stage')}")
        print(f" Build Verified:  {'YES' if final_state.get('build_succeeded') else 'NO'}")
        print(f" Retries Used:    {final_state.get('retry_count')}")
        print(f" Status Message:  {final_state.get('status_message')}")
        print("=" * 60)

        if final_state.get("patch_history"):
            print("\n[AI Patch History]")
            for patch in final_state["patch_history"]:
                print(f"  Attempt {patch['attempt']}:")
                print(f"    - Diagnosis:   {patch['diagnosis']}")
                print(f"    - Fix Applied: {patch['patch_explanation']}")

        artifacts = final_state.get("artifacts", {})
        print(f"\nGenerated Artifacts ({len(artifacts)}):")
        for key in artifacts.keys():
            print(f"  - {key}")
        print()


if __name__ == "__main__":
    main()
