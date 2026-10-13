# accra-language public API

These names are public and stable; a change to their shape is a breaking
change. Everything is exported from `accra_language` unless noted.

## Models

- `Config`: `repo`, `cwd`, `env`, `shell`, `timeout` (per subprocess), `build_timeout` (whole build, seconds), `language_version` (caller-chosen version, skips selection)
- `Dependency`: `name` (may carry extras, or be a VCS/URL/path spec), `version` (full specifier with operator; a bare version means `==`). Properties `specifier` (the spec to give the package manager), `base_name`, `extras`, `is_direct`
- `DependencyStatus`: `PENDING`, `INSTALLED`, `SATISFIED`, `OPTIONAL`, `SYSTEM`, `FAILED`
- `AccraResult`: `dockerfile` (`Dockerfile` with `pre_dependencies_installation`, `dependencies_installation`, `post_dependencies_installation`), `errors`, `success`, `dependency_status`
- `AccraError` (and `AccraInstallError`, `AccraBuildError`, `AccraRunError`, `AccraAnalysisError`): `message`, `command`, `exit_code`, `stderr`; `from_exception()` converts a subprocess exception
- `AccraFailedDependencyInstallError`: an install error with `dependency`, `environment_manager` and an optional `reason` (`DependencyFailureReason`)

## Language

`Language(spec, env_manager=None)`. The env manager is chosen by name and version; with none, `env_manager` is `None` and the methods below return errors. None of these raise.

- `detect()`: selects the language version; `bool | AccraError`
- `build()`: `AccraResult`; honours `Config.build_timeout` between steps and dependencies
- `environment_path()`, `interpreter_path()`, `run_environment()`
- `observed_imports()`, `install_project()`, `teardown()`, `check_import(module)`
- `env_manager`

## EnvironmentManager

Plugins implement `pre_dependencies_installation()` and `_install_dependency()`; they may override the rest.

- `present_manifests`, `selected_language_version`, `select_language_version()`
- `dependencies()`, `install_dependency(dependency)` (also usable after the build), `install_results` (the last `AccraResult`, with its Dockerfile instructions, per dependency), `dependency_statuses()`
- `environment_path()`, `interpreter_path()`, `run_environment()`: default `None`/`None`/`Config.env`; plugins override
- `install_project()`, `check_import()`, `teardown()`, `observed_imports()`: no-op defaults; plugins override
- `subprocess_timeout()`: pass it to every `subprocess.run`, so `Config.build_timeout` caps each call
- `start_build_budget()`, `end_build_budget()`, `remaining_build_time()`, `build_budget_exhausted()`

## Manifest

`spec`, `detect()`, `extract_dependencies()`, `get_supported_language_versions()`, `observed_imports()` (default: empty).
