"""
AI DevOps Agent Platform (ai-devops-agent)
Autonomous Cloud & Local DevOps Automation Engine
"""

__version__ = "0.1.1"

from ai_devops_agent.analyzer.detector import analyze_repository
from ai_devops_agent.generator.engine import generate_devops_bundle
from ai_devops_agent.agent.graph import run_devops_workflow

__all__ = [
    "__version__",
    "analyze_repository",
    "generate_devops_bundle",
    "run_devops_workflow",
]
