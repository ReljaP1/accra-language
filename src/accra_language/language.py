from abc import ABC

from pydantic import BaseModel, Field, InstanceOf

from .analyzer import Analyzer
from .config import Config
from .environment_manager import EnvironmentManager
from .error import AccraError
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
        self.env_manager: EnvironmentManager | None = env_manager or next(
            iter(spec.supported_env_managers)
        )

    def detect(self) -> bool | AccraError:
        """Detects if the language is present in this project."""
        selected_language_version: str | AccraError = (
            self.env_manager.select_language_version()
        )
        if isinstance(selected_language_version, AccraError):
            return selected_language_version

        return bool(selected_language_version)

    def build(self) -> AccraResult:
        """Builds the project and determines the Dockerfile instructions."""

        result = AccraResult()

        if not self.env_manager:
            return result

        result.extend(self.env_manager.pre_dependencies_installation())
        if not result.success:
            return result

        result.extend(self.env_manager.dependencies_installation())
        if not result.success:
            return result

        result.extend(self.env_manager.post_dependencies_installation())
        return result
