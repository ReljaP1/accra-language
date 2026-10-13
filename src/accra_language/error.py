import subprocess
from abc import ABC
from typing import Self

from pydantic import BaseModel

_STDERR_TAIL_CHARS = 2000


class AccraError(ABC, BaseModel):
    message: str
    command: list[str] | str | None = None
    exit_code: int | None = None
    stderr: str | None = None

    @classmethod
    def from_exception(cls, exc: Exception, message: str) -> Self:
        """Converts an exception raised by a subprocess call into an error.

        The command, exit code and the tail of stderr are kept when the
        exception carries them."""

        command: list[str] | str | None = None
        exit_code: int | None = None
        stderr: str | bytes | None = None

        match exc:
            case subprocess.TimeoutExpired():
                command, stderr = _command(exc.cmd), exc.stderr
                message = f"{message} (timed out after {exc.timeout}s)"
            case subprocess.CalledProcessError():
                command, exit_code, stderr = (
                    _command(exc.cmd),
                    exc.returncode,
                    exc.stderr,
                )
                message = f"{message} (exit code {exc.returncode})"
            case OSError():
                command = str(exc.filename) if exc.filename else None
                message = f"{message} ({type(exc).__name__}: {exc})"
            case _:
                message = f"{message} ({type(exc).__name__}: {exc})"

        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")

        return cls(
            message=message,
            command=command,
            exit_code=exit_code,
            stderr=stderr[-_STDERR_TAIL_CHARS:] if stderr else None,
        )


def _command(cmd: object) -> list[str] | str | None:
    if isinstance(cmd, str):
        return cmd
    if isinstance(cmd, (list, tuple)):
        return [str(part) for part in cmd]
    return None


class AccraInstallError(AccraError):
    pass


class AccraBuildError(AccraError):
    pass


class AccraRunError(AccraError):
    pass


class AccraAnalysisError(AccraError):
    pass
