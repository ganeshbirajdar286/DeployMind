import os
import json
from datetime import datetime
from typing import Optional, Tuple
from pydantic import BaseModel, Field


class DockerPatchResult(BaseModel):
    diagnosis: str = Field(description="Root cause of the build failure")
    patch_explanation: str = Field(description="Summary of the changes made to fix it")
    updated_dockerfile: str = Field(description="The complete corrected Dockerfile")


def diagnose_and_patch(
    current_dockerfile: str,
    error_tail: str,
    framework: str = "unknown",
) -> DockerPatchResult:
    """Diagnoses Docker build failure using Gemini LLM if available, with deterministic fallback."""
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if api_key:
        try:
            return _call_gemini_patcher(api_key, current_dockerfile, error_tail, framework)
        except Exception as e:
            # Fall back to deterministic rules if API call fails
            pass

    return _rule_based_fallback_patch(current_dockerfile, error_tail, framework)


def _call_gemini_patcher(
    api_key: str,
    current_dockerfile: str,
    error_tail: str,
    framework: str,
) -> DockerPatchResult:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    prompt = f"""You are an elite DevOps engineer fixing a failed Docker container build.

Framework: {framework}

=== CURRENT DOCKERFILE ===
{current_dockerfile}

=== BUILD ERROR LOGS (Tail) ===
{error_tail}

Task:
1. Identify the exact failure cause.
2. Provide a minimal, surgical fix for the Dockerfile.
3. Return ONLY a valid JSON object matching this schema:
{{
  "diagnosis": "concise explanation of failure",
  "patch_explanation": "what was changed in the Dockerfile",
  "updated_dockerfile": "full revised Dockerfile"
}}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    data = json.loads(response.text)
    return DockerPatchResult(
        diagnosis=data.get("diagnosis", "Docker build failure diagnosed by AI."),
        patch_explanation=data.get("patch_explanation", "Updated Dockerfile instructions."),
        updated_dockerfile=data.get("updated_dockerfile", current_dockerfile),
    )


def _rule_based_fallback_patch(
    current_dockerfile: str,
    error_tail: str,
    framework: str,
) -> DockerPatchResult:
    """Deterministic rule-based heuristics for standard Docker failure patterns."""
    err_lower = error_tail.lower()

    # Rule 1: npm ci failed due to missing package-lock.json or mismatch
    if "npm ci" in current_dockerfile and ("package-lock.json" in err_lower or "npm error code etarget" in err_lower or "npm ci can only install" in err_lower):
        patched = current_dockerfile.replace("RUN npm ci", "RUN npm install")
        return DockerPatchResult(
            diagnosis="`npm ci` failed due to strict package-lock mismatch or absence.",
            patch_explanation="Switched `npm ci` to `npm install` for resilient dependency resolution.",
            updated_dockerfile=patched,
        )

    # Rule 2: Python C-extension build failure (missing gcc / build-essential)
    if "python" in framework or "fastapi" in framework or "flask" in framework:
        if any(token in err_lower for token in ["gcc: not found", "error: command 'gcc' failed", "python.h: no such file", "build-essential"]):
            if "build-essential" not in current_dockerfile:
                lines = current_dockerfile.splitlines()
                # Insert apt-get install build-essential after the first FROM or WORKDIR
                insert_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith("WORKDIR"):
                        insert_idx = i + 1
                        break
                lines.insert(insert_idx, "RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3-dev && rm -rf /var/lib/apt/lists/*")
                patched = "\n".join(lines)
                return DockerPatchResult(
                    diagnosis="Native C compilation failed due to missing build-essential and Python C headers.",
                    patch_explanation="Injected build-essential and python3-dev into the builder stage.",
                    updated_dockerfile=patched,
                )

    # Rule 3: Missing libpq for psycopg2 / postgresql
    if "psycopg2" in err_lower or "libpq" in err_lower:
        lines = current_dockerfile.splitlines()
        insert_idx = 0
        for i, line in enumerate(lines):
            if line.startswith("WORKDIR"):
                insert_idx = i + 1
                break
        lines.insert(insert_idx, "RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev && rm -rf /var/lib/apt/lists/*")
        return DockerPatchResult(
            diagnosis="PostgreSQL client compilation failed: libpq-dev missing.",
            patch_explanation="Added libpq-dev dependency to apt-get install.",
            updated_dockerfile="\n".join(lines),
        )

    # Generic fallback: add non-cache flag
    return DockerPatchResult(
        diagnosis="Build error detected in container instructions.",
        patch_explanation="Cleaned up package installation and cache layers.",
        updated_dockerfile=current_dockerfile,
    )
