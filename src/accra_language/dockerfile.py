from typing import NewType, Self

from pydantic import BaseModel, Field

DockerfileInstruction = NewType("DockerfileInstruction", str)


class Dockerfile(BaseModel):
    """A plugin's part of an image.

    - `base_image`: the image the plugin builds on when its language is the
      only one (e.g. `python:3.12`); empty for no preference.
    - `toolchain_installation`: the steps that install the plugin's toolchain
      on another (Debian-based) base, used when a different base is chosen.
    - `pre_dependencies_installation`, `dependencies_installation`,
      `post_dependencies_installation`: the project steps (environment setup,
      dependencies, project install).

    The steps that are the same for every language (`FROM`, cloning the
    repository, the working directory, creating and switching the user) are
    the caller's: it composes one image from the plugins' parts."""

    base_image: str = ""
    toolchain_installation: list[DockerfileInstruction] = Field(default_factory=list)
    pre_dependencies_installation: list[DockerfileInstruction] = Field(
        default_factory=list
    )
    dependencies_installation: list[DockerfileInstruction] = Field(default_factory=list)
    post_dependencies_installation: list[DockerfileInstruction] = Field(
        default_factory=list
    )

    def extend(self, other: Self):
        if not self.base_image:
            self.base_image = other.base_image
        self.toolchain_installation.extend(other.toolchain_installation)
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
        """The plugin's part on its own base (no caller steps)."""
        head = [f"FROM {self.base_image}"] if self.base_image else []
        return (
            "\n".join(head + self.pre_dependencies_installation)
            + "\n"
            + "\n".join(self.dependencies_installation)
            + "\n"
            + "\n".join(self.post_dependencies_installation)
        )
