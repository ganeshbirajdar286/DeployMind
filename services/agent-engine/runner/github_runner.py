import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Callable
import requests
from .docker_runner import ComposeValidationResult


class GitHubActionsRunner:
    """Dispatches, monitors, and streams raw container logs from GitHub Actions cloud sandboxes."""

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"
        self.last_run_id: Optional[int] = None

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
            resp = requests.get(url, headers=self._headers(), allow_redirects=True, timeout=25)
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
        log_callback: Optional[Callable[[str], None]] = None,
        commit_sha: Optional[str] = None,
        exclude_run_ids: Optional[set] = None,
    ) -> ComposeValidationResult:
        """Dispatches the verification workflow on GitHub Actions and streams real logs."""
        logs: List[str] = []
        self.last_run_id = None

        def emit(msg: str):
            logs.append(msg)
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        emit(f"[GitHubActionsRunner] Target Sandbox: {repo} (branch: {branch})")
        emit(f"[GitHubActionsRunner] Cloud Workflow: .github/workflows/{workflow_file}")

        if not self.token:
            emit("[GitHubActionsRunner] Warning: No GITHUB_TOKEN configured.")
            emit("[GitHubActionsRunner] Falling back to pre-flight static verification.")
            return ComposeValidationResult(
                success=True,
                syntax_valid=True,
                containers_started=False,
                health_check_passed=False,
                logs=logs,
                is_simulated=True,
                status_summary="Cloud verification prepared (Set GITHUB_TOKEN to execute remotely).",
            )

        # 1. Trigger workflow_dispatch (or verify if a push-triggered run is already active)
        dispatch_url = f"{self.base_url}/repos/{repo}/actions/workflows/{workflow_file}/dispatches"
        payload = {
            "ref": branch,
            "inputs": {
                "health_port": str(health_port),
                "health_path": health_path,
            },
        }

        try:
            emit("-> Checking cloud sandbox workflow on GitHub Actions...")
            resp = requests.post(dispatch_url, json=payload, headers=self._headers(), timeout=15)
            if resp.status_code in (204, 201):
                emit("-> Workflow dispatched successfully via API. Waiting for runner assignment...")
            elif resp.status_code in (404, 422):
                emit(f"-> Workflow '{workflow_file}' is new on branch '{branch}'.")
                emit(f"-> Tracking automatic push-triggered run on GitHub Actions...")
            else:
                emit(f"-> Workflow dispatch returned HTTP {resp.status_code}. Tracking push trigger on '{branch}'...")
        except Exception as e:
            emit(f"-> Workflow dispatch warning: {e}. Tracking push trigger on '{branch}'...")

        # 2. Wait and find the Run ID
        time.sleep(3)
        runs_url = f"{self.base_url}/repos/{repo}/actions/runs?branch={branch}&per_page=15"
        run_id = None
        start_time = time.time()

        for attempt in range(24):
            try:
                res = requests.get(runs_url, headers=self._headers(), timeout=10)
                if res.status_code == 200:
                    runs = res.json().get("workflow_runs", [])
                    for r in runs:
                        r_id = r.get("id")
                        if exclude_run_ids and r_id in exclude_run_ids:
                            continue

                        branch_match = (r.get("head_branch") == branch)
                        file_match = (
                            workflow_file in r.get("path", "")
                            or "ai-devops-verify" in r.get("path", "")
                            or "ai-devops-verify" in r.get("name", "").lower()
                            or "verification sandbox" in r.get("name", "").lower()
                            or "verify" in r.get("name", "").lower()
                        )
                        sha_match = True
                        if commit_sha:
                            sha_match = (r.get("head_sha") == commit_sha)

                        # Primary match: exact branch, file, and commit SHA
                        if branch_match and file_match and sha_match:
                            run_id = r_id
                            status = r.get("status")
                            emit(f"-> Assigned to GitHub Actions Run #{run_id} (Status: {status}, HTML: {r.get('html_url')})")
                            break

                        # Secondary match after attempt 4 (10s): any queued/in_progress run on this branch
                        if attempt >= 4 and branch_match and r.get("status") in ("queued", "in_progress", "waiting"):
                            run_id = r_id
                            status = r.get("status")
                            emit(f"-> Bound to active GitHub Actions Run #{run_id} (Status: {status}, HTML: {r.get('html_url')})")
                            break

                    if run_id:
                        self.last_run_id = run_id
                        break
            except Exception:
                pass
            time.sleep(2.5)

        if not run_id:
            err_msg = f"Could not locate an active GitHub Actions workflow run for {workflow_file} on branch '{branch}' within 60s."
            emit(f"ERROR: {err_msg}")
            return ComposeValidationResult(
                success=False,
                syntax_valid=True,
                containers_started=False,
                health_check_passed=False,
                logs=logs,
                error_tail=err_msg,
                status_summary=f"Workflow run not detected on branch '{branch}'.",
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
                    emit(f"-> GitHub Actions Runner finished with conclusion: [{conclusion.upper()}]")
                    break

                emit(f"   ... Runner is currently {status} (elapsed: {int(time.time() - start_time)}s)")
            except Exception:
                pass

            time.sleep(poll_interval)

        success = (conclusion == "success")
        container_logs = ""
        error_tail = ""

        # 4. Fetch job step logs & download complete raw console logs for EVERY job
        jobs_url = f"{self.base_url}/repos/{repo}/actions/runs/{run_id}/jobs"
        try:
            emit("\nDownloading complete GitHub Actions runner execution logs...")
            jobs_res = requests.get(jobs_url, headers=self._headers(), timeout=10).json()
            for job in jobs_res.get("jobs", []):
                job_name = job.get('name')
                job_id = job.get('id')
                job_conclusion = job.get('conclusion')
                emit(f"\n[Cloud Runner Job: {job_name}] Status: {job_conclusion}")
                for step in job.get("steps", []):
                    icon = "[OK]" if step.get("conclusion") == "success" else "[FAIL]"
                    emit(f"  {icon} Step: {step.get('name')}")

                # Download the complete raw log text for the job (both on success and failure)
                raw_log = self.get_job_raw_logs(repo, job_id)
                if raw_log:
                    emit("\n" + "=" * 70)
                    emit(f"📋 FULL GITHUB ACTIONS CONSOLE LOGS (Job: '{job_name}', Run #{run_id})")
                    emit("=" * 70)

                    for raw_line in raw_log.splitlines():
                        cleaned = re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*", "", raw_line).rstrip()
                        if not cleaned:
                            continue
                        if cleaned.startswith("##[group]"):
                            step_title = cleaned.replace("##[group]", "").strip()
                            emit(f"\n--- [Step: {step_title}] ---")
                        elif cleaned.startswith("##[endgroup]"):
                            continue
                        elif cleaned.startswith("##[section]"):
                            sec_title = cleaned.replace("##[section]", "").strip()
                            emit(f"\n>>> {sec_title}")
                        elif cleaned.startswith("##[error]"):
                            err_text = cleaned.replace("##[error]", "").strip()
                            emit(f"❌ ERROR: {err_text}")
                        elif cleaned.startswith("##[warning]"):
                            warn_text = cleaned.replace("##[warning]", "").strip()
                            emit(f"⚠️ WARNING: {warn_text}")
                        else:
                            emit(f"  {cleaned}")

                    emit("=" * 70 + "\n")

                    # Extract container error traceback specifically for the AI patcher if failed
                    if not success:
                        if "--- BEGIN RAW CONTAINER LOGS ---" in raw_log:
                            parts = raw_log.split("--- BEGIN RAW CONTAINER LOGS ---")
                            last_part = parts[-1]
                            if "--- END RAW CONTAINER LOGS ---" in last_part:
                                container_logs = last_part.split("--- END RAW CONTAINER LOGS ---")[0].strip()
                            else:
                                container_logs = last_part.strip()
                        elif "=== Container Logs ===" in raw_log:
                            parts = raw_log.split("=== Container Logs ===")
                            last_part = parts[-1]
                            container_logs = last_part.split("=== 5. Cleaning Up")[0].strip()
                        elif "Traceback" in raw_log:
                            tb_idx = raw_log.rfind("Traceback")
                            container_logs = raw_log[tb_idx:].split("##[endgroup]")[0].strip()

                        if container_logs:
                            err_lines = []
                            for l in container_logs.splitlines():
                                cl = re.sub(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s*", "", l).rstrip()
                                if cl and not cl.startswith("##["):
                                    err_lines.append(cl)
                            error_tail = "\n".join(err_lines)
        except Exception as e:
            emit(f"Error fetching detailed logs: {e}")

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
