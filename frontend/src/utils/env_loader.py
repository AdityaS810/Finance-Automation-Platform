"""Environment variable loading helpers."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_environment_variables(env_file: str | Path | None = None) -> None:
    """Load environment variables from the default .env file or a provided path."""
    if env_file is None:
        repo_root_env = Path(__file__).resolve().parents[4] / ".env"
        frontend_env = Path(__file__).resolve().parents[2] / ".env"

        if repo_root_env.exists():
            load_dotenv(repo_root_env)
            return

        load_dotenv(frontend_env)
        return

    load_dotenv(Path(env_file))
