from abc import ABC, abstractmethod

from pydantic import BaseModel, Field, InstanceOf

from .config import Config
from .dependency import Dependency
from .error import AccraError, AccraInstallError
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
        self.present_manifests: set[Manifest] = {
            manifest for manifest in spec.supported_manifests if manifest.detect()
        }
        self.selected_language_version: str | None = None

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
        """Selects a language version that works for the project and its dependencies."""

        if self.selected_language_version:
            return self.selected_language_version

        supported_versions_from_manifests: list[set[str]] | AccraError = []
        supported_versions: set[str] = set()

        for manifest in self.present_manifests:
            result: set[str] | AccraError = manifest.get_supported_language_versions()
            match result:
                case set():
                    supported_versions_from_manifests.append(result)

                case AccraError():
                    return result

        supported_versions = set.intersection(*supported_versions_from_manifests)
        if not supported_versions:
            return AccraInstallError(message="could not decide on a language version")

        # NOTE versions are sorted alphabetically, override this
        # method for different selection of versions
        self.selected_language_version = next(iter(sorted(supported_versions)))
        return self.selected_language_version

    def dependencies_installation(self) -> AccraResult:
        """Installs all project dependencies."""

        result = AccraResult()

        dependencies: set[Dependency] | None = self._get_dependencies()

        if not dependencies:
            return result

        dependencies_sorted: list[Dependency] = sorted(dependencies)

        for dependency in dependencies_sorted:
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
