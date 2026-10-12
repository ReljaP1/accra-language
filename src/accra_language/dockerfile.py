from typing import NewType, Self

from pydantic import BaseModel, Field

DockerfileInstruction = NewType("DockerfileInstruction", str)


class Dockerfile(BaseModel):
    pre_dependencies_installation: list[DockerfileInstruction] = Field(
        default_factory=list
    )
    dependencies_installation: list[DockerfileInstruction] = Field(default_factory=list)
    post_dependencies_installation: list[DockerfileInstruction] = Field(
        default_factory=list
    )

    def extend(self, other: Self):
        self.pre_dependencies_installation.extend(other.pre_dependencies_installation)
        self.dependencies_installation.extend(other.dependencies_installation)
        self.post_dependencies_installation.extend(other.post_dependencies_installation)

    def __str__(self) -> str:
        return (
            "\n".join(self.pre_dependencies_installation)
            + "\n"
            + "\n".join(self.dependencies_installation)
            + "\n"
            + "\n".join(self.post_dependencies_installation)
        )
