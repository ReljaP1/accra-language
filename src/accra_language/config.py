from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)
    repo: str = ""
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    shell: bool = False
    # Timeout of each single subprocess.
    timeout: float | None = None
    # Time budget of the whole build, in seconds.
    build_timeout: float | None = None
    # Caller-chosen language version; when set, it is used instead of
    # selecting one from the manifests.
    language_version: str | None = None
