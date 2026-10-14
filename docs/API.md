# accra-language public API

accra-language is the common interface for plugins of every language
(compiled, interpreted, VM-based). It only holds what is language-agnostic:
what a language's interpreter, environment, imports or system packages are is
decided by the plugin, and running, tearing down and recording the build is
ACCRa's job.

Everything is exported from `accra_language`.

## Models

- `Config`: `repo`, `cwd`, `env`, `shell`, `timeout` (per subprocess), `build_timeout` (whole build, seconds; 0 or None means no budget), `language_version` (caller-chosen version, skips selection). `env` is how the caller passes environment variables to a plugin.
- `Dependency`: `name` and `version`, both opaque strings that only the plugin interprets. `name` is the identifier as the manifest wrote it (extras, VCS, URL or path specs included); `version` is the constraint in the language's native syntax with its operator, empty when unconstrained. Frozen, hashable and ordered by `(name, version)`.
- `AccraResult`: `dockerfile` (`Dockerfile` with `pre_dependencies_installation`, `dependencies_installation`, `post_dependencies_installation`), `errors` (every error, in the order encountered, install errors included), `success`
- `AccraError` and `AccraInstallError`, `AccraBuildError`, `AccraRunError`, `AccraAnalysisError`: `message`, `command`, `exit_code`, `stderr` (tail); `from_exception()` converts a subprocess exception. `AccraFailedDependencyInstallError` adds the failed `dependency`.

## Language

`Language(spec, env_manager=None)`. The env manager is chosen by name and version. Constructing a language without any env manager raises `ValueError` (a misconfigured plugin), and this is the only check; the methods below never raise.

- `detect()`: selects the language version; `bool | AccraError`
- `build()`: `AccraResult`; honours `Config.build_timeout` between steps and dependencies
- `env_manager`

## EnvironmentManager

A plugin must implement `pre_dependencies_installation()`, `_install_dependency()` and `choose_language_version()`; it may override `post_dependencies_installation()`.

- `present_manifests`, `selected_language_version`, `select_language_version()`: intersects what the manifests allow, the plugin's `choose_language_version(versions)` picks one
- `_get_dependencies()`, `_install_dependency(dependency)`: the plugin contract for collecting and installing dependencies; a plugin's own errors for an install go into the returned `AccraResult.errors`
- `subprocess_timeout()`: pass it to every `subprocess.run`, so `Config.build_timeout` caps each call
- `start_build_budget()`, `end_build_budget()`, `remaining_build_time()`, `build_budget_exhausted()`

## Manifest

`spec`, `detect()` (must not raise), `extract_dependencies()`, `get_supported_language_versions()`.

## Left to plugins and ACCRa

Not part of this interface: interpreter and environment locations, run environment variables (`Config.env`), import checking and observed imports, installing the project itself, teardown, system packages and readiness classification of dependencies.
