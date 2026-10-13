import re
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, InstanceOf

from .config import Config
from .dependency import Dependency, DependencyStatus
from .error import AccraBuildError, AccraError, AccraInstallError
from .manifest import Manifest
from .result import AccraResult


class EnvironmentManagerSpec(BaseModel):
    name: str
    version: str
    config: Config
    supported_manifests: set[InstanceOf[Manifest]] = Field(default_factory=set)


def _version_key(version: str) -> tuple[tuple[int, ...], str]:
    """Orders versions numerically, so that "3.10" is higher than "3.5"."""
    return tuple(int(part) for part in re.findall(r"\d+", version)), version


def _manifest_present(manifest: Manifest) -> bool:
    # a failing detect() only loses that manifest
    try:
        return manifest.detect()
    except Exception:  # noqa: BLE001
        return False


class EnvironmentManager(ABC):
    def __init__(self, spec: EnvironmentManagerSpec):
        self.spec = spec
        self.present_manifests: set[Manifest] = {
            manifest
            for manifest in spec.supported_manifests
            if _manifest_present(manifest)
        }
        self.selected_language_version: str | None = spec.config.language_version
        # Result of the last install of each dependency, including its
        # Dockerfile instructions. Kept after build() returns.
        self.install_results: dict[Dependency, AccraResult] = {}
        self._deadline: float | None = None

    # -- environment accessors ---------------------------------------

    def environment_path(self) -> Path | None:
        """The directory of the built environment (e.g. the venv), or None
        when there is none or it is not known. Override in plugins."""
        return None

    def interpreter_path(self) -> Path | None:
        """The interpreter / runtime of the built environment, or None.
        Override in plugins."""
        return None

    def run_environment(self) -> Mapping[str, str]:
        """The environment variables needed to run the project inside the
        built environment (e.g. VIRTUAL_ENV, PATH). Defaults to the
        configured `Config.env`. Override in plugins."""
        return dict(self.spec.config.env or {})

    # -- build time budget -------------------------------------------

    def start_build_budget(self) -> None:
        """Starts the whole-build time budget of `Config.build_timeout`."""
        budget = self.spec.config.build_timeout
        self._deadline = None if budget is None else time.monotonic() + budget

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

    def dependencies(self) -> set[Dependency]:
        """All dependencies of the present manifests."""
        return self._get_dependencies() or set()

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

    def select_language_version(self) -> str | AccraError:
        """Selects a language version that works for the project and its dependencies.

        The highest version supported by every present manifest is chosen.
        Never raises."""

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

        # NOTE the highest version is selected, override this method for
        # a different selection of versions
        self.selected_language_version = max(supported_versions, key=_version_key)
        return self.selected_language_version

    def install_dependency(self, dependency: Dependency) -> AccraResult:
        """Installs a single dependency, also after the build.

        Never raises: a failed install is reported as an
        `AccraFailedDependencyInstallError` naming the dependency. The
        result is kept in `install_results`."""

        try:
            result = self._install_dependency(dependency)
        except (subprocess.SubprocessError, OSError) as exc:
            result = AccraResult(
                errors=[
                    AccraInstallError.from_exception(
                        exc,
                        f"could not install dependency: {dependency.name}{dependency.version}",
                    )
                ]
            )

        result.errors = [
            self._attribute_error(error, dependency) for error in result.errors
        ]
        self.install_results[dependency] = result
        return result

    def _attribute_error(self, error: AccraError, dependency: Dependency) -> AccraError:
        if isinstance(error, AccraInstallError) and not isinstance(
            error, AccraFailedDependencyInstallError
        ):
            return AccraFailedDependencyInstallError(
                **error.model_dump(),
                dependency=dependency,
                environment_manager=self,
            )
        return error

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

            result.extend(self.install_dependency(dependency))
            if not result.success:
                return result

        return result

    def dependency_statuses(self) -> dict[Dependency, DependencyStatus]:
        """The readiness of every dependency of the project.

        A plugin reports satisfied, optional or system through
        `AccraResult.dependency_status`; otherwise an install is
        installed, or failed when it carries an error."""

        statuses: dict[Dependency, DependencyStatus] = {}
        for dependency in self.dependencies() | set(self.install_results):
            install = self.install_results.get(dependency)
            if install is None:
                statuses[dependency] = DependencyStatus.PENDING
            elif install.dependency_status is not None:
                statuses[dependency] = install.dependency_status
            elif any(
                isinstance(error, AccraFailedDependencyInstallError)
                for error in install.errors
            ):
                statuses[dependency] = DependencyStatus.FAILED
            else:
                statuses[dependency] = DependencyStatus.INSTALLED
        return statuses

    def post_dependencies_installation(self) -> AccraResult:
        """Work needed to be done after all dependencies are present.

        This could include building / compiling the project."""
        return AccraResult()

    # -- optional capabilities, override in plugins --------------------

    def install_project(self) -> AccraResult:
        """Installs the project itself into the environment (for layouts
        that the dependency installation does not cover). Not called by
        `Language.build()`."""
        return AccraResult()

    def check_import(self, module: str) -> bool | AccraError:
        """Checks that `module` can be imported in the built environment."""
        return AccraBuildError(message="import check is not supported")

    def teardown(self) -> AccraResult:
        """Removes what the build created (environment, helper tools).
        Its result's Dockerfile is ignored."""
        return AccraResult()

    def observed_imports(self) -> set[str]:
        """The modules the project is observed to import, from all present manifests."""
        imports: set[str] = set()
        for manifest in self.present_manifests:
            try:
                imports |= manifest.observed_imports()
            except (subprocess.SubprocessError, OSError):
                continue
        return imports


# NOTE defined here and not in error.py to prevent circular dependency
# with environment_manager field.
class DependencyFailureReason(Enum):
    UNRESOLVABLE = "unresolvable"  # no matching distribution
    BUILD_FROM_SOURCE = "build_from_source"  # needs a compiler or headers
    SYSTEM_PACKAGE = "system_package"  # needs a system package or daemon
    OTHER = "other"


class AccraFailedDependencyInstallError(AccraInstallError, BaseModel):
    dependency: Dependency
    reason: DependencyFailureReason | None = None
    environment_manager: InstanceOf[EnvironmentManager]
