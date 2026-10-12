from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    model_config = ConfigDict(frozen=True)
    repo: str = ""
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    shell: bool = False
    timeout: float | None = None
