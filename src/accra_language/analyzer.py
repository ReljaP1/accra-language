from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .config import Config
from .environment_manager import EnvironmentManager
from .error import AccraAnalysisError
from .run import EntryPoint


class Metrics(Enum):
    LOAD = "load"
    MEMORY = "memory"
    NETWORK = "network"
    PARALLELISM = "parallelism"


# NOTE profiles are frozen (hence hashable), so they hold tuples, not lists
class Profile(BaseModel):
    """One measured run.

    - `duration_ms`: how long the run took.
    - `exit_code`: the run's exit code when it ended with an error but could
      still be measured (e.g. a profiler's report of a failing script); None
      for a run that succeeded.
    - `report`: the analyzer's report file, when it writes one.
    """

    model_config = ConfigDict(frozen=True)
    duration_ms: int
    exit_code: int | None = None
    report: str | None = None


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


class FileModules(BaseModel):
    """The modules one source file uses for a purpose (e.g. threading)."""

    model_config = ConfigDict(frozen=True)
    path: str
    modules: tuple[str, ...] = ()


class ParallelismUsage(BaseModel):
    model_config = ConfigDict(frozen=True)
    used: bool = False
    modules: tuple[str, ...] = ()
    # which files use them
    files: tuple[FileModules, ...] = ()


class ParallelismProfile(Profile):
    multithreading: ParallelismUsage = Field(default_factory=ParallelismUsage)
    multiprocessing: ParallelismUsage = Field(default_factory=ParallelismUsage)


class AnalyzerSpec(BaseModel):
    name: str
    config: Config
    metrics: set[Metrics] = Field(default_factory=set)


# what an analyzer returns: keyed by the entry point of each run it measured,
# None for a measure of the whole project (e.g. a static analysis of its
# sources)
AnalysisResults = dict[EntryPoint | None, set[Profile] | AccraAnalysisError]


class AnalysisRun(BaseModel):
    """One run of an entry point that an analyzer measures.

    - `entry_point`, `args`: what to run and with which arguments.
    - `env`: the whole process environment of the run, e.g. the caller's run
      environment plus the run's own variables; None for
      `env_manager.run_environment()` of the configured environment.
    - `timeout`: seconds the run may take; None for the plugin's subprocess
      timeout.
    - `output_dir`: where the analyzer writes its reports; None for a new
      temporary directory.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    entry_point: EntryPoint
    args: tuple[str, ...] = ()
    env: Mapping[str, str] | None = None
    timeout: float | None = None
    output_dir: Path | None = None


class Analyzer(ABC):
    """Measures a built project: from its sources, from runs of its entry
    points, or both.

    The analyzer makes whatever it needs usable by itself (e.g. a profiler) and
    keeps it out of the project's own environment and dependencies."""

    def __init__(self, spec: AnalyzerSpec):
        self.spec = spec

    @abstractmethod
    def analyze_project(
        self, env_manager: EnvironmentManager, runs: Sequence[AnalysisRun] = ()
    ) -> AnalysisResults:
        """Measures the project of `env_manager` (its `Config.cwd`, its
        manifests' dependencies), returning errors instead of raising.

        - A measure of the whole project (a static analysis) is keyed by None
          and needs no `runs`.
        - A measure of a run is keyed by that run's entry point, one run per
          entry point; an analyzer that measures runs gives nothing when there
          are none. A run that ended before it could be measured gives an
          `AccraNoSamplesError`."""
        ...
