import functools
import logging
import re
import shutil
from abc import ABC
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, InstanceOf

from .analyzer import Analyzer, Metrics, Profile, ProjectAnalyzer
from .config import Config
from .environment_manager import EnvironmentManager
from .error import (
    AccraAnalysisError,
    AccraBuildError,
    AccraError,
    AccraRunError,
    AccraUnsupportedError,
)
from .result import AccraResult
from .run import EntryPoint
from .script import (
    ApiSymbol,
    PackageDocs,
    RunDiagnosis,
    ScriptProfile,
    ScriptReading,
)
from .toolchain import Toolchain


class LanguageSpec(BaseModel):
    name: str
    version: str
    config: Config
    supported_env_managers: set[InstanceOf[EnvironmentManager]] = Field(
        default_factory=set
    )
    analyzers: set[InstanceOf[Analyzer]] = Field(default_factory=set)
    # static analyses of the project's sources (no entry point, no run)
    project_analyzers: set[InstanceOf[ProjectAnalyzer]] = Field(default_factory=set)
    # the system tools the plugin needs
    toolchain: Toolchain = Field(default_factory=Toolchain)


_logger = logging.getLogger(__name__)


def _version_key(version: str) -> tuple[tuple[int, int | str, str], ...]:
    """A sort key comparing the numeric parts of a version as numbers ("10" after
    "9"); a part that is not a number compares as text after the numbers."""

    parts: list[tuple[int, int | str, str]] = []
    for part in re.split(r"[.\-+_]", version):
        parts.append((0, int(part), "") if part.isdigit() else (1, 0, part))
    return tuple(parts)


def _never_raises(default: Callable[[], Any]) -> Callable[[Callable], Callable]:
    """Makes a read-only hook return `default()` when it raises any
    `Exception`, so that a plugin's bug or a malformed project file cannot
    escape through it."""

    def decorate(function: Callable) -> Callable:
        if getattr(function, "_never_raises", False):
            return function

        @functools.wraps(function)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            try:
                return function(*args, **kwargs)
            except Exception:
                _logger.debug("%s raised", function.__qualname__, exc_info=True)
                return default()

        guarded._never_raises = True  # type: ignore[attr-defined]
        return guarded

    return decorate


# the read-only hooks a caller relies on not raising, and what each gives
# when it does (also for a plugin's override of them)
_GUARDED_HOOKS: dict[str, Callable[[], Any]] = {
    "is_present": lambda: False,
    "manifest_paths": set,
    "covered_manifests": set,
    "missing_tools": tuple,
}


