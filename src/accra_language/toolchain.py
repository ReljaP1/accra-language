import os
import shlex
from pathlib import Path

from pydantic import BaseModel, ConfigDict

# What a toolchain's `install`, `environment` and `path` write for the home
# directory of the user that runs the plugin.
HOME_PLACEHOLDER = "{home}"


class Toolchain(BaseModel):
    """The system tools a plugin needs on the host, so a caller can
    check them or install them, e.g. when building an image.

    - `executables`: commands that must be on PATH (e.g. `uv`, `cargo`).
    - `packages`: Debian package names that provide them or what builds need.
    - `notes`: anything else, e.g. how a tool is installed.
    - `install`: shell commands (POSIX `sh`) that install the executables on a
      Debian host where `packages` are installed, run in order by the user
      that runs the plugin, with `environment` and `path` applied; each is
      safe to run again.
    - `environment`: the variables the executables need when they run (and
      the install commands after them), as (name, value) pairs; a tuple, so
      the model stays hashable.
    - `path`: directories to put in front of `PATH` for them.

    In `install`, `environment` and `path`, `{home}` stands for that user's
    home directory (`for_home`); in `install` (shell commands) it is
    shell-quoted, in `environment` and `path` it is inserted as is.
    """

    model_config = ConfigDict(frozen=True)
    executables: tuple[str, ...] = ()
    packages: tuple[str, ...] = ()
    notes: str = ""
    install: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    path: tuple[str, ...] = ()

    def for_home(self, home: Path | str) -> "Toolchain":
        """This toolchain with `{home}` replaced by `home`."""

        def render(value: str) -> str:
            return value.replace(HOME_PLACEHOLDER, str(home))

        def render_shell(value: str) -> str:
            return value.replace(HOME_PLACEHOLDER, shlex.quote(str(home)))

        return self.model_copy(
            update={
                "install": tuple(render_shell(c) for c in self.install),
                "environment": tuple((k, render(v)) for k, v in self.environment),
                "path": tuple(render(p) for p in self.path),
            }
        )

    def applied_to(self, env: dict[str, str]) -> dict[str, str]:
        """`env` with this toolchain's `environment` set and its `path` in
        front of `PATH` (each directory once, the toolchain's first, even when
        `PATH` has it later). Without a `PATH` in `env` it starts from
        `os.defpath`. Render `{home}` first."""

        result = {**env, **dict(self.environment)}
        if not self.path:
            return result
        current = [p for p in result.get("PATH", os.defpath).split(":") if p]
        merged = list(dict.fromkeys([*self.path, *current]))
        result["PATH"] = ":".join(merged)
        return result
