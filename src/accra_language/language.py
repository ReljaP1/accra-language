import subprocess
from abc import ABC

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
        """Raises ValueError when there is no environment manager to use:
        that is a misconfigured plugin, and it is checked only here."""

        self.spec = spec

        # NOTE the choice must not depend on set iteration order
        chosen = env_manager or next(
            iter(
                sorted(
                    spec.supported_env_managers,
                    key=lambda m: (m.spec.name, m.spec.version),
                )
            ),
            None,
        )
        if chosen is None:
            raise ValueError(f"language {spec.name!r} has no environment manager")

        self.env_manager: EnvironmentManager = chosen

    def detect(self) -> bool | AccraError:
        """Detects if the language is present in this project.

        Also selects the language version. Never raises."""

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
