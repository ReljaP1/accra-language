import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
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

# Orders language versions, most preferred first. The plugin defines what
# "best" means (newest, an LTS, ...) and how versions compare.
VersionOrder = Callable[[set[str]], Sequence[str]]


class EnvironmentManagerSpec(BaseModel):
    name: str
    version: str
    config: Config
    supported_manifests: set[InstanceOf[Manifest]] = Field(default_factory=set)
    # the plugin's preference among the versions a project allows; without
    # one, versions are ordered alphabetically
    version_order: VersionOrder | None = None


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

    def project_names(self) -> set[str]:
        """The names under which the project refers to itself (its modules,
        packages, crates or artifacts), never dependencies. Read-only."""
        return set()

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

    def supported_language_versions(self) -> set[str] | AccraError:
        """The language versions every present manifest allows, e.g.
        so a caller can retry with another one. A manifest may return any set
        (e.g. a `frozenset`); anything else is an error naming it."""

        if not self.present_manifests:
            return AccraInstallError(
                message="could not decide on a language version: no manifest present"
            )

        supported_versions_from_manifests: list[set[str]] = []

        for manifest in sorted(self.present_manifests, key=lambda m: m.spec.name):
            result: set[str] | AccraError = manifest.get_supported_language_versions()

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

    def ranked_language_versions(
        self, order: VersionOrder | None = None
    ) -> list[str] | AccraError:
        """The language versions every present manifest allows, most preferred
        first: by `order`, else the spec's `version_order`, else alphabetically.
        Versions `order` leaves out come last, alphabetically, and ones it
        invents are dropped, so every supported version appears once."""

        supported: set[str] | AccraError = self.supported_language_versions()
        if isinstance(supported, AccraError):
            return supported

        ordering = order or self.spec.version_order or sorted
        ranked = [v for v in dict.fromkeys(ordering(supported)) if v in supported]
        return ranked + sorted(supported - set(ranked))

    def select_language_version(
        self, order: VersionOrder | None = None
    ) -> str | AccraError:
        """Selects a language version that works for the project and its
        dependencies: the first of `ranked_language_versions(order)`, or the
        version the caller chose in `Config.language_version`."""

        if self.selected_language_version:
            return self.selected_language_version

        ranked: list[str] | AccraError = self.ranked_language_versions(order)
        if isinstance(ranked, AccraError):
            return ranked

        self.selected_language_version = ranked[0]
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
                result.errors.append(
                    AccraBuildError(message="build time budget exhausted")
                )
                result.success = False
                return result

            result.extend(self._install_dependency(dependency))
            if not result.success:
                return result

        return result

    def post_dependencies_installation(self) -> AccraResult:
        """Work needed to be done after all dependencies are present: installing
        or building the project itself, with the Dockerfile lines that do the
        same.

        A language that cannot run from its sources (it needs a compile step)
        returns `success=False` when that fails, which fails the build. A
        language that can run from its sources (e.g. Python) never does: it
        records what went wrong in `AccraResult.errors` and leaves `success`
        True, as the project is still usable."""
        return AccraResult()


# NOTE defined here and not in error.py to prevent circular dependency
# with environment_manager field.
class AccraFailedDependencyInstallError(AccraInstallError, BaseModel):
    dependency: Dependency
    environment_manager: InstanceOf[EnvironmentManager]
