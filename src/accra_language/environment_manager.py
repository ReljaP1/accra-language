import subprocess
import time
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, InstanceOf

from .config import Config
from .dependency import Dependency
from .error import AccraBuildError, AccraError, AccraInstallError
from .manifest import Manifest
from .result import AccraResult


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

        What "best" means (newest, an LTS, ...) and how versions order
        depends on the language, so each plugin defines it."""
        ...

    def select_language_version(self) -> str | AccraError:
        """Selects a language version that works for the project and its dependencies.

        The versions allowed by every present manifest are intersected and
        the plugin chooses one with `choose_language_version`. Never raises."""

        if self.selected_language_version:
            return self.selected_language_version

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
            except (subprocess.SubprocessError, OSError) as exc:
                return AccraInstallError.from_exception(
                    exc,
                    f"could not get supported language versions from {manifest.spec.name}",
                )

            match result:
                case set():
                    supported_versions_from_manifests.append(result)

                case AccraError():
                    return result

        if not supported_versions_from_manifests:
            return AccraInstallError(message="could not decide on a language version")

        supported_versions: set[str] = set.intersection(
            *supported_versions_from_manifests
        )
        if not supported_versions:
            return AccraInstallError(message="could not decide on a language version")

        self.selected_language_version = self.choose_language_version(
            supported_versions
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
        """Work needed to be done after all dependencies are present.

        This could include building / compiling the project."""
        return AccraResult()


# NOTE defined here and not in error.py to prevent circular dependency
# with environment_manager field.
class AccraFailedDependencyInstallError(AccraInstallError, BaseModel):
    dependency: Dependency
    environment_manager: InstanceOf[EnvironmentManager]
