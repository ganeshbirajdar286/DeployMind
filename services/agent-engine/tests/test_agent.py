import tempfile
from pathlib import Path
from ai_devops_agent.runner.docker_runner import DockerRunner
from ai_devops_agent.agent.graph import run_devops_workflow
from ai_devops_agent.agent.patcher import diagnose_and_patch, patcher_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_docker_runner_validation():
    runner = DockerRunner()
    target_repo = FIXTURES_DIR / "sample-react-vite"

    dockerfile_path = target_repo / "Dockerfile"
    dockerfile_path.write_text("FROM nginx:alpine\nEXPOSE 80\nCMD [\"nginx\", \"-g\", \"daemon off;\"]\n")

    try:
        result = runner.build_image(context_path=target_repo, dockerfile_path=dockerfile_path)
        assert result.success is True
        assert result.exit_code == 0
    finally:
        if dockerfile_path.exists():
            dockerfile_path.unlink()


def test_docker_runner_compose_validation():
    runner = DockerRunner()
    sample_compose = """version: '3.8'
services:
  web:
    build: .
    ports:
      - "3000:3000"
"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_compose = Path(temp_dir) / "docker-compose.yml"
        temp_compose.write_text(sample_compose, encoding="utf-8")

        res = runner.validate_compose(compose_file_path=temp_compose, ports_to_probe=[3000])
        assert res.success is True
        assert res.syntax_valid is True


def test_langgraph_workflow_sample_fastapi():
    target = FIXTURES_DIR / "sample-fastapi"
    state = run_devops_workflow(repo_path=str(target), max_retries=2)

    assert state["current_stage"] == "SUCCESS"
    assert state["build_succeeded"] is True
    assert state["compose_succeeded"] is True
    assert "Dockerfile" in state["artifacts"]
    assert "docker-compose.yml" in state["artifacts"]
    assert ".github/workflows/ci.yml" in state["artifacts"]


def test_ai_patcher_rule_fallback():
    broken_dockerfile = "FROM node:20-alpine\nWORKDIR /app\nCOPY package*.json ./\nRUN npm ci\nCMD [\"npm\", \"start\"]\n"
    error_tail = "npm error code ETARGET\nnpm error notarget No matching version found for foo@^9.9.9\nnpm ci can only install with existing package-lock"

    patch = diagnose_and_patch(
        current_dockerfile=broken_dockerfile,
        error_tail=error_tail,
        framework="react_vite",
    )

    assert "npm install" in patch.updated_dockerfile
    assert "npm ci" not in patch.updated_dockerfile
    assert patch.failure_category == "DEPENDENCY_MISMATCH"
    assert len(patch.diagnosis) > 0
    assert len(patch.patch_explanation) > 0


def test_ai_patcher_missing_python_module():
    dockerfile = "FROM python:3.11-slim\nWORKDIR /app\nRUN pip install -r requirements.txt\nCMD [\"python\", \"main.py\"]\n"
    error_tail = "Traceback (most recent call last):\n  File \"/app/main.py\", line 2, in <module>\n    import yt_dlp\nModuleNotFoundError: No module named 'yt_dlp'\n"

    patch = diagnose_and_patch(dockerfile, error_tail, framework="fastapi")

    assert patch.failure_category == "MISSING_PYTHON_MODULE"
    assert "RUN pip install --no-cache-dir yt-dlp" in patch.updated_dockerfile
    assert "yt_dlp" in patch.diagnosis


def test_patcher_subgraph_langgraph_execution():
    """Directly verifies the LangGraph patcher subgraph state machine."""
    broken_dockerfile = "FROM python:3.11-slim\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"main.py\"]\n"
    error_tail = "error: command 'gcc' failed: No such file or directory. Python.h: No such file."

    initial_state = {
        "current_dockerfile": broken_dockerfile,
        "error_tail": error_tail,
        "framework": "fastapi",
        "failure_category": "GENERAL",
        "missing_module": None,
        "diagnosis": "",
        "patch_explanation": "",
        "updated_dockerfile": broken_dockerfile,
        "is_syntax_valid": False,
        "validation_errors": [],
        "internal_retries": 0,
        "max_internal_retries": 2,
    }

    final_state = patcher_app.invoke(initial_state)

    assert final_state["failure_category"] == "MISSING_HEADER"
    assert "build-essential" in final_state["updated_dockerfile"]
    assert final_state["is_syntax_valid"] is True
    assert final_state["internal_retries"] >= 1


def test_ai_patcher_missing_whisper_and_ffmpeg():
    dockerfile = """FROM python:3.11-slim AS builder
WORKDIR /app
RUN pip install -r requirements.txt
FROM python:3.11-slim AS runner
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
CMD ["python", "main.py"]
"""
    error_tail = "ModuleNotFoundError: No module named 'whisper'"
    patch = diagnose_and_patch(dockerfile, error_tail, framework="fastapi")

    assert patch.failure_category == "MISSING_PYTHON_MODULE"
    assert "openai-whisper" in patch.updated_dockerfile
    assert "ffmpeg" in patch.updated_dockerfile


def test_ai_patcher_monorepo_path_alias():
    dockerfile = """FROM node:20-alpine AS runner