class Language(ABC):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for name, default in _GUARDED_HOOKS.items():
            if name in cls.__dict__ and callable(cls.__dict__[name]):
                setattr(cls, name, _never_raises(default)(cls.__dict__[name]))

    def __init__(
        self, spec: LanguageSpec, env_manager: EnvironmentManager | None = None
    ):
        self.spec = spec

        # the caller's choice, else picked by `detect()` (or `build()`)
        self.env_manager: EnvironmentManager | None = env_manager
        # analyzers installed into the built environment, by name
        self._installed_analyzers: set[str] = set()
        # analyzers whose install failed in this build, with the error
        self._failed_analyzers: dict[str, AccraError] = {}
        # build budget left when `build()` ended; None when unbounded
        self._build_time_left: float | None = None

    def _select_env_manager(self) -> EnvironmentManager | AccraError:
        """The environment manager to use: the caller's, else the first of the
        supported ones by name and version. An AccraError when there is none."""

        if self.env_manager is None:
            # NOTE the choice must not depend on set iteration order: managers
            # holding a manifest of the project first, then by name and version
            candidates = sorted(
                self.spec.supported_env_managers,
                key=lambda m: (
                    not m.present_manifests,
                    m.spec.name,
                    _version_key(m.spec.version),
                ),
            )
            self.env_manager = next(iter(candidates), None)
            if self.env_manager is not None:
                _logger.debug(
                    "language %s: selected env manager %s %s (%d candidate(s); "
                    "manifests present: %s)",
                    self.spec.name,
                    self.env_manager.spec.name,
                    self.env_manager.spec.version,
                    len(candidates),
                    bool(self.env_manager.present_manifests),
                )

        if self.env_manager is None:
            return AccraBuildError(
                message=f"language {self.spec.name!r} has no environment manager"
            )

        return self.env_manager

    @_never_raises(_GUARDED_HOOKS["is_present"])
    def is_present(self) -> bool:
        """Whether the project holds code of this language.

        Cheap and read-only: it selects no version, runs no tool and writes
        nothing, so a caller can ask every available plugin about a project.
        The default is that a supported env manager found one of its
        manifests; a plugin with a catch-all manifest overrides it. Never
        raises: False when it would."""

        return any(
            env_manager.present_manifests
            for env_manager in self.spec.supported_env_managers
        )

    @_never_raises(_GUARDED_HOOKS["manifest_paths"])
    def manifest_paths(self) -> set[Path]:
        """The files of the language's present manifests."""

        return {
            path
            for env_manager in self.spec.supported_env_managers
            for manifest in env_manager.present_manifests
            if (path := manifest.path()) is not None
        }

    @_never_raises(_GUARDED_HOOKS["covered_manifests"])
    def covered_manifests(self) -> set[Path]:
        """Other languages' manifest files that this language's build also
        handles, e.g. the `Cargo.toml` a Python build backend compiles. A caller leaves them out of the other languages' builds.
        Empty by default; read-only, never raises."""

        return set()

    def install_project(self) -> AccraResult:
        """The env manager's `install_project()`; after `build()`.
        When `Config.build_timeout` is set, it runs under what `build()` left
        of that budget (`subprocess_timeout()` is capped by it, and an
        exhausted budget is an error). Never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return AccraResult(errors=[env_manager], success=False)
        left = self._build_time_left
        try:
            if left is not None:
                if left <= 0:
                    _logger.debug("build budget exhausted before project install")
                    return AccraResult(
                        errors=[
                            AccraBuildError(
                                message="build time budget exhausted before "
                                "project install"
                            )
                        ],
                        success=False,
                    )
                env_manager.resume_build_budget(left)
            return env_manager.install_project()
        except Exception as exc:  # noqa: BLE001 never-raises contract
            return AccraResult(
                errors=[AccraBuildError.from_exception(exc, "project install failed")],
                success=False,
            )
        finally:
            if left is not None:
                env_manager.end_build_budget()

    def project_names(self) -> set[str]:
        """The env manager's `project_names()`. Read-only, never
        raises: empty when it would."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return set()
        try:
            return set(env_manager.project_names())
        except Exception:  # noqa: BLE001 never-raises contract
            return set()

    def has_project_install(self) -> bool:
        """The env manager's `has_project_install()`. Read-only,
        never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return False
        try:
            return bool(env_manager.has_project_install())
        except Exception:  # noqa: BLE001 never-raises contract
            return False

    @_never_raises(_GUARDED_HOOKS["missing_tools"])
    def missing_tools(self) -> tuple[str, ...]:
        """The toolchain executables that are not on PATH. Never
        raises: when the lookup itself raises, the result is empty (nothing is
        reported missing, and a real absence then surfaces as a build error)."""

        path = (self.spec.config.env or {}).get("PATH")
        return tuple(
            executable
            for executable in self.spec.toolchain.executables
            if shutil.which(executable, path=path) is None
        )

    def entry_points(self) -> set[EntryPoint] | AccraError:
        """What the project can run and how, from the env manager.

        Meaningful after `build()`; never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return env_manager

        try:
            return env_manager.entry_points()
        except Exception as exc:  # noqa: BLE001 never-raises contract
            return AccraBuildError.from_exception(exc, "could not list entry points")

    def entry_point_for(self, path: Path) -> EntryPoint | AccraError:
        """How to run the project's source file `path`, from the env manager. Never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return env_manager
        try:
            return env_manager.entry_point_for(Path(path))
        except Exception as exc:  # noqa: BLE001 never-raises contract
            return AccraRunError.from_exception(exc, f"cannot run {path}")

    # -- usage scripts (optional) ------------------------------
    #
    # What a caller needs to write a usage script for the project, check it and
    # tell what it demonstrates, without knowing the language. The defaults say
    # that the language has no such support; a plugin overrides all three. They
    # need no build: the language's knowledge, not the environment, answers.

    def script_profile(self) -> ScriptProfile | None:
        """How a usage script in the language is written; None when the plugin
        does not support usage scripts."""

        return None

    def check_script(self, path: Path) -> AccraError | None:
        """Whether the script at `path` is valid in the language (e.g. parses),
        without running it: None when it is, else why not."""

        return AccraUnsupportedError(message=f"{self.spec.name} cannot check scripts")

    def read_script(
        self,
        source: str,
        symbols: Iterable[ApiSymbol],
        inferred_inputs: Iterable[str] = (),
    ) -> ScriptReading | AccraError:
        """What the script `source` does with the project's public `symbols`,
        read without running it. `inferred_inputs` are values the caller
        handed to the script (arguments copied from them are not the script's
        own). Never raises."""

        return AccraUnsupportedError(message=f"{self.spec.name} cannot read scripts")

    # -- reading runs and documentation (optional) -------------
    #
    # What a caller that works out an entry point's inputs needs to read the
    # language's own messages and documentation. The defaults read nothing;
    # none of them needs a build.

    def diagnose_run(self, output: str) -> RunDiagnosis:
        """What the output of a failed run of a project file says is missing;
        nothing by default. Never raises."""

        return RunDiagnosis()

    def standard_modules(self) -> frozenset[str]:
        """The names of the language's standard library modules, which are
        never dependencies to install; none by default."""

        return frozenset()

    def package_documentation(
        self, name: str = "", timeout: float | None = None
    ) -> PackageDocs | None:
        """The project's package as the language's registry publishes it,
        looked up by the name its manifests give, else `name`; None when there
        is none or it cannot be read within `timeout` seconds. Never raises."""

        return None

    def _matching_analyzers(self, metrics: set[Metrics] | None) -> list[Analyzer]:
        return [
            analyzer
            for analyzer in sorted(self.spec.analyzers, key=lambda a: a.spec.name)
            if metrics is None or analyzer.spec.metrics & metrics
        ]

    def install_analyzers(
        self, metrics: set[Metrics] | None = None
    ) -> dict[str, AccraError | None]:
        """Installs every analyzer that measures one of `metrics` (all when
        None) once, after `build()`; keyed by analyzer name, each value None or
        the error that makes the analyzer unusable here. Never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        results: dict[str, AccraError | None] = {}
        for analyzer in self._matching_analyzers(metrics):
            name = analyzer.spec.name
            if isinstance(env_manager, AccraError):
                results[name] = env_manager
                continue
            if name in self._installed_analyzers:
                _logger.debug("analyzer %s already installed; reused", name)
                results[name] = None
                continue
            if name in self._failed_analyzers:
                _logger.debug("analyzer %s failed to install earlier; skipped", name)
                results[name] = self._failed_analyzers[name]
                continue
            try:
                error = analyzer.install(env_manager)
            except Exception as exc:  # noqa: BLE001 never-raises contract
                error = AccraAnalysisError.from_exception(
                    exc, f"{name} could not be installed"
                )
            if error is None:
                _logger.debug("analyzer %s installed", name)
                self._installed_analyzers.add(name)
            else:
                _logger.debug("analyzer %s install failed: %s", name, error.message)
                self._failed_analyzers[name] = error
            results[name] = error
        return results

    def analyze(
        self,
        entry_point: EntryPoint,
        args: list[str],
        metrics: set[Metrics] | None = None,
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        output_dir: Path | None = None,
    ) -> dict[str, set[Profile] | AccraError]:
        """Runs every analyzer that measures one of `metrics` (all when None)
        on `entry_point` with `args`, installing it first unless
        `install_analyzers()` did. `env`, `timeout` and `output_dir`
        are passed to each analyzer (see `Analyzer.analyze`).

        Keyed by analyzer name, in name order; each value is the analyzer's
        profiles or its error. Meaningful after `build()`; never raises."""

        installed: dict[str, AccraError | None] = self.install_analyzers(metrics)
        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        options = {
            key: value
            for key, value in (
                ("env", env),
                ("timeout", timeout),
                ("output_dir", output_dir),
            )
            if value is not None
        }
        results: dict[str, set[Profile] | AccraError] = {}
        for analyzer in self._matching_analyzers(metrics):
            name = analyzer.spec.name
            if installed.get(name) is not None:
                results[name] = installed[name]
                continue
            if isinstance(env_manager, AccraError):
                results[name] = env_manager
                continue
            try:
                results[name] = analyzer.analyze(
                    entry_point, args, env_manager, **options
                )
            except Exception as exc:  # noqa: BLE001 never-raises contract
                results[name] = AccraAnalysisError.from_exception(exc, f"{name} failed")
        return results

    def analyze_project(
        self, metrics: set[Metrics] | None = None
    ) -> dict[str, set[Profile] | AccraError]:
        """Runs every project analyzer that measures one of `metrics` (all when
        None) on the project's sources; keyed by analyzer name, in name order,
        each value its profiles or its error. Needs the env manager (after
        `detect()` or `build()`); never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        results: dict[str, set[Profile] | AccraError] = {}
        for analyzer in sorted(self.spec.project_analyzers, key=lambda a: a.spec.name):
            if metrics is not None and not (analyzer.spec.metrics & metrics):
                continue
            if isinstance(env_manager, AccraError):
                results[analyzer.spec.name] = env_manager
                continue
            try:
                results[analyzer.spec.name] = analyzer.analyze_project(env_manager)
            except Exception as exc:  # noqa: BLE001 never-raises contract
                results[analyzer.spec.name] = AccraAnalysisError.from_exception(
                    exc, f"{analyzer.spec.name} failed"
                )
        return results

    def detect(self) -> bool | AccraError:
        """Detects if the language is present in this project.

        Picks the environment manager and selects the language version.
        Never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return env_manager

        try:
            selected_language_version: str | AccraError = (
                env_manager.select_language_version()
            )
        except Exception as exc:  # noqa: BLE001 never-raises contract
            return AccraBuildError.from_exception(
                exc, "could not select a language version"
            )

        if isinstance(selected_language_version, AccraError):
            _logger.debug(
                "language %s: no version selected: %s",
                self.spec.name,
                selected_language_version.message,
            )
            return selected_language_version

        _logger.debug(
            "language %s: chosen version %s", self.spec.name, selected_language_version
        )
        return bool(selected_language_version)

    def build(self) -> AccraResult:
        """Builds the project and determines the Dockerfile instructions.

        Picks the environment manager when `detect()` has not. Never raises."""

        env_manager: EnvironmentManager | AccraError = self._select_env_manager()
        if isinstance(env_manager, AccraError):
            return AccraResult(errors=[env_manager], success=False)

        steps = (
            (
                "pre-dependencies installation",
                env_manager.pre_dependencies_installation,
            ),
            ("dependencies installation", env_manager.dependencies_installation),
            (
                "post-dependencies installation",
                env_manager.post_dependencies_installation,
            ),
        )

        result = AccraResult()
        self._installed_analyzers.clear()
        self._failed_analyzers.clear()
        self._build_time_left = None
        env_manager.start_build_budget()
        try:
            for name, step in steps:
                if env_manager.build_budget_exhausted():
                    _logger.debug("build budget exhausted before %s", name)
                    result.errors.append(
                        AccraBuildError(
                            message=f"build time budget exhausted before {name}"
                        )
                    )
                    result.success = False
                    break

                _logger.debug("build step: %s", name)
                try:
                    result.extend(step())
                except Exception as exc:  # noqa: BLE001 never-raises contract
                    result.errors.append(
                        AccraBuildError.from_exception(exc, f"{name} failed")
                    )
                    result.success = False

                if not result.success:
                    _logger.debug("build step %s failed; later steps skipped", name)
                    break
        finally:
            self._build_time_left = env_manager.remaining_build_time()
            env_manager.end_build_budget()

        return result
