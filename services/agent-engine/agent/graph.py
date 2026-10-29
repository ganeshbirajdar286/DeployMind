from typing import Literal
from langgraph.graph import StateGraph, END
from .state import AgentState
from .nodes import (
    analyze_and_generate_node,
    validate_build_node,
    validate_compose_node,
    repair_patch_node,
)


def decide_after_docker_build(state: AgentState) -> Literal["validate_compose", "repair_patch", "__end__"]:
    """Routes to compose validation on success, or repair loop on failure."""
    if state.get("build_succeeded", False):
        return "validate_compose"

    if state.get("retry_count", 0) >= state.get("max_retries", 2):
        return END

    return "repair_patch"


def decide_after_compose(state: AgentState) -> Literal["repair_patch", "__end__"]:
    """Completes on compose success, or routes to repair loop on failure."""
    if state.get("compose_succeeded", False):
        return END

    if state.get("retry_count", 0) >= state.get("max_retries", 2):
        return END

    return "repair_patch"


def build_devops_graph() -> StateGraph:
    """Constructs the comprehensive LangGraph supervisor workflow."""
    workflow = StateGraph(AgentState)

    # Register Nodes
    workflow.add_node("analyze_and_generate", analyze_and_generate_node)
    workflow.add_node("validate_build", validate_build_node)
    workflow.add_node("validate_compose", validate_compose_node)
    workflow.add_node("repair_patch", repair_patch_node)

    # Configure Edges
    workflow.set_entry_point("analyze_and_generate")
    workflow.add_edge("analyze_and_generate", "validate_build")

    # Routing after Dockerfile validation
    workflow.add_conditional_edges(
        "validate_build",
        decide_after_docker_build,
        {
            "validate_compose": "validate_compose",
            "repair_patch": "repair_patch",
            END: END,
        }
    )

    # Routing after Docker Compose validation
    workflow.add_conditional_edges(
        "validate_compose",
        decide_after_compose,
        {
            "repair_patch": "repair_patch",
            END: END,
        }
    )

    workflow.add_edge("repair_patch", "validate_build")

    return workflow.compile()


# Singleton compiled graph instance
devops_agent_app = build_devops_graph()


def run_devops_workflow(
    repo_path: str,
    max_retries: int = 2,
    job_id: str = None,
) -> AgentState:
    """Executes the complete AI DevOps workflow synchronously."""
    initial_state: AgentState = {
        "repo_path": repo_path,
        "analysis": None,
        "artifacts": {},
        "current_stage": "INITIALIZING",
        "build_logs": [],
        "compose_logs": [],
        "error_tail": "",
        "retry_count": 0,
        "max_retries": max_retries,
        "build_succeeded": False,
        "compose_succeeded": False,
        "patch_history": [],
        "status_message": "Starting AI DevOps Agent...",
        "job_id": job_id,
    }

    final_state = devops_agent_app.invoke(initial_state)
    return final_state
