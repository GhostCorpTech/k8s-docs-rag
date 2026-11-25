import os
import subprocess
import sys
from pathlib import Path


def test_project_root_override_sets_pricing_and_tmp_inside_project(tmp_path: Path) -> None:
    environment = {**os.environ, "RAG_PROJECT_ROOT": str(tmp_path)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.config import Settings,PROJECT_ROOT; s=Settings(_env_file=None); print(PROJECT_ROOT); print(s.api_pricing_path)",
        ],
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        str(tmp_path),
        str(tmp_path / "data/billing/pricing.json"),
    ]
