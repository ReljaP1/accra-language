import subprocess
from abc import ABC
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, Field, InstanceOf

from .analyzer import Analyzer
from .config import Config
from .environment_manager import EnvironmentManager
from .error import AccraBuildError, AccraError
from .result import AccraResult


class LanguageSpec(BaseModel):
    name: str
    version: str
    config: Config
    supported_env_managers: set[InstanceOf[EnvironmentManager]] = Field(
        default_factory=set
    )
    analyzers: set[InstanceOf[Analyzer]] = Field(default_factory=set)


class Language(ABC):
    def __init__(
        self, spec: LanguageSpec, env_manager: EnvironmentManager | None = None
    ):
        self.spec = spec
        # NOTE the choice must not depend on set iteration order
        self.env_manager: EnvironmentManager | None = env_manager or next(
            iter(
                sorted(
                    spec.supported_env_managers,
                    key=lambda m: (m.spec.name, m.spec.version),
                )
            ),
            None,
        )

    def detect(self) -> bool | AccraError:
        """Detects if the language is present in this project.

        Also selects the language version. Never raises."""

        if not self.env_manager:
            return AccraBuildError(message="no environment manager available")

        try:
            selected_language_version: str | AccraError = (
                self.env_manager.select_language_version()
            )
        except (subprocess.SubprocessError, OSError) as exc:
            return AccraBuildError.from_exception(
                exc, "could not select a language version"
            )

        if isinstance(selected_language_version, AccraError):
            return selected_language_version

        return bool(selected_language_version)

    def build(self) -> AccraResult:
        """Builds the project and determines the Dockerfile instructions.

        Never raises."""

        if not self.env_manager:
            return AccraResult(
                errors=[AccraBuildError(message="no environment manager available")],
                success=False,
            )

        steps = (
            (
                "pre-dependencies installation",
                self.env_manager.pre_dependencies_installation,
            ),
            ("dependencies installation", self.env_manager.dependencies_installation),
            (
                "post-dependencies installation",
                self.env_manager.post_dependencies_installation,
            ),
        )

        result = AccraResult()
        self.env_manager.start_build_budget()
        try:
            for name, step in steps:
                if self.env_manager.build_budget_exhausted():
                    result.errors.append(
                        AccraBuildError(
                            message=f"build time budget exhausted before {name}"
                        )
                    )
                    result.success = False
                    break

                try:
                    result.extend(step())
                except (subprocess.SubprocessError, OSError) as exc:
                    result.errors.append(
                        AccraBuildError.from_exception(exc, f"{name} failed")
                    )
                    result.success = False

                if not result.success:
                    break
        finally:
            self.env_manager.end_build_budget()

        return result

    def environment_path(self) -> Path | None:
        return self.env_manager.environment_path() if self.env_manager else None

    def interpreter_path(self) -> Path | None:
        return self.env_manager.interpreter_path() if self.env_manager else None

    def run_environment(self) -> Mapping[str, str]:
        """The environment variables needed to run the project."""
        if not self.env_manager:
            return dict(self.spec.config.env or {})
        return self.env_manager.run_environment()

    def observed_imports(self) -> set[str]:
        return self.env_manager.observed_imports() if self.env_manager else set()

    def install_project(self) -> AccraResult:
        return self._guarded("install of the project", "install_project")

    def teardown(self) -> AccraResult:
        return self._guarded("teardown", "teardown")

    def check_import(self, module: str) -> bool | AccraError:
        """Checks that `module` imports in the built environment. Never raises."""
        if not self.env_manager:
            return AccraBuildError(message="no environment manager available")
        try:
            return self.env_manager.check_import(module)
        except (subprocess.SubprocessError, OSError) as exc:
            return AccraBuildError.from_exception(
                exc, f"import check of {module} failed"
            )

    def _guarded(self, name: str, method: str) -> AccraResult:
        if not self.env_manager:
            return AccraResult(
                errors=[AccraBuildError(message="no environment manager available")],
                success=False,
            )
        try:
            return getattr(self.env_manager, method)()
        except (subprocess.SubprocessError, OSError) as exc:
            return AccraResult(
                errors=[AccraBuildError.from_exception(exc, f"{name} failed")],
                success=False,
            )
