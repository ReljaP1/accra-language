# accra-language public API

accra-language is the common interface for plugins of every language
(compiled, interpreted, VM-based). It only holds what is language-agnostic:
what a language's interpreter, environment, imports or system packages are is
decided by the plugin, and running, tearing down and recording the build is
ACCRa's job.

Everything is exported from `accra_language`.

A guide for plugin authors, with what ACCRa does with each hook, is in the ACCRa repository ([github.com/GiancarloSucci/ACCRa](https://github.com/GiancarloSucci/ACCRa)) at `docs/language-plugin-contract.md`.

## Models

- `Config`: `repo`, `cwd`, `env`, `shell`, `timeout` (per subprocess), `build_timeout` (whole build, seconds; 0 or None means no budget), `language_version` (caller-chosen version, skips selection), `image_workdir` (the project's directory in the composed image, `/project` by default; the caller clones there and makes it the working directory). `env` is how the caller passes environment variables to a plugin. A `Config` with an `env` is not hashable (the mapping is not). `shell` defaults to False.
- `Dependency`: `name` and `version`, both opaque strings that only the plugin interprets. `name` is the identifier as the manifest wrote it (extras, VCS, URL or path specs included); `version` is the constraint in the language's native syntax with its operator, empty when unconstrained. Frozen, hashable and ordered by `(name, version)`.
- `Dockerfile`: the plugin's part of an image: `pre_dependencies_installation` (installing the language's toolchain on the caller's Debian-based base image and setting up the environment), `dependencies_installation` and `post_dependencies_installation` (the project install); `project_steps()` joins them. The steps that are the same for every language (`FROM`, cloning, the working directory, the user) are the caller's, which composes one image from all present languages' parts on its own base image.
- `AccraResult`: `dockerfile` (a `Dockerfile`), `errors` (every error, in the order encountered, install errors included), `success`
- `EntryPoint`: `name`, `kind` (the plugin's own category, e.g. `script`, `module`, `binary`), `command` (the argv; the caller appends the arguments), `cwd`, `path` (the source file, if any). Frozen.
- `Toolchain`: `executables` (commands needed on PATH), `packages` (Debian packages that provide them or that builds need), `notes`, and how to install them: `install` (POSIX `sh` commands, run in order by the user that runs the plugin on a Debian host where `packages` are installed, each safe to run again), `environment` ((name, value) pairs the executables need) and `path` (directories to put in front of `PATH`); in those three, `{home}` stands for that user's home directory. `for_home(home)` renders it (in `install` the home is shell-quoted with `shlex.quote`, in `environment` and `path` it is inserted as is); `applied_to(env)` returns `env` with the environment set and the path in front of `PATH` (the toolchain's directories first, each once, even when `PATH` had them later; without a `PATH` in `env` it starts from `os.defpath`). Frozen; every field defaults to empty.
- `ScriptProfile`: how programs in the language are written and run, for a caller that describes them to a language model (one that writes a usage script, or works out an entry point's inputs): `language` (its name in prose), `file_suffix`, `code_fence` (the tag of a fenced code block), `conventions` (the language's own rules for a usage script, one sentence each), `run_command` (how a source file is run, `{file}` standing for it, e.g. `python {file}`; empty when a file is not run by itself), `cli_frameworks` (the command-line libraries whose `--help` output an entry point prints), `project_files` (the root files that say how the project is set up), `command_pattern` (a regular expression matching, in documentation, the command that runs a source file), `usage_patterns` (regular expressions, matched case-insensitively, marking documentation that tells how to install or run the language's programs) and `package_registry` (the name of the registry its packages are published to). Frozen; `language`, `file_suffix` and `code_fence` are required, the others default to empty.
- `RunDiagnosis`: what a failed run's output says is missing: `missing_dependencies`, `missing_files`, `missing_arguments` (each as the output names them). Frozen.
- `PackageDocs`: the project's package as the registry publishes it: `registry`, `package_name`, `source_url`, `summary`, `description_excerpt`, `project_urls` (a tuple of `(label, url)` pairs; a dict is accepted and converted, so the model is hashable). Frozen.
- `ApiSymbol`: one public symbol of the project: `module` (its source file, repository-relative), `name` (`Class.method` for a method), `kind` (`function`, `class`, `method`), `returns_value`, `is_driver` (it only starts the program). Frozen.
- `ScriptReading`: what a script does with the project's API, read without running it: `calls_project_api`, `calls_only_entry_point`, `builds_own_arguments`, `reports_returned_value`, `distinct_api_symbols`, `demonstrated_symbols`, `driver_symbols_called`, `launched_subprocess`, `parse_error`. Frozen.
- `AccraError` and `AccraInstallError`, `AccraBuildError`, `AccraRunError`, `AccraAnalysisError`: `message`, `command`, `exit_code`, `stderr` (tail); `from_exception(exc, message)` converts any exception into the error: it keeps the command, exit code and stderr tail when `exc` carries them and adds the timeout, exit code or exception to `message`. `AccraFailedDependencyInstallError` adds the failed `dependency` and the `environment_manager` that tried to install it; `AccraNoSamplesError` (an `AccraAnalysisError`) is a run that ended before an analyzer could measure it. `AccraUnsupportedError` (an `AccraError`) is what a hook's default returns when the plugin does not implement the capability (`check_script`, `read_script`, `entry_point_for`): it means "cannot be decided", not that the input is invalid. A `Language` selects its env manager lazily, on the first call that needs one (`detect()`, `build()`, `project_names()`, `entry_points()`, `entry_point_for()`, `analyze_project()`): the managers with a present manifest first, then by name and version (numerically). A manager passed to the constructor is used as it is.

## Language

`Language(spec, env_manager=None)`.

A plugin's `Language` subclass is constructed by its caller as `YourLanguage(config: Config, env_manager=None)`, and builds its own `LanguageSpec` from the `Config`. ACCRa calls it with one positional `Config`, on a project path that may not exist yet and with `env=None` (to read the toolchain before the tools are installed, and to probe `is_present()`), so the constructor must succeed with a missing `cwd`, `env=None` and none of the toolchain installed. It does read files: each `EnvironmentManager` constructor calls its manifests' `detect()`, which must be cheap, read-only and must not raise. Without an `env_manager` argument, the env manager is selected lazily by the first call that needs one, as described under Models; until then `env_manager` is None. A language without any env manager gets an `AccraError` from `detect()` and a failed `AccraResult` from `build()`. Neither the library nor a plugin logs or swallows exceptions: everything a caller needs comes back in the return values (an `AccraError`, or `AccraResult.errors`), and a plugin's hooks should not raise. An exception that does occur is a bug, and it propagates to the caller, which decides what a failed run means. A dependency a plugin fails to install is recorded in the returned `AccraResult.errors` as an `AccraFailedDependencyInstallError` (`success` stays True) and the build goes on with the others. A manifest's `get_supported_language_versions()` may return any set, or an `AccraError`, which is passed through; anything else is an `AccraInstallError` naming the manifest.

- `is_present()`: whether the project holds code of the language; cheap and read-only (no version selection, no tools, no writes). Default: a supported env manager found one of its manifests; a plugin with a catch-all manifest overrides it
- `detect()`: selects the env manager if no earlier call did, and the language version; `bool | AccraError`
- `build()`: `AccraResult`; honours `Config.build_timeout` between steps and dependencies. The project's own install or build is part of it (the env manager's `post_dependencies_installation()`)
- `entry_points()`: the env manager's `entry_points()`; `set[EntryPoint] | AccraError`
- `manifest_paths()`: the files of the present manifests
- `project_names()`: the env manager's, see below
- `entry_point_for(path)`: how to run one source file of the project, from the env manager; `EntryPoint | AccraError`
- `missing_tools()`: the `toolchain.executables` that are not on PATH (`Config.env`'s, else the process's)
- `analyze_project(metrics=None, runs=())`: runs every analyzer measuring one of `metrics` (all when None) on the project and on `runs` (`AnalysisRun`s, for the analyzers that measure runs); `dict` of analyzer name to its `AnalysisResults` (`dict` keyed by entry point, None for a measure of the whole project, to `set[Profile]` or `AccraAnalysisError`), in name order. Selects the env manager when no earlier call did; an analyzer sets itself up (e.g. a profiler) and keeps that out of the project's environment
- `script_profile()`, `check_script(path)`, `read_script(source, symbols, inferred_inputs=())`: usage scripts in the language. The profile (a `ScriptProfile`, None when the plugin has no usage scripts), a check that the script is valid without running it (None, else an `AccraError`), and what the script does with the project's public `symbols` (`ApiSymbol`s; `inferred_inputs` are values the caller handed it), a `ScriptReading` or an `AccraError`. They need no build. Unsupported by default
- `diagnose_run(output)`, `standard_modules()`, `package_documentation(name="", timeout=None)`: reading the language's own messages and documentation, for a caller that works out an entry point's inputs. A `RunDiagnosis` of a failed run's output, the standard library's module names (never dependencies to install), and the project's `PackageDocs` looked up by the name its manifests give, else `name` (None when there is none). They need no build. The defaults return nothing; an override should not raise.
- `env_manager`: the caller's, or the one the first call that needs it selected; None before that

`LanguageSpec.toolchain` (a `Toolchain`) declares the system tools the plugin needs; `LanguageSpec.analyzers` holds its analyzers.

## EnvironmentManager

A plugin must implement `pre_dependencies_installation()` and `_install_dependency()`; it may override `post_dependencies_installation()` (the project's own install or build, with its Dockerfile lines: a language that cannot run from its sources returns `success=False` when that fails, which fails the build; one that can, like Python, records the failure in `errors` and keeps `success` True) and give its `EnvironmentManagerSpec` a `version_order`.

- `present_manifests`, `selected_language_version`, `supported_language_versions()` (what every present manifest allows), `ranked_language_versions(order=None)` (those versions, most preferred first, by `order`, else the spec's `version_order`, else alphabetically; a version the order leaves out comes last and one it invents is dropped) and `select_language_version(order=None)` (the first of them; `order` is a callback taking the set of versions and returning them ordered, so the plugin, or a caller that retries without a version that failed, defines what "best" means)
- `_get_dependencies()`, `_install_dependency(dependency)`: the plugin contract for collecting and installing dependencies; a plugin's own errors for an install go into the returned `AccraResult.errors`
- `subprocess_timeout()`: pass it to every `subprocess.run`, so `Config.build_timeout` caps each call
- `start_build_budget()`, `end_build_budget()`, `remaining_build_time()`, `build_budget_exhausted()`

Optional, so a caller can use the built environment without knowing the language (the defaults describe a manager that offers none of it):

- `environment_path()`: where the built environment is; None by default
- `run_environment(base_env)`: the process environment for running commands in it; a copy of `base_env` by default
- `parse_dependency(spec)`: a `Dependency` from a native spec; by default the whole spec is the name
- `installed_dependencies()`, `uninstall_dependencies(dependencies)`: listing and removing what is installed; an error by default
- `project_names()`: the names under which the project refers to itself (modules, packages, crates, artifacts); empty by default
- `entry_point_for(path)`: how to run the source file `path`; an error by default
- `entry_points()`: what the built project can run and how; empty by default

## Analyzer

`Analyzer(spec)` with `AnalyzerSpec`: `name`, `config`, `metrics` (`Metrics`: load, memory, network, parallelism). It measures a built project from its sources, from runs of its entry points, or both. A plugin implements one method:

- `analyze_project(env_manager, runs=())`: measures the project of `env_manager` (`Config.cwd`, the manifests' dependencies) and returns an `AnalysisResults`: a `dict` keyed by entry point (None for a measure of the whole project, as a static analysis gives, which needs no `runs`) to `set[Profile] | AccraAnalysisError`. An analyzer that measures runs gives one result per run, keyed by the run's entry point (one run per entry point), and nothing when there are no runs. A run that ended before it could be measured gives an `AccraNoSamplesError`. The analyzer makes itself usable (e.g. installs a profiler) without touching the project's own environment or dependencies

An `AnalysisRun` is one run to measure: `entry_point`, `args`, `env` (the run's whole process environment; default `env_manager.run_environment()` of the configured one), `timeout` (its time limit; default the plugin's subprocess timeout) and `output_dir` (where the reports go; default a new temporary directory).

The caller chooses what to run and with which arguments. The profiles are `MemoryProfile` (`peak_memory_mb`, `total_memory_mb`), `LoadProfile` (`cpu_percent`, `gpu_percent`), `NetworkProfile` (`requires_network`, `dependencies`, and `imports`, `api_calls`, `web_frameworks` as `LibraryUsage(name, count)`) and `ParallelismProfile` (`multithreading` and `multiprocessing`, each a `ParallelismUsage`: `used`, `modules`, `files` as `FileModules(path, modules)`), all with `duration_ms`, `exit_code` (set when the run failed but could still be measured) and `report` (the analyzer's report file, if any); frozen.

## Manifest

`spec`, `detect()` (must not raise, and must be cheap and read-only: `Language.is_present()` relies on it), `path()` (the manifest's file, `cwd/<spec.name>` when it exists), `extract_dependencies()`, `get_supported_language_versions()`.

## Left to plugins and ACCRa

Not part of this interface: observed imports, teardown, and readiness classification of dependencies. The environment facts above are optional hooks, not a required part of the contract.

## Security obligations

- Never pass dependency strings (or any manifest-derived text) through a shell: run commands as argument lists. `Config.shell` is False by default; set it only for a command line the plugin fully controls.
- Quote every argument you put into a Dockerfile instruction, and keep control characters out of them.
- `Toolchain.install` commands are trusted: they run as `sh` at image build, as the user that runs the plugin, so they must come only from the plugin itself. `for_home` shell-quotes the home it substitutes.
- `ScriptProfile.command_pattern` and `usage_patterns` must compile as Python regular expressions; construction raises a `ValueError` naming the field otherwise.
- `AccraError.command` and `stderr` are stored as-is by callers; do not put credentials in commands (use `Config.env`).
