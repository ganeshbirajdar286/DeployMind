from pathlib import Path
from analyzer.detector import analyze_repository
from analyzer.models import FrameworkType, Language, PackageManager

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_detect_react_vite():
    target = FIXTURES_DIR / "sample-react-vite"
    analysis = analyze_repository(target)

    assert analysis.repo_name == "sample-react-vite"
    assert not analysis.is_monorepo
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.REACT_VITE
    assert svc.language == Language.TYPESCRIPT
    assert svc.package_manager == PackageManager.NPM
    assert svc.default_port == 80
    assert analysis.suggested_deployment_targets[svc.name] == "vercel"


def test_detect_fastapi():
    target = FIXTURES_DIR / "sample-fastapi"
    analysis = analyze_repository(target)

    assert analysis.repo_name == "sample-fastapi"
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.FASTAPI
    assert svc.language == Language.PYTHON
    assert svc.default_port == 8000
    assert "uvicorn main:app" in svc.start_command
    assert analysis.suggested_deployment_targets[svc.name] == "render"


def test_detect_express():
    target = FIXTURES_DIR / "sample-express"
    analysis = analyze_repository(target)

    assert analysis.repo_name == "sample-express"
    assert len(analysis.services) == 1

    svc = analysis.services[0]
    assert svc.framework == FrameworkType.EXPRESS
    assert svc.language == Language.TYPESCRIPT
    assert svc.default_port == 4000  # scanned from process.env.PORT || 4000
    assert analysis.suggested_deployment_targets[svc.name] == "render"
