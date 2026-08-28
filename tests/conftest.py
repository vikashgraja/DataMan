import os
import tempfile
from pathlib import Path

# Setup coverage for subprocesses
# Pytest runs this before tests. We create a temporary sitecustomize.py
# that initializes coverage for any python subprocess launched during testing.


def pytest_configure(config):
    if "COVERAGE_RUN" in os.environ or os.environ.get("COVERAGE_PROCESS_START"):
        # We are running under coverage!
        temp_dir = tempfile.mkdtemp(prefix="dataman_coverage_")
        sitecustomize_path = Path(temp_dir) / "sitecustomize.py"
        sitecustomize_path.write_text(
            "import coverage\n"
            "try:\n"
            "    coverage.process_startup()\n"
            "except Exception:\n"
            "    pass\n"
        )

        if not os.environ.get("COVERAGE_PROCESS_START"):
            os.environ["COVERAGE_PROCESS_START"] = str(
                Path(__file__).parent.parent / "pyproject.toml"
            )

        # Ensure all subprocesses write to the same coverage file
        root_dir = Path(__file__).parent.parent
        os.environ["COVERAGE_FILE"] = str(root_dir / ".coverage")

        # Add the temp dir to PYTHONPATH so subprocesses pick up sitecustomize.py
        current_pythonpath = os.environ.get("PYTHONPATH", "")
        if current_pythonpath:
            os.environ["PYTHONPATH"] = f"{temp_dir}:{current_pythonpath}"
        else:
            os.environ["PYTHONPATH"] = str(temp_dir)
