from abc import ABC, abstractmethod

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
        """Returns True if the manifest is present in the project, False otherwise."""
        return False

    @abstractmethod
    def extract_dependencies(self) -> set[Dependency] | None:
        """Returns all dependencies listed in the manifest."""
        return None

    @abstractmethod
    def get_supported_language_versions(self) -> set[str] | AccraError:
        """Returns all language versions that are allowed by the manifest."""
        return None
