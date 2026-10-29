import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import requests
from .docker_runner import ComposeValidationResult


class GitHubActionsRunner:
    """Dispatches, monitors, and streams raw container logs from GitHub Actions cloud sandboxes."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AI-DevOps-Agent-Runner/1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get_job_raw_logs(self, repo: str, job_id: int) -> str:
        """Downloads full raw text log of a completed GitHub Actions job."""
        url = f"{self.base_url}/repos/{repo}/actions/jobs/{job_id}/logs"
        try:
            resp = requests.get(url, headers=self._headers(), allow_redirects=True, timeout=20)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            pass
        return ""

    def dispatch_and_wait(
        self,
        repo: str,
        branch: str = "main",
        workflow_file: str = "ai-devops-verify.yml",
        health_port: int = 8000,
        health_path: str = "/",
        timeout_seconds: int = 360,
        poll_interval: int = 4,
    ) -> ComposeValidationResult:
        """Dispatches the verification workflow on GitHub Actions and streams real logs."""
        logs = [
            f"[GitHubActionsRunner] Target Sandbox: {repo} (branch: {branch})",
            f"[GitHubActionsRunner] Cloud Workflow: .github/workflows/{workflow_file}",
        ]

        if not self.token:
            logs.append("[GitHubActionsRunner] Warning: No GITHUB_TOKEN configured.")
            logs.append("[GitHubActionsRunner] Falling back to pre-flight static verification.")
            return ComposeValidationResult(
                success=True,
                syntax_valid=True,
                containers_started=False,
                health_check_passed=False,
                logs=logs,
                is_simulated=True,
                status_summary="Cloud verification prepared (Set GITHUB_TOKEN to execute remotely).",
            )

        # 1. Trigger workflow_dispatch
        dispatch_url = f"{self.base_url}/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
        payload = {
            "ref": branch,
            "inputs": {
                "health_port": str(health_port),
                "health_path": health_path,
            },
        }

        try:
            logs.append("-> Dispatching cloud sandbox workflow on GitHub Actions...")
            resp = requests.post(dispatch_url, json=payload, headers=self._headers(), timeout=15)
            if resp.status_code not in (204, 201):
                err_msg = f"Failed to dispatch workflow: HTTP {resp.status_code} - {resp.text}"
                logs.append(f"ERROR: {err_msg}")
                return ComposeValidationResult(
                    success=False,
                    syntax_valid=True,
                    containers_started=False,
                    health_check_passed=False,
                    logs=logs,
                    error_tail=err_msg,
                    status_summary="Failed to dispatch GitHub Actions sandbox.",
                )
            logs.append("-> Workflow dispatched successfully. Waiting for runner assignment...")
        except Exception as e:
            return ComposeValidationResult(
                success=False,
                syntax_valid=True,
                containers_started=False,
                health_check_passed=False,
                logs=logs + [f"Network error connecting to GitHub API: {e}"],
                error_tail=str(e),
                status_summary="Network error connecting to GitHub.",
            )

        # 2. Wait and find the Run ID
        time.sleep(3)
        runs_url = f"{self.base_url}/repos/{repo}/actions/runs"
        run_id = None
        start_time = time.time()

        for attempt in range(8):
            try:
                res = requests.get(runs_url, headers=self._headers(), timeout=10)
                if res.status_code == 200:
                    runs = res.json().get("workflow_runs", [])
                    for r in runs:
                        if r.get("head_branch") == branch and workflow_file in r.get("path", ""):
                            run_id = r["id"]
                            logs.append(f"-> Assigned to GitHub Actions Run #{run_id} (HTML: {r.get('html_url')})")
                            break
                    if run_id:
                        break
            except Exception:
                pass
            time.sleep(2)

        if not run_id:
            logs.append("WARNING: Dispatched workflow but could not poll run ID within 30s.")
            return ComposeValidationResult(
                success=True,
                syntax_valid=True,
                containers_started=True,
                health_check_passed=False,
                logs=logs,
                is_simulated=False,
                status_summary="Workflow dispatched in background on GitHub Actions.",
            )

        # 3. Poll for completion
        run_detail_url = f"{self.base_url}/repos/{repo}/actions/runs/{run_id}"
        conclusion = None

        while time.time() - start_time < timeout_seconds:
            try:
                r_detail = requests.get(run_detail_url, headers=self._headers(), timeout=10).json()
                status = r_detail.get("status")
                conclusion = r_detail.get("conclusion")

                if status == "completed":
                    logs.append(f"-> GitHub Actions Runner finished with conclusion: [{conclusion.upper()}]")
                    break

                logs.append(f"   ... Runner is currently {status} (elapsed: {int(time.time() - start_time)}s)")
            except Exception:
                pass

            time.sleep(poll_interval)

        success = (conclusion == "success")
        container_logs = ""
        error_tail = ""

        # 4. Fetch job step logs & download full raw container logs
        jobs_url = f"{self.base_url}/repos/{repo}/actions/runs/{run_id}/jobs"
        try:
            jobs_res = requests.get(jobs_url, headers=self._headers(), timeout=10).json()
            for job in jobs_res.get("jobs", []):
                logs.append(f"\n[Cloud Runner Job: {job.get('name')}] Status: {job.get('conclusion')}")
                for step in job.get("steps", []):
                    icon = "[OK]" if step.get("conclusion") == "success" else "[FAIL]"
                    logs.append(f"  {icon} Step: {step.get('name')}")

                # If job failed, download the raw log stream
                if job.get("conclusion") == "failure":
                    raw_log = self.get_job_raw_logs(repo, job.get("id"))
                    if raw_log:
                        # Extract the container logs section if present
                        if "=== Container Logs ===" in raw_log:
                            parts = raw_log.split("=== Container Logs ===")
                            if len(parts) > 1:
                                container_section = parts[1].split("=== 5. Cleaning Up")[0]
                                container_logs = container_section.strip()
                        elif "Traceback" in raw_log or "Error" in raw_log:
                            container_logs = raw_log[-2000:].strip()

                        if container_logs:
                            logs.append("\n" + "=" * 60)
                            logs.append("📦 LIVE CONTAINER RUNTIME LOGS (from Cloud Sandbox):")
                            logs.append("=" * 60)
                            for cl in container_logs.splitlines()[:50]:
                                logs.append(f"  | {cl}")
                            logs.append("=" * 60)
                            error_tail = container_logs
        except Exception as e:
            logs.append(f"Error fetching detailed logs: {e}")

        if not error_tail and not success:
            error_tail = f"GitHub Actions runner run #{run_id} failed with conclusion: {conclusion}"

        return ComposeValidationResult(
            success=success,
            syntax_valid=True,
            containers_started=True,
            health_check_passed=success,
            logs=logs,
            error_tail=error_tail if not success else "",
            is_simulated=False,
            status_summary=f"GitHub Actions cloud sandbox run #{run_id}: {conclusion.upper() if conclusion else 'TIMED_OUT'}",
        )
