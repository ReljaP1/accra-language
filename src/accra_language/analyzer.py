from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .config import Config
from .error import AccraAnalysisError, AccraInstallError


class Metrics(Enum):
    LOAD = "load"
    MEMORY = "memory"
    NETWORK = "network"
    PARALLELISM = "parallelism"


# NOTE profiles are frozen (hence hashable), so they hold tuples, not lists
class Profile(BaseModel):
    model_config = ConfigDict(frozen=True)
    duration_ms: int


class LoadProfile(Profile):
    cpu_percent: float
    gpu_percent: float


class MemoryProfile(Profile):
    peak_memory_mb: float
    total_memory_mb: float


class LibraryUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    count: int | None = None


class NetworkProfile(Profile):
    requires_network: bool = False
    dependencies: tuple[str, ...] = ()
    imports: tuple[LibraryUsage, ...] = ()
    api_calls: tuple[LibraryUsage, ...] = ()
    web_frameworks: tuple[LibraryUsage, ...] = ()


class ParallelismUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    used: bool = False
    modules: tuple[str, ...] = ()


class ParallelismProfile(Profile):
    multithreading: ParallelismUsage = Field(default_factory=ParallelismUsage)
    multiprocessing: ParallelismUsage = Field(default_factory=ParallelismUsage)


class AnalyzerSpec(BaseModel):
    binary: Path
    config: Config
    metrics: set[Metrics] = Field(default_factory=set)


class Analyzer(ABC):
    def __init__(self, spec: AnalyzerSpec):
        self.spec = spec

    @abstractmethod
    def install(self, config: Config | None = None) -> AccraInstallError | None: ...

    @abstractmethod
    def analyze(
        self, args: list[str], config: Config | None = None
    ) -> set[Profile] | AccraAnalysisError: ...
