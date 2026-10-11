from functools import total_ordering
from typing import Self

from pydantic import BaseModel, ConfigDict


@total_ordering
class Dependency(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    version: str

    def __lt__(self, other: Self) -> bool:
        return (self.name, self.version) < (other.name, other.version)
