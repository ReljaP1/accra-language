from typing import NewType, Self

from pydantic import BaseModel, Field

DockerfileInstruction = NewType("DockerfileInstruction", str)


class Dockerfile(BaseModel):
    """A plugin's part of an image: the steps that set up its environment
    (`pre_dependencies_installation`, which includes installing the language's
    toolchain on the caller's base image), install the dependencies
    (`dependencies_installation`) and the project (`post_dependencies_installation`).

    The steps that are the same for every language (`FROM`, cloning the
    repository, the working directory, creating and switching the user) are
    the caller's: it composes one image from the plugins' parts."""

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

    def project_steps(self) -> list[DockerfileInstruction]:
        """The pre, dependency and post steps, in order."""
        return [
            *self.pre_dependencies_installation,
            *self.dependencies_installation,
            *self.post_dependencies_installation,
        ]

    def __str__(self) -> str:
        return (
            "\n".join(self.pre_dependencies_installation)
            + "\n"
            + "\n".join(self.dependencies_installation)
            + "\n"
            + "\n".join(self.post_dependencies_installation)
        )