WORKDIR /app
COPY . .
USER node
CMD ["node", "index.js"]
"""
    error_tail = """Error [ERR_MODULE_NOT_FOUND]: Cannot find module '/backend/router/email.router.js' imported from /app/index.js
    at finalizeResolution (node:internal/modules/esm/resolve:283:11)
    code: 'ERR_MODULE_NOT_FOUND',
    url: 'file:///backend/router/email.router.js'"""

    patch = diagnose_and_patch(dockerfile, error_tail, framework="express")

    assert patch.failure_category == "MONOREPO_PATH_ALIAS"
    assert "ln -s /app /backend" in patch.updated_dockerfile
    assert patch.updated_dockerfile.index("ln -s /app /backend") < patch.updated_dockerfile.index("USER node")


def test_interactive_find_target_file():
    from ai_devops_agent.agent.autonomous_loop import find_target_file

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        settings_file = tmp_path / "btre" / "settings.py"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text("DEBUG = True\nALLOWED_HOSTS = []\n")

        df = tmp_path / "Dockerfile"
        df.write_text("FROM python:3.11\nEXPOSE 8000\n")

        reqs = tmp_path / "requirements.txt"
        reqs.write_text("django>=4.2\n")

        # Explicit name
        f1 = find_target_file(tmp_path, "Please update settings.py to allow all hosts")
        assert f1 is not None
        assert f1.name == "settings.py"

        # Semantic keywords
        f2 = find_target_file(tmp_path, "change the database and allowed_hosts fallback")
        assert f2 is not None
        assert f2.name == "settings.py"

        f3 = find_target_file(tmp_path, "change port to 8080 in container Dockerfile")
        assert f3 is not None
        assert f3.name == "Dockerfile"

        f4 = find_target_file(tmp_path, "add pillow and psycopg2 to dependencies")
        assert f4 is not None
        assert f4.name == "requirements.txt"


def test_interactive_apply_ai_instruction():
    from ai_devops_agent.agent.autonomous_loop import apply_ai_instruction_to_file

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Test settings.py modification
        settings_file = tmp_path / "settings.py"
        settings_file.write_text(
            "DEBUG = True\n"
            "ALLOWED_HOSTS = []\n"
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.postgresql',\n"
            "        'NAME': 'testdb',\n"
            "    }\n"
            "}\n"
        )
        ok, explanation, new_content = apply_ai_instruction_to_file(
            settings_file,
            "Update settings.py to allow all hosts and fallback to sqlite3 if postgres fails"
        )
        assert ok is True
        assert "ALLOWED_HOSTS" in new_content
        assert "sqlite3" in new_content
        assert "DB_HOST" in new_content

        # 2. Test requirements.txt modification
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("Django>=4.2.0\n")
        ok, explanation, new_reqs = apply_ai_instruction_to_file(
            req_file,
            "Add pillow and psycopg2 to requirements"
        )
        assert ok is True
        assert "Pillow" in new_reqs
        assert "psycopg2-binary" in new_reqs

        # 3. Test Dockerfile modification
        df_file = tmp_path / "Dockerfile"
        df_file.write_text("FROM python:3.11\nEXPOSE 5000\nENV PORT=5000\n")
        ok, explanation, new_df = apply_ai_instruction_to_file(
            df_file,
            "Change expose port to 8000"
        )
        assert ok is True
        assert "EXPOSE 8000" in new_df
        assert "ENV PORT=8000" in new_df


def test_classify_user_intent():
    from ai_devops_agent.agent.autonomous_loop import classify_user_intent

    # 1. Questions and diagnostic queries
    assert classify_user_intent("what is issue") == "QUESTION_OR_DIAGNOSIS"
    assert classify_user_intent("why did it fail?") == "QUESTION_OR_DIAGNOSIS"
    assert classify_user_intent("explain the error") == "QUESTION_OR_DIAGNOSIS"
    assert classify_user_intent("how to fix this") == "QUESTION_OR_DIAGNOSIS"
    assert classify_user_intent("what failed") == "QUESTION_OR_DIAGNOSIS"
    assert classify_user_intent("what's wrong") == "QUESTION_OR_DIAGNOSIS"

    # 2. Shortcuts
    assert classify_user_intent("q") == "QUIT"
    assert classify_user_intent("exit") == "QUIT"
    assert classify_user_intent("r") == "RERUN"
    assert classify_user_intent("rerun") == "RERUN"
    assert classify_user_intent("l") == "LOGS"
    assert classify_user_intent("s") == "STATUS"
    assert classify_user_intent("p") == "PUSH"
    assert classify_user_intent("d") == "DEPLOY"
    assert classify_user_intent("e") == "ALIGN_CODEBASE"
    assert classify_user_intent("align") == "ALIGN_CODEBASE"
    assert classify_user_intent("fix") == "ALIGN_CODEBASE"
    assert classify_user_intent("open settings.py") == "EDIT_SPECIFIC_FILE"
    assert classify_user_intent("help") == "HELP"

    # 3. Pending fix confirmation
    assert classify_user_intent("yes", has_pending_fix=True) == "APPLY_PENDING_FIX"
    assert classify_user_intent("y", has_pending_fix=True) == "APPLY_PENDING_FIX"
    assert classify_user_intent("do it", has_pending_fix=True) == "APPLY_PENDING_FIX"
    assert classify_user_intent("no", has_pending_fix=True) == "DISCARD_PENDING_FIX"

    # 4. File modification instructions
    assert classify_user_intent("Update settings.py to allow all hosts") == "MODIFY_FILE"
    assert classify_user_intent("Change port to 8000 in Dockerfile") == "MODIFY_FILE"
    assert classify_user_intent("Add pillow to requirements.txt") == "MODIFY_FILE"


def test_align_project_codebase():
    from ai_devops_agent.agent.autonomous_loop import align_project_codebase
    from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # 1. Django settings.py with hardcoded localhost and empty ALLOWED_HOSTS
        settings_file = tmp_path / "btre" / "settings.py"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            "DEBUG = True\n"
            "ALLOWED_HOSTS = []\n"
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.postgresql',\n"
            "        'NAME': 'btredb',\n"
            "        'USER': 'postgres',\n"
            "        'PASSWORD': '123',\n"
            "        'HOST': 'localhost',\n"
            "    }\n"
            "}\n"
        )

        # 2. docker-compose.yml without postgres
        compose_file = tmp_path / "docker-compose.yml"
        compose_file.write_text("version: '3.8'\nservices:\n  web:\n    build: .\n    ports:\n      - '8000:8000'\n")

        # 3. Dockerfile
        df_file = tmp_path / "Dockerfile"
        df_file.write_text("FROM python:3.11\nWORKDIR /app\nCMD [\"gunicorn\", \"btre.wsgi:application\"]\n")

        # 4. requirements.txt missing psycopg2
        reqs_file = tmp_path / "requirements.txt"
        reqs_file.write_text("django>=4.2.0\n")

        analysis = ProjectAnalysis(
            repo_name="test-repo",
            services=[
                ServiceMetadata(
                    name="web",
                    path=".",
                    language=Language.PYTHON,
                    framework=FrameworkType.DJANGO,
                    package_manager=PackageManager.PIP,
                    default_port=8000,
                )
            ]
        )

        res = align_project_codebase(
            target_path=tmp_path,
            analysis=analysis,
            error_context="psycopg2.OperationalError: connection to server at 'localhost', port 5432 failed: Connection refused",
        )

        # Assert all files were detected and aligned across the project
        assert len(res.file_changes) >= 3
        assert any("settings.py" in k for k in res.file_changes)
        assert "docker-compose.yml" in res.file_changes
        assert "Dockerfile" in res.file_changes

        # Verify settings.py alignment
        aligned_settings = res.file_changes["btre/settings.py" if "btre/settings.py" in res.file_changes else "settings.py"]
        assert "ALLOWED_HOSTS" in aligned_settings
        assert "sqlite3" in aligned_settings
        assert "DB_HOST" in aligned_settings

        # Verify docker-compose alignment
        aligned_compose = res.file_changes["docker-compose.yml"]
        assert "image: postgres" in aligned_compose

        # Verify Dockerfile alignment
        aligned_df = res.file_changes["Dockerfile"]
        assert "migrate" in aligned_df


def test_handle_conversational_query_deterministic_diagnosis():
    from ai_devops_agent.agent.autonomous_loop import handle_conversational_query
    from ai_devops_agent.analyzer.models import ProjectAnalysis, ServiceMetadata, FrameworkType, Language, PackageManager

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        settings_file = tmp_path / "settings.py"
        settings_file.write_text("DEBUG = True\nALLOWED_HOSTS = []\nDATABASES = {}\n")

        analysis = ProjectAnalysis(
            repo_name="test-repo",
            services=[
                ServiceMetadata(
                    name="web",
                    path=".",
                    language=Language.PYTHON,
                    framework=FrameworkType.DJANGO,
                    package_manager=PackageManager.PIP,
                    default_port=8000,
                )
            ]
        )

        sample_err_log = (
            "Attempt 2: Probing http://localhost:8000/ -> HTTP 500\n"
            "psycopg2.OperationalError: connection to server at 'localhost', port 5432 failed: Connection refused"
        )

        proposed = handle_conversational_query(
            user_query="what is issue",
            target_path=tmp_path,
            analysis=analysis,
            detected_repo="owner/test-repo",
            branch_name="ai-devops-setup",
            last_error_logs=sample_err_log,
        )

        assert proposed is not None
        assert proposed["file_path"] == settings_file
        assert "SQLite fallback" in proposed["instruction"]
