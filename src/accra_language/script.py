import re
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, field_validator


class ScriptProfile(BaseModel):
    r"""How programs in the language are written and run, for a caller that
    describes them to a language model: one that writes a usage script for a
    project and runs it with `entry_point_for`, or one that works out
    the inputs of the project's entry points.

    - `language`: the language's name as used in prose, e.g. `Python`.
    - `file_suffix`: the script's file suffix, e.g. `.py`.
    - `code_fence`: the tag of a fenced code block in the language, e.g.
      `python`.
    - `conventions`: the language's own rules for such a script, each one
      sentence, e.g. which test frameworks not to use.
    - `run_command`: how a source file is run on the command line, `{file}`
      standing for the file, e.g. `python {file}`; empty when a file is not run
      by itself.
    - `cli_frameworks`: the command-line libraries whose `--help` output an
      entry point prints, e.g. `argparse`.
    - `project_files`: the files at the project root that say how the project
      is set up, e.g. `pyproject.toml`.
    - `command_pattern`: a regular expression (Python `re` syntax) matching,
      in documentation, the command that runs a source file, e.g.
      `python(?:3)?`.
    - `usage_patterns`: regular expressions (Python `re` syntax, matched
      case-insensitively) marking documentation that tells how to install or
      run the language's programs, e.g. `pip(?:3)?\s+install`.
    - `package_registry`: the name of the registry the language's packages are
      published to, e.g. `PyPI`.
    """

    model_config = ConfigDict(frozen=True)
    language: str
    file_suffix: str
    code_fence: str
    conventions: tuple[str, ...] = ()
    run_command: str = ""
    cli_frameworks: tuple[str, ...] = ()
    project_files: tuple[str, ...] = ()
    command_pattern: str = ""
    usage_patterns: tuple[str, ...] = ()
    package_registry: str = ""

    @field_validator("command_pattern", "usage_patterns")
    @classmethod
    def _compiles(cls, value, info):
        for pattern in (value,) if isinstance(value, str) else value:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(
                    f"{info.field_name} is not a valid regular expression "
                    f"({pattern!r}): {exc}"
                ) from exc
        return value


class RunDiagnosis(BaseModel):
    """What the output of a failed run of a project file says is missing, read as the language reports it.

    - `missing_dependencies`: the modules or packages the run could not find,
      as the output names them (e.g. `yaml` for Python's `No module named
      'yaml'`).
    - `missing_files`: the paths the run could not open.
    - `missing_arguments`: the command-line arguments the program said are
      required, as it listed them.
    """

    model_config = ConfigDict(frozen=True)
    missing_dependencies: tuple[str, ...] = ()
    missing_files: tuple[str, ...] = ()
    missing_arguments: tuple[str, ...] = ()


class PackageDocs(BaseModel):
    """The project's package as its language's registry publishes it, for a
    caller that has no `--help` output to go on.

    - `registry`: the registry's name, e.g. `PyPI`.
    - `package_name`: the name it was looked up by.
    - `source_url`: where the metadata was read.
    - `summary`: the one-line summary.
    - `description_excerpt`: the usage part of the long description, else its
      beginning.
    - `project_urls`: the published links as (label, url) pairs, a tuple so
      the model stays hashable; a dict is accepted and converted.
    """

    model_config = ConfigDict(frozen=True)
    registry: str
    package_name: str
    source_url: str
    summary: str = ""
    description_excerpt: str = ""
    project_urls: tuple[tuple[str, str], ...] = ()

    @field_validator("project_urls", mode="before")
    @classmethod
    def _urls_from_mapping(cls, value):
        if isinstance(value, Mapping):
            return tuple((str(k), str(v)) for k, v in value.items())
        return value


class ApiSymbol(BaseModel):
    """One public symbol of the project.

    - `module`: the repository-relative path of its source file.
    - `name`: its name in that file (`Class.method` for a method).
    - `kind`: `function`, `class` or `method`.
    - `returns_value`: whether calling it gives a result worth reporting.
    - `is_driver`: whether it only starts the program (e.g. `main`).
    """

    model_config = ConfigDict(frozen=True)
    module: str
    name: str
    kind: str = "function"
    returns_value: bool = True
    is_driver: bool = False


class ScriptReading(BaseModel):
    """What a script does with the project's own API, read from its source
    without running it.

    - `calls_project_api`: it calls a non-driver symbol in its own process.
    - `calls_only_entry_point`: it calls driver symbols only.
    - `builds_own_arguments`: an argument of such a call is built by the script,
      not copied from an inferred input.
    - `reports_returned_value`: a printed or asserted value depends on a call's
      result.
    - `distinct_api_symbols`: how many value-returning symbols it calls.
    - `demonstrated_symbols`, `driver_symbols_called`: their names.
    - `launched_subprocess`: it starts another process.
    - `parse_error`: why the source could not be read, when it could not.
    """

    model_config = ConfigDict(frozen=True)
    calls_project_api: bool = False
    calls_only_entry_point: bool = False
    builds_own_arguments: bool = False
    reports_returned_value: bool = False
    distinct_api_symbols: int = 0
    demonstrated_symbols: tuple[str, ...] = ()
    driver_symbols_called: tuple[str, ...] = ()
    launched_subprocess: bool = False
    parse_error: str | None = None
