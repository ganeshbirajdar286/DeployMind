import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

from analyzer.detector import analyze_repository
from generator.engine import generate_devops_bundle
from runner.docker_runner import DockerRunner
from .state import AgentState, PatchRecord
from .patcher import diagnose_and_patch


def analyze_and_generate_node(state: AgentState) -> Dict[str, Any]:
    """Node 1: Deterministically inspects repo and produces initial DevOps bundle."""
    repo_path = Path(state["repo_path"]).resolve()

    analysis = analyze_repository(repo_path)
    bundle = generate_devops_bundle(analysis)

    return {
        "analysis": analysis.model_dump(),
        "artifacts": bundle,
        "current_stage": "VALIDATING_DOCKER",
        "status_message": f"Detected {len(analysis.services)} service(s). Generated {len(bundle)} configuration files.",
    }


def validate_build_node(state: AgentState) -> Dict[str, Any]:
    """Node 2: Runs Docker build / syntax verification on Dockerfile."""
    repo_path = Path(state["repo_path"]).resolve()
    artifacts = state.get("artifacts", {})

    dockerfile_content = artifacts.get("Dockerfile")
    if not dockerfile_content:
        for path_key, content in artifacts.items():
            if path_key.endswith("Dockerfile"):
                dockerfile_content = content
                break

    runner = DockerRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        temp_dockerfile = temp_path / "Dockerfile"
        temp_dockerfile.write_text(dockerfile_content or "", encoding="utf-8")

        result = runner.build_image(
            context_path=repo_path,
            dockerfile_path=temp_dockerfile,
            tag=f"agent-verify-{Path(repo_path).name.lower()}:latest",
        )

    if result.success:
        return {
            "build_succeeded": True,
            "current_stage": "VALIDATING_COMPOSE",
            "build_logs": result.logs,
            "error_tail": "",
            "status_message": "Dockerfile validation passed. Starting Docker Compose verification...",
        }
    else:
        return {
            "build_succeeded": False,
            "current_stage": "REPAIRING" if state.get("retry_count", 0) < state.get("max_retries", 2) else "FAILED",
            "build_logs": result.logs,
            "error_tail": result.error_tail,
            "status_message": f"Dockerfile check failed: {result.error_tail[:120] if result.error_tail else 'Build error'}",
        }


def validate_compose_node(state: AgentState) -> Dict[str, Any]:
    """Node 3: Validates docker-compose.yml: boots containers, probes ports, and tears down."""
    repo_path = Path(state["repo_path"]).resolve()
    artifacts = state.get("artifacts", {})
    compose_content = artifacts.get("docker-compose.yml")

    if not compose_content:
        return {
            "compose_succeeded": False,
            "compose_logs": ["ERROR: No docker-compose.yml found in artifacts."],
            "current_stage": "FAILED",
            "status_message": "No docker-compose.yml found.",
        }

    # Extract exposed ports to probe
    ports_to_probe: List[int] = []
    analysis = state.get("analysis")
    if analysis and "services" in analysis:
        for svc in analysis["services"]:
            port = svc.get("default_port")
            if port and isinstance(port, int):
                ports_to_probe.append(port)

    runner = DockerRunner()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        # Write docker-compose.yml
        temp_compose = temp_path / "docker-compose.yml"
        temp_compose.write_text(compose_content, encoding="utf-8")

        # Also write the Dockerfile into context so compose can find it
        dockerfile_content = artifacts.get("Dockerfile")
        if dockerfile_content:
            (temp_path / "Dockerfile").write_text(dockerfile_content, encoding="utf-8")

        result = runner.validate_compose(
            compose_file_path=temp_compose,
            ports_to_probe=ports_to_probe,
        )

    if result.success:
        return {
            "compose_succeeded": True,
            "current_stage": "SUCCESS",
            "compose_logs": result.logs,
            "status_message": f"All validations PASSED: Dockerfile & Docker Compose verified! ({result.status_summary})",
        }
    else:
        return {
            "compose_succeeded": False,
            "current_stage": "REPAIRING" if state.get("retry_count", 0) < state.get("max_retries", 2) else "FAILED",
            "compose_logs": result.logs,
            "error_tail": result.error_tail,
            "status_message": f"Docker Compose check failed: {result.status_summary}",
        }


def repair_patch_node(state: AgentState) -> Dict[str, Any]:
    """Node 4: Subgraph-powered AI repair for Dockerfile and Compose failures."""
    artifacts = dict(state.get("artifacts", {}))
    retry_count = state.get("retry_count", 0) + 1
    patch_history = list(state.get("patch_history", []))

    dockerfile_key = "Dockerfile"
    current_dockerfile = artifacts.get(dockerfile_key, "")

    framework = "unknown"
    analysis_data = state.get("analysis")
    if analysis_data and analysis_data.get("services"):
        framework = analysis_data["services"][0].get("framework", "unknown")

    patch_result = diagnose_and_patch(
        current_dockerfile=current_dockerfile,
        error_tail=state.get("error_tail", ""),
        framework=framework,
    )

    artifacts[dockerfile_key] = patch_result.updated_dockerfile

    record: PatchRecord = {
        "attempt": retry_count,
        "diagnosis": patch_result.diagnosis,
        "target_file": dockerfile_key,
        "patch_explanation": patch_result.patch_explanation,
        "applied_at": datetime.now(timezone.utc).isoformat(),
    }
    patch_history.append(record)

    return {
        "artifacts": artifacts,
        "retry_count": retry_count,
        "patch_history": patch_history,
        "current_stage": "VALIDATING_DOCKER",
        "status_message": f"Applied AI patch (Attempt {retry_count}): {patch_result.patch_explanation}",
    }
