from pathlib import Path

from pydantic import BaseModel, ConfigDict


class EntryPoint(BaseModel):
    """Something a built project can run.

    - `name`: what the plugin calls it (a script path, module, binary or
      class name).
    - `kind`: the plugin's own category, e.g. `script`, `module`,
      `console_script`, `binary`, `main_class`. Callers only display it.
    - `command`: the argv that runs it; the caller appends the arguments.
    - `cwd`: the directory to run it in, None for the project directory.
    - `path`: the source file it starts from, when there is one.
    """

    model_config = ConfigDict(frozen=True)
    name: str
    kind: str
    command: tuple[str, ...]
    cwd: Path | None = None
    path: Path | None = None
