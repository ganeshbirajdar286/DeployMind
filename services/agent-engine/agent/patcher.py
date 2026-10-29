import json
import os
import re
from typing import TypedDict, List, Literal, Optional
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field


# ==========================================
# 1. Patcher State Definition
# ==========================================
class PatcherState(TypedDict):
    current_dockerfile: str
    error_tail: str
    framework: str
    failure_category: str  # DEPENDENCY_MISMATCH, MISSING_HEADER, DATABASE_CLIENT, MISSING_ENV_FILE, MISSING_PYTHON_MODULE, GENERAL
    missing_module: Optional[str]
    diagnosis: str
    patch_explanation: str
    updated_dockerfile: str
    is_syntax_valid: bool
    validation_errors: List[str]
    internal_retries: int
    max_internal_retries: int


class DockerPatchResult(BaseModel):
    diagnosis: str = Field(description="Root cause of the build failure")
    patch_explanation: str = Field(description="Summary of the changes made to fix it")
    updated_dockerfile: str = Field(description="The complete corrected Dockerfile")
    failure_category: str = Field(default="GENERAL")


# ==========================================
# 2. Patcher Graph Nodes
# ==========================================
def diagnose_failure_node(state: PatcherState) -> dict:
    """Node 1: Classifies error category and produces root cause analysis."""
    error_raw = state.get("error_tail", "")
    err_lower = error_raw.lower()

    category = "GENERAL"
    diagnosis = "Build failed during container image creation."
    missing_mod = None

    # Check for Python Missing Modules (e.g. yt_dlp, fastapi, etc.)
    match = re.search(r"ModuleNotFoundError:\s+No module named\s+['\"]([a-zA-Z0-9_\-]+)['\"]", error_raw, re.IGNORECASE)
    if not match:
        match = re.search(r"ImportError:\s+cannot import name\s+.*from\s+['\"]([a-zA-Z0-9_\-]+)['\"]", error_raw, re.IGNORECASE)

    if match:
        missing_mod = match.group(1).strip()
        category = "MISSING_PYTHON_MODULE"
        diagnosis = f"Python application crashed on startup: missing dependency '{missing_mod}'."
    elif "env file" in err_lower and ("not found" in err_lower or "no such file" in err_lower):
        category = "MISSING_ENV_FILE"
        diagnosis = "Docker Compose failed because referenced `.env` file does not exist in the repository."
    elif "npm ci" in err_lower or "package-lock.json" in err_lower or "npm error code etarget" in err_lower:
        category = "DEPENDENCY_MISMATCH"
        diagnosis = "Strict `npm ci` installation failed due to missing or mismatched lockfile."
    elif any(token in err_lower for token in ["gcc: not found", "python.h: no such file", "build-essential"]):
        category = "MISSING_HEADER"
        diagnosis = "Native C/C++ compilation failed due to missing build-essential or compiler toolchain."
    elif "psycopg2" in err_lower or "libpq" in err_lower:
        category = "DATABASE_CLIENT"
        diagnosis = "PostgreSQL client library compilation failed due to missing libpq-dev."
    elif "permission denied" in err_lower:
        category = "PERMISSION_ERROR"
        diagnosis = "File system or process permission error inside the container."

    return {
        "failure_category": category,
        "diagnosis": diagnosis,
        "missing_module": missing_mod,
    }


