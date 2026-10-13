import re
from enum import Enum
from functools import total_ordering
from typing import Self

from pydantic import BaseModel, ConfigDict

# A version starting with one of these already carries its operator (or
# is a PEP 508 "@ url" / "; marker" suffix).
_SPECIFIER_PREFIXES = ("<", ">", "=", "!", "~", "@", ";")
_DIRECT_PREFIXES = ("git+", "hg+", "svn+", "bzr+", "file:", ".", "/")


@total_ordering
class Dependency(BaseModel):
    """A dependency of the project.

    `name` may carry extras (e.g. `uvicorn[standard]`), or be a direct
    VCS, URL or path spec (with an empty `version`). `version` holds the
    full specifier including its operator (e.g. `>=1.2`); a bare version
    (e.g. `1.2`) is read as `==1.2`."""

    model_config = ConfigDict(frozen=True)
    name: str
    version: str

    def __lt__(self, other: Self) -> bool:
        return (self.name, self.version) < (other.name, other.version)

    @property
    def specifier(self) -> str:
        """The spec to hand to the package manager."""

        version = self.version.strip()
        if not version:
            return self.name
        if version.startswith(_SPECIFIER_PREFIXES):
            separator = " " if version[0] in "@;" else ""
            return f"{self.name}{separator}{version}"
        return f"{self.name}=={version}"

    @property
    def is_direct(self) -> bool:
        """True for VCS, URL and path specs."""

        return "://" in self.name or self.name.startswith(_DIRECT_PREFIXES)

    @property
    def base_name(self) -> str:
        """The name without extras, specifiers and markers."""

        return re.split(r"[\[<>=!~;@\s]", self.name.strip(), maxsplit=1)[0]

    @property
    def extras(self) -> tuple[str, ...]:
        match = re.search(r"\[([^\]]*)\]", self.name)
        if not match:
            return ()
        return tuple(
            extra.strip() for extra in match.group(1).split(",") if extra.strip()
        )


class DependencyStatus(Enum):
    PENDING = "pending"  # not attempted yet
    INSTALLED = "installed"
    SATISFIED = "satisfied"  # already present, nothing installed
    OPTIONAL = "optional"  # not required to run the project
    SYSTEM = "system"  # needs a system package, not installable here
    FAILED = "failed"
