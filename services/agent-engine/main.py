"""
Backward-compatibility FastAPI entrypoint shim.
Redirects to the ai_devops_agent package FastAPI app.
"""
from ai_devops_agent.main import app, main

if __name__ == "__main__":
    main()

