from functools import total_ordering
from typing import Self

from pydantic import BaseModel, ConfigDict


@total_ordering
class Dependency(BaseModel):
    """A dependency of the project, in a language-agnostic form.

    Both fields are opaque strings that only the plugin of the language
    interprets; ACCRa and accra-language pass them through unchanged.

    - `name`: the identifier exactly as the manifest (or ACCRa) wrote it.
      It may carry anything the language's package manager accepts in
      that position (extras, a VCS, URL or path spec, ...).
    - `version`: the constraint in the language's native syntax, operator
      included (e.g. `>=1.2` or `^1.2`). Empty means unconstrained.
    """

    model_config = ConfigDict(frozen=True)
    name: str
    version: str

    def __lt__(self, other: Self) -> bool:
        return (self.name, self.version) < (other.name, other.version)
