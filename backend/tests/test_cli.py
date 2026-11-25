from pathlib import Path

from typer.testing import CliRunner

from app.cli import app
from app.config import Settings


def test_eval_cli_passes_parallel_workers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("app.cli.get_settings", lambda: Settings(_env_file=None))

    def run(settings, with_faithfulness=False, workers=1):
        assert with_faithfulness is True
        assert workers == 2
        return tmp_path

    monkeypatch.setattr("app.cli.run_evaluation", run)
    result = CliRunner().invoke(app, ["evaluate", "--with-faithfulness", "--workers", "2"])
    assert result.exit_code == 0, result.output
