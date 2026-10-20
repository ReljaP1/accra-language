from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class Config(BaseModel):
    """Settings a caller gives a plugin. Frozen, but a Config whose `env` is
    set is not hashable (the mapping is not); do not use it as a dict key.

    `shell` defaults to False: commands are run as argument lists. Set it only
    for a command line you fully control, never with untrusted strings.
    """

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
    # The project's directory in the image the caller composes (it clones the
    # repository there and makes it the working directory); plugins use it
    # in their Dockerfile steps.
    image_workdir: str = "/project"
