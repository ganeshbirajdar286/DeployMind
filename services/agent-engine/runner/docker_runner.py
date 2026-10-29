import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class BuildResult:
    success: bool
    exit_code: int
    logs: List[str] = field(default_factory=list)
    error_tail: str = ""
    duration_seconds: float = 0.0
    is_simulated: bool = False


class DockerRunner:
    """Executes Docker builds or performs static validation if Docker daemon is not available."""

    def __init__(self, timeout_seconds: int = 300):
        self.timeout_seconds = timeout_seconds
        self.docker_path = shutil.which("docker")

    def is_docker_available(self) -> bool:
        if not self.docker_path:
            return False
        try:
            res = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=5,
            )
            return res.returncode == 0
        except Exception:
            return False

    def build_image(
        self,
        context_path: Path,
        dockerfile_path: Optional[Path] = None,
        tag: str = "agent-test-image:latest",
    ) -> BuildResult:
        context_path = Path(context_path).resolve()
        if dockerfile_path is None:
            dockerfile_path = context_path / "Dockerfile"

        start_time = time.time()
        logs: List[str] = []

        # If Docker is not running or available on this system, perform strict static validation
        if not self.is_docker_available():
            return self._static_validate(context_path, dockerfile_path, start_time)

        # Real Docker build execution
        cmd = [
            "docker", "build",
            "-t", tag,
            "-f", str(dockerfile_path),
            str(context_path),
        ]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in process.stdout:
                clean_line = line.rstrip()
                logs.append(clean_line)

            process.wait(timeout=self.timeout_seconds)
            exit_code = process.returncode
            duration = round(time.time() - start_time, 2)
            success = (exit_code == 0)

            error_tail = ""
            if not success:
                error_tail = "\n".join(logs[-40:])

            return BuildResult(
                success=success,
                exit_code=exit_code,
                logs=logs,
                error_tail=error_tail,
                duration_seconds=duration,
                is_simulated=False,
            )

        except subprocess.TimeoutExpired:
            process.kill()
            duration = round(time.time() - start_time, 2)
            return BuildResult(
                success=False,
                exit_code=-1,
                logs=logs + [f"Build timed out after {self.timeout_seconds} seconds."],
                error_tail=f"Build timed out after {self.timeout_seconds} seconds.",
                duration_seconds=duration,
                is_simulated=False,
            )
        except Exception as e:
            duration = round(time.time() - start_time, 2)
            return BuildResult(
                success=False,
                exit_code=1,
                logs=logs + [f"Execution error: {str(e)}"],
                error_tail=str(e),
                duration_seconds=duration,
                is_simulated=False,
            )

    def _static_validate(
        self, context_path: Path, dockerfile_path: Path, start_time: float
    ) -> BuildResult:
        """Fallback static analysis when Docker daemon is not active."""
        logs = [
            "[DockerRunner] Notice: Docker daemon is not running locally.",
            "[DockerRunner] Executing static syntax and file reference validation...",
        ]

        if not dockerfile_path.exists():
            duration = round(time.time() - start_time, 2)
            logs.append(f"ERROR: Dockerfile not found at {dockerfile_path}")
            return BuildResult(
                success=False,
                exit_code=1,
                logs=logs,
                error_tail=f"Dockerfile not found at {dockerfile_path}",
                duration_seconds=duration,
                is_simulated=True,
            )

        content = dockerfile_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()

        has_from = False
        has_expose = False
        has_cmd_or_entrypoint = False

        for idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("FROM "):
                has_from = True
            elif stripped.startswith("EXPOSE "):
                has_expose = True
            elif stripped.startswith("CMD ") or stripped.startswith("ENTRYPOINT "):
                has_cmd_or_entrypoint = True

            # Check COPY instructions to ensure source files exist
            if stripped.startswith("COPY "):
                parts = stripped.split()
                if len(parts) >= 3 and not parts[1].startswith("--"):
                    src = parts[1]
                    # ignore wildcard or glob paths
                    if "*" not in src and not (context_path / src).exists():
                        logs.append(f"WARNING: COPY source '{src}' does not exist on disk in {context_path.name}")

        logs.append(f"-> Syntax check: FROM statement: {'[OK]' if has_from else '[FAIL]'}")
        logs.append(f"-> Syntax check: EXPOSE port: {'[OK]' if has_expose else '[FAIL]'}")
        logs.append(f"-> Syntax check: CMD/ENTRYPOINT: {'[OK]' if has_cmd_or_entrypoint else '[FAIL]'}")

        is_valid = has_from and has_cmd_or_entrypoint
        duration = round(time.time() - start_time, 2)

        if is_valid:
            logs.append("[DockerRunner] Static validation PASSED successfully.")
            return BuildResult(
                success=True,
                exit_code=0,
                logs=logs,
                error_tail="",
                duration_seconds=duration,
                is_simulated=True,
            )
        else:
            err = "Dockerfile is missing mandatory instructions (FROM or CMD/ENTRYPOINT)."
            logs.append(f"ERROR: {err}")
            return BuildResult(
                success=False,
                exit_code=1,
                logs=logs,
                error_tail=err,
                duration_seconds=duration,
                is_simulated=True,
            )
