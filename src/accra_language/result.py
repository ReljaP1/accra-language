from typing import Self

from pydantic import BaseModel, Field

from .dependency import DependencyStatus
from .dockerfile import Dockerfile
from .error import AccraError


class AccraResult(BaseModel):
    """Contains the dockerfile instructions, and a list of errors in
    the order they were encountered.

    If the `success` field is False, an unrecoverable error was encountered such that the plugin cannot execute any further procedures.
    """

    dockerfile: Dockerfile = Field(default_factory=Dockerfile)
    errors: list[AccraError] = Field(default_factory=list)
    success: bool = True
    # Set by a plugin on the result of a single dependency install to
    # report it as satisfied, optional or system; otherwise derived.
    dependency_status: DependencyStatus | None = None

    def extend(self, other: Self):
        self.dockerfile.extend(other.dockerfile)
        self.errors.extend(other.errors)
        self.success &= other.success
