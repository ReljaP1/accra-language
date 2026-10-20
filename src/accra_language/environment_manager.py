import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from pathlib import Path

from pydantic import BaseModel, Field, InstanceOf

from .config import Config
from .dependency import Dependency
from .error import (
    AccraBuildError,
    AccraError,
    AccraInstallError,
    AccraUnsupportedError,
)
from .manifest import Manifest
from .result import AccraResult
from .run import EntryPoint

_logger = logging.getLogger(__name__)


class EnvironmentManagerSpec(BaseModel):
    name: str
    version: str
    config: Config
    supported_manifests: set[InstanceOf[Manifest]] = Field(default_factory=set)


class EnvironmentManager(ABC):
    def __init__(self, spec: EnvironmentManagerSpec):
        self.spec = spec
        # NOTE Manifest.detect() must not raise
        self.present_manifests: set[Manifest] = {
            manifest for manifest in spec.supported_manifests if manifest.detect()
        }
        self.selected_language_version: str | None = spec.config.language_version
        self._deadline: float | None = None

    # -- build time budget -------------------------------------------

    def start_build_budget(self) -> None:
        """Starts the whole-build time budget of `Config.build_timeout`."""
        budget = self.spec.config.build_timeout
        self._deadline = time.monotonic() + budget if budget else None

    def resume_build_budget(self, seconds: float) -> None:
        """Restarts the build budget with `seconds` left (what an earlier
        `build()` did not use), for `install_project()`."""
        self._deadline = time.monotonic() + seconds

    def end_build_budget(self) -> None:
        self._deadline = None

    def remaining_build_time(self) -> float | None:
        """Seconds left of the build budget (never negative), None if unbounded."""
        if self._deadline is None:
            return None
        return max(0.0, self._deadline - time.monotonic())

    def build_budget_exhausted(self) -> bool:
        remaining = self.remaining_build_time()
        return remaining is not None and remaining <= 0

    def subprocess_timeout(self) -> float | None:
        """The timeout to give the next subprocess: `Config.timeout`, capped
        by what is left of the build budget. Plugins should pass this to
        `subprocess.run` so that the budget bounds each call."""
        limits = [
            limit
            for limit in (self.spec.config.timeout, self.remaining_build_time())
            if limit is not None
        ]
        return min(limits) if limits else None

    # -- environment facts (optional) --------------------------------
    #
    # What a caller needs to use the built environment without knowing the
    # language. The defaults describe an environment manager that
    # offers none of it; a plugin overrides what its language has.

    def environment_path(self) -> Path | None:
        """Where the built environment lives, None when there is no such place."""
        return None

    def run_environment(self, base_env: Mapping[str, str]) -> dict[str, str]:
        """The process environment that runs commands inside the built
        environment, from `base_env` (e.g. a venv's `VIRTUAL_ENV` and `PATH`)."""
        return dict(base_env)

    def parse_dependency(self, spec: str) -> Dependency | AccraError:
        """A dependency from a spec in the language's native syntax (e.g. a
        requirement line); the default keeps the spec whole as the name."""
        return Dependency(name=spec.strip(), version="")

    def installed_dependencies(self) -> set[Dependency] | AccraError:
        """What is installed in the built environment, versions included."""
        return AccraInstallError(
            message=f"{self.spec.name} cannot list installed dependencies"
        )

    def uninstall_dependencies(self, dependencies: Iterable[Dependency]) -> AccraResult:
        """Removes `dependencies` from the built environment."""
        return AccraResult(
            errors=[
                AccraInstallError(
                    message=f"{self.spec.name} cannot uninstall dependencies"
                )
            ],
            success=False,
        )

    def is_usable(self, dependency: Dependency) -> bool | AccraError:
        """Whether the built environment can use `dependency` (for Python,
        whether it imports)."""
        return AccraUnsupportedError(
            message=f"{self.spec.name} cannot check whether a dependency is usable"
        )

    def install_project(self) -> AccraResult:
        """Installs the project itself into the built environment,
        with the Dockerfile lines that do the same; nothing by default."""
        return AccraResult()

    def project_names(self) -> set[str]:
        """The names under which the project refers to itself (its modules,
        packages, crates or artifacts), never dependencies. Read-only;
        `Language.project_names()` gives empty if it raises."""
        return set()

    def has_project_install(self) -> bool:
        """Whether the project has an install step of its own (e.g. a build
        manifest), so `install_project()` installs it; without one the project
        runs from its sources. False by default."""
        return False

    # -- running (optional) ------------------------------------------

    def entry_points(self) -> set[EntryPoint] | AccraError:
        """What the built project can run and how; empty when the
        environment manager does not know."""
        return set()

    def entry_point_for(self, path: Path) -> EntryPoint | AccraError:
        """How to run the project's source file `path` (e.g. as a script or as
        a module), whether or not `entry_points()` lists it."""
        return AccraUnsupportedError(message=f"{self.spec.name} cannot run {path}")

    # -- dependencies ------------------------------------------------

    def _get_dependencies(self) -> set[Dependency] | None:
        """Collects the dependencies from all present manifests into a single set."""

        dependencies: set[Dependency] = set()

        for manifest in self.present_manifests:
            manifest_dependencies: set[Dependency] | None = (
                manifest.extract_dependencies()
            )
            if manifest_dependencies:
                dependencies.update(manifest_dependencies)

        if not dependencies:
            return None

        return dependencies

    @abstractmethod
    def pre_dependencies_installation(self) -> AccraResult:
        """Work needed to be done before dependencies are installed.

        This could include installation of the language toolchain (e.g. language compiler / interpreter, package manager) and setup of the environment (e.g. venv for Python)."""
        return AccraResult()

    @abstractmethod
    def _install_dependency(self, dependency: Dependency) -> AccraResult:
        """Installs a dependency."""
        return AccraResult()

    @abstractmethod
    def choose_language_version(self, versions: set[str]) -> str:
        """Chooses one of the language versions that every present manifest
        allows (`versions` is never empty).

        The plugin defines what "best" means (newest, an LTS, ...) and how
        versions order."""
        ...

    def supported_language_versions(self) -> set[str] | AccraError:
        """The language versions every present manifest allows, e.g.
        so a caller can retry with another one. Never raises. A manifest may return
        any set (e.g. a `frozenset`); anything else is an error naming it."""

        if not self.present_manifests:
            return AccraInstallError(
                message="could not decide on a language version: no manifest present"
            )

        supported_versions_from_manifests: list[set[str]] = []

        for manifest in sorted(self.present_manifests, key=lambda m: m.spec.name):
            try:
                result: set[str] | AccraError = (
                    manifest.get_supported_language_versions()
                )
            except Exception as exc:  # noqa: BLE001 never-raises contract
                return AccraInstallError.from_exception(
                    exc,
                    f"could not get supported language versions from {manifest.spec.name}",
                )

            match result:
                case AbstractSet():
                    supported_versions_from_manifests.append(set(result))

                case AccraError():
                    return result

                case _:
                    return AccraInstallError(
                        message=(
                            f"{manifest.spec.name} returned "
                            f"{type(result).__name__} as its supported language "
                            "versions, expected a set"
                        )
                    )

        if not supported_versions_from_manifests:
            return AccraInstallError(message="could not decide on a language version")

        supported_versions: set[str] = set.intersection(
            *supported_versions_from_manifests
        )
        if not supported_versions:
            return AccraInstallError(message="could not decide on a language version")

        return supported_versions

    def select_language_version(self) -> str | AccraError:
        """Selects a language version that works for the project and its dependencies.

        The plugin chooses one of `supported_language_versions()` with
        `choose_language_version`. Never raises."""

        if self.selected_language_version:
            return self.selected_language_version

        supported_versions: set[str] | AccraError = self.supported_language_versions()
        if isinstance(supported_versions, AccraError):
            return supported_versions

        try:
            chosen = self.choose_language_version(supported_versions)
        except Exception as exc:  # noqa: BLE001 never-raises contract
            return AccraInstallError.from_exception(
                exc, "could not choose a language version"
            )
        if not isinstance(chosen, str) or chosen not in supported_versions:
            return AccraInstallError(
                message=(
                    f"could not choose a language version: {chosen!r} is not one "
                    f"of {sorted(supported_versions)}"
                )
            )
        self.selected_language_version = chosen
        _logger.debug(
            "env manager %s: chose language version %s of %s",
            self.spec.name,
            chosen,
            sorted(supported_versions),
        )
        return self.selected_language_version

    def dependencies_installation(self) -> AccraResult:
        """Installs all project dependencies."""

        result = AccraResult()

        dependencies: set[Dependency] | None = self._get_dependencies()

        if not dependencies:
            return result

        dependencies_sorted: list[Dependency] = sorted(dependencies)

        for dependency in dependencies_sorted:
            if self.build_budget_exhausted():
                _logger.debug("build budget exhausted during dependencies")
                result.errors.append(
                    AccraBuildError(message="build time budget exhausted")
                )
                result.success = False
                return result

            try:
                installed = self._install_dependency(dependency)
            except Exception as exc:  # noqa: BLE001 never-raises contract
                # one dependency must not cost the others: record it failed
                # (`success` stays True) and go on
                base = AccraInstallError.from_exception(
                    exc, f"could not install dependency: {dependency.name}"
                )
                result.errors.append(
                    AccraFailedDependencyInstallError(
                        **base.model_dump(),
                        dependency=dependency,
                        environment_manager=self,
                    )
                )
                continue
            result.extend(installed)
            if not result.success:
                return result

        return result

    def post_dependencies_installation(self) -> AccraResult:
        """Work needed to be done after all dependencies are present.

        This could include building / compiling the project."""
        return AccraResult()


# NOTE defined here and not in error.py to prevent circular dependency
# with environment_manager field.
class AccraFailedDependencyInstallError(AccraInstallError, BaseModel):
    dependency: Dependency
    environment_manager: InstanceOf[EnvironmentManager] | None = None
