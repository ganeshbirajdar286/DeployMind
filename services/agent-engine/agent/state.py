from typing import TypedDict, List, Dict, Optional, Any


class PatchRecord(TypedDict):
    attempt: int
    diagnosis: str
    target_file: str
    patch_explanation: str
    applied_at: str


class AgentState(TypedDict):
    repo_path: str
    analysis: Optional[Dict[str, Any]]
    artifacts: Dict[str, str]
    current_stage: str  # "INITIALIZING", "ANALYZING", "VALIDATING_DOCKER", "VALIDATING_COMPOSE", "REPAIRING", "SUCCESS", "FAILED"
    build_logs: List[str]
    compose_logs: List[str]
    error_tail: str
    retry_count: int
    max_retries: int
    build_succeeded: bool
    compose_succeeded: bool
    patch_history: List[PatchRecord]
    status_message: str
    job_id: Optional[str]
