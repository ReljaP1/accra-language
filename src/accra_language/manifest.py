from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel

from .config import Config
from .dependency import Dependency
from .error import AccraError


class ManifestSpec(BaseModel):
    name: str
    version: str
    config: Config


class Manifest(ABC):
    def __init__(self, spec: ManifestSpec):
        self.spec = spec

    @abstractmethod
    def detect(self) -> bool:
        """Returns True if the manifest is present in the project, False otherwise.

        Must not raise, and must be cheap and read-only: `Language.is_present()`
        relies on it."""
        return False

    def path(self) -> Path | None:
        """The manifest's file, when it is one (`cwd/<spec.name>` exists)."""
        if self.spec.config.cwd is None:
            return None
        candidate = Path(self.spec.config.cwd) / self.spec.name
        return candidate if candidate.is_file() else None

    @abstractmethod
    def extract_dependencies(self) -> set[Dependency] | None:
        """Returns all dependencies listed in the manifest."""
        return None

    @abstractmethod
    def get_supported_language_versions(self) -> set[str] | AccraError:
        """Returns all language versions that are allowed by the manifest."""
        return None