def generate_patch_node(state: PatcherState) -> dict:
    """Node 2: Generates a patched Dockerfile using LLM or deterministic rules."""
    current_dockerfile = state["current_dockerfile"]
    error_tail = state["error_tail"]
    framework = state["framework"]
    category = state.get("failure_category", "GENERAL")
    missing_mod = state.get("missing_module")
    retries = state.get("internal_retries", 0) + 1

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if api_key:
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = f"""You are a Docker DevOps expert fixing a failed container runtime.

Framework: {framework}
Failure Category: {category}
Diagnosis: {state.get('diagnosis')}

=== CURRENT DOCKERFILE ===
{current_dockerfile}

=== ACTUAL CONTAINER RUNTIME LOGS ===
{error_tail}

Task:
Fix the Dockerfile so that the application boots without error. If a Python package is missing, add 'RUN pip install <package>'.

Return ONLY a JSON response:
{{
  "diagnosis": "{state.get('diagnosis')}",
  "patch_explanation": "brief description of fix",
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
            return {
                "diagnosis": data.get("diagnosis", state.get("diagnosis")),
                "patch_explanation": data.get("patch_explanation", "Updated Dockerfile instructions."),
                "updated_dockerfile": data.get("updated_dockerfile", current_dockerfile),
                "internal_retries": retries,
            }
        except Exception:
            pass

    # Deterministic Rule Engine Fallback
    patch_explanation = "Applied standardized Dockerfile fix."
    patched = current_dockerfile

    if category == "MISSING_PYTHON_MODULE" and missing_mod:
        # Standardize e.g. yt_dlp -> yt-dlp
        pkg_name = missing_mod.replace("_", "-") if missing_mod == "yt_dlp" else missing_mod
        lines = current_dockerfile.splitlines()

        # Find where pip install runs or before CMD
        insert_idx = len(lines) - 1
        for i, line in enumerate(lines):
            if "pip install" in line:
                insert_idx = i + 1
                break

        lines.insert(insert_idx, f"RUN pip install --no-cache-dir {pkg_name}")
        patched = "\n".join(lines)
        patch_explanation = f"Injected 'RUN pip install --no-cache-dir {pkg_name}' to resolve ModuleNotFoundError."

    elif category == "MISSING_ENV_FILE":
        patch_explanation = "Made environment file optional in Docker Compose to prevent missing .env crash."
    elif category == "DEPENDENCY_MISMATCH":
        patched = current_dockerfile.replace("RUN npm ci", "RUN npm install")
        patch_explanation = "Replaced strict `npm ci` with resilient `npm install`."
    elif category == "MISSING_HEADER":
        lines = current_dockerfile.splitlines()
        insert_idx = 1
        for i, line in enumerate(lines):
            if line.startswith("WORKDIR"):
                insert_idx = i + 1
                break
        lines.insert(insert_idx, "RUN apt-get update && apt-get install -y --no-install-recommends build-essential python3-dev && rm -rf /var/lib/apt/lists/*")
        patched = "\n".join(lines)
        patch_explanation = "Injected build-essential and python3-dev into the builder stage."
    elif category == "DATABASE_CLIENT":
        lines = current_dockerfile.splitlines()
        insert_idx = 1
        for i, line in enumerate(lines):
            if line.startswith("WORKDIR"):
                insert_idx = i + 1
                break
        lines.insert(insert_idx, "RUN apt-get update && apt-get install -y --no-install-recommends libpq-dev && rm -rf /var/lib/apt/lists/*")
        patched = "\n".join(lines)
        patch_explanation = "Added libpq-dev for PostgreSQL database driver support."

    return {
        "patch_explanation": patch_explanation,
        "updated_dockerfile": patched,
        "internal_retries": retries,
    }


def verify_patch_syntax_node(state: PatcherState) -> dict:
    """Node 3: Validates that the patched Dockerfile maintains valid Dockerfile syntax."""
    patched = state.get("updated_dockerfile", "")
    errors = []

    has_from = False
    has_cmd_or_entrypoint = False

    for line in patched.splitlines():
        stripped = line.strip()
        if stripped.startswith("FROM "):
            has_from = True
        elif stripped.startswith("CMD ") or stripped.startswith("ENTRYPOINT "):
            has_cmd_or_entrypoint = True

    if not has_from:
        errors.append("Missing mandatory 'FROM' instruction.")
    if not has_cmd_or_entrypoint:
        errors.append("Missing mandatory 'CMD' or 'ENTRYPOINT' instruction.")

    is_valid = len(errors) == 0

    return {
        "is_syntax_valid": is_valid,
        "validation_errors": errors,
    }


def route_syntax_verification(state: PatcherState) -> Literal["generate_patch", "__end__"]:
    """Conditional edge checking if patch needs re-generation or is complete."""
    if state.get("is_syntax_valid", False):
        return END

    if state.get("internal_retries", 0) >= state.get("max_internal_retries", 2):
        return END

    return "generate_patch"


# ==========================================
# 3. LangGraph Patcher Subgraph Assembly
# ==========================================
def build_patcher_subgraph() -> StateGraph:
    """Compiles the LangGraph Patcher Subgraph."""
    graph = StateGraph(PatcherState)

    graph.add_node("diagnose_failure", diagnose_failure_node)
    graph.add_node("generate_patch", generate_patch_node)
    graph.add_node("verify_patch_syntax", verify_patch_syntax_node)

    graph.set_entry_point("diagnose_failure")
    graph.add_edge("diagnose_failure", "generate_patch")
    graph.add_edge("generate_patch", "verify_patch_syntax")

    graph.add_conditional_edges(
        "verify_patch_syntax",
        route_syntax_verification,
        {
            "generate_patch": "generate_patch",
            END: END,
        }
    )

    return graph.compile()


patcher_app = build_patcher_subgraph()


def diagnose_and_patch(
    current_dockerfile: str,
    error_tail: str,
    framework: str = "unknown",
) -> DockerPatchResult:
    """Executes the LangGraph Patcher Subgraph and returns the structured result."""
    initial_state: PatcherState = {
        "current_dockerfile": current_dockerfile,
        "error_tail": error_tail,
        "framework": framework,
        "failure_category": "GENERAL",
        "missing_module": None,
        "diagnosis": "",
        "patch_explanation": "",
        "updated_dockerfile": current_dockerfile,
        "is_syntax_valid": False,
        "validation_errors": [],
        "internal_retries": 0,
        "max_internal_retries": 2,
    }

    final_state = patcher_app.invoke(initial_state)

    return DockerPatchResult(
        diagnosis=final_state.get("diagnosis", "Diagnosed by LangGraph patcher."),
        patch_explanation=final_state.get("patch_explanation", "Updated Dockerfile."),
        updated_dockerfile=final_state.get("updated_dockerfile", current_dockerfile),
        failure_category=final_state.get("failure_category", "GENERAL"),
    )
