"""
Backward-compatibility CLI entrypoint shim.
Redirects to the ai_devops_agent package CLI.
"""
from ai_devops_agent.cli import main

if __name__ == "__main__":
    main()

