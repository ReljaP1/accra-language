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
- `Dockerfile`: the plugin's part of an image. `base_image` (the image it builds on when its language is the only one), `toolchain_installation` (the steps that install its toolchain on another, Debian-based base) and the project steps `pre_dependencies_installation`, `dependencies_installation`, `post_dependencies_installation` (`project_steps()` joins them). The steps that are the same for every language (`FROM`, cloning, the working directory, the user) are the caller's, which composes one image from all present languages' parts.
- `AccraResult`: `dockerfile` (a `Dockerfile`), `errors` (every error, in the order encountered, install errors included), `success`
- `EntryPoint`: `name`, `kind` (the plugin's own category, e.g. `script`, `module`, `binary`), `command` (the argv; the caller appends the arguments), `cwd`, `path` (the source file, if any). Frozen.
- `Toolchain`: `executables` (commands needed on PATH), `packages` (Debian packages that provide them or that builds need), `notes`, and how to install them: `install` (POSIX `sh` commands, run in order by the user that runs the plugin on a Debian host where `packages` are installed, each safe to run again), `environment` ((name, value) pairs the executables need) and `path` (directories to put in front of `PATH`); in those three, `{home}` stands for that user's home directory. `for_home(home)` renders it (in `install` the home is shell-quoted with `shlex.quote`, in `environment` and `path` it is inserted as is); `applied_to(env)` returns `env` with the environment set and the path in front of `PATH` (the toolchain's directories first, each once, even when `PATH` had them later; without a `PATH` in `env` it starts from `os.defpath`). Frozen; every field defaults to empty.
- `ScriptProfile`: how programs in the language are written and run, for a caller that describes them to a language model (one that writes a usage script, or works out an entry point's inputs): `language` (its name in prose), `file_suffix`, `code_fence` (the tag of a fenced code block), `conventions` (the language's own rules for a usage script, one sentence each), `run_command` (how a source file is run, `{file}` standing for it, e.g. `python {file}`; empty when a file is not run by itself), `cli_frameworks` (the command-line libraries whose `--help` output an entry point prints), `project_files` (the root files that say how the project is set up), `command_pattern` (a regular expression matching, in documentation, the command that runs a source file), `usage_patterns` (regular expressions, matched case-insensitively, marking documentation that tells how to install or run the language's programs) and `package_registry` (the name of the registry its packages are published to). Frozen; `language`, `file_suffix` and `code_fence` are required, the others default to empty.
- `RunDiagnosis`: what a failed run's output says is missing: `missing_dependencies`, `missing_files`, `missing_arguments` (each as the output names them). Frozen.
- `PackageDocs`: the project's package as the registry publishes it: `registry`, `package_name`, `source_url`, `summary`, `description_excerpt`, `project_urls` (a tuple of `(label, url)` pairs; a dict is accepted and converted, so the model is hashable). Frozen.
- `ApiSymbol`: one public symbol of the project: `module` (its source file, repository-relative), `name` (`Class.method` for a method), `kind` (`function`, `class`, `method`), `returns_value`, `is_driver` (it only starts the program). Frozen.
- `ScriptReading`: what a script does with the project's API, read without running it: `calls_project_api`, `calls_only_entry_point`, `builds_own_arguments`, `reports_returned_value`, `distinct_api_symbols`, `demonstrated_symbols`, `driver_symbols_called`, `launched_subprocess`, `parse_error`. Frozen.
- `AccraError` and `AccraInstallError`, `AccraBuildError`, `AccraRunError`, `AccraAnalysisError`: `message`, `command`, `exit_code`, `stderr` (tail); `from_exception(exc, message)` converts any exception into the error: it keeps the command, exit code and stderr tail when `exc` carries them and adds the timeout, exit code or exception to `message`. `AccraFailedDependencyInstallError` adds the failed `dependency` (and, optionally, the `environment_manager`); `AccraNoSamplesError` (an `AccraAnalysisError`) is a run that ended before an analyzer could measure it. `AccraUnsupportedError` (an `AccraError`) is what a hook's default returns when the plugin does not implement the capability (`check_script`, `read_script`, `entry_point_for`, `is_usable`): it means "cannot be decided", not that the input is invalid. A `Language` selects its env manager lazily, on the first call that needs one (`detect()`, `build()`, `install_project()`, `project_names()`, `has_project_install()`, `entry_points()`, `entry_point_for()`, `install_analyzers()`, `analyze()`, `analyze_project()`): the managers with a present manifest first, then by name and version (numerically). A manager passed to the constructor is used as it is. The library logs decisions at debug level to the `accra_language` logger (a `NullHandler`, no values of `Config.env`).

## Language

`Language(spec, env_manager=None)`. Construction never raises.

A plugin's `Language` subclass is constructed by its caller as `YourLanguage(config: Config, env_manager=None)`, and builds its own `LanguageSpec` from the `Config`. ACCRa calls it with one positional `Config`, on a project path that may not exist yet and with `env=None` (to read the toolchain before the tools are installed, and to probe `is_present()`), so the constructor must succeed with a missing `cwd`, `env=None` and none of the toolchain installed. It does read files: each `EnvironmentManager` constructor calls its manifests' `detect()`, which must be cheap, read-only and must not raise. Without an `env_manager` argument, the env manager is selected lazily by the first call that needs one, as described under Models; until then `env_manager` is None. A language without any env manager gets an `AccraError` from `detect()` and a failed `AccraResult` from `build()`. The methods below, as `Language` defines them, do not raise: every guard converts any `Exception` (not only subprocess and OS errors) into the hook's error value. Of a plugin's overrides, only those of `is_present`, `manifest_paths`, `covered_manifests` and `missing_tools` are wrapped the same way; any other override must not raise either, which is the plugin's obligation. When a read-only hook raises, `is_present()` gives False, `manifest_paths()`, `covered_manifests()` and `project_names()` empty sets, `missing_tools()` an empty tuple (nothing reported missing; a real absence then fails the build) and `has_project_install()` False. A dependency whose install raises is recorded as an `AccraFailedDependencyInstallError` (`success` stays True) and the build goes on with the others. A manifest's `get_supported_language_versions()` may return any set, or an `AccraError`, which is passed through; anything else is an `AccraInstallError` naming the manifest.

- `is_present()`: whether the project holds code of the language; cheap and read-only (no version selection, no tools, no writes). Default: a supported env manager found one of its manifests; a plugin with a catch-all manifest overrides it
- `detect()`: selects the env manager if no earlier call did, and the language version; `bool | AccraError`
- `build()`: `AccraResult`; honours `Config.build_timeout` between steps and dependencies. It also resets the installed-analyzer cache (including remembered install failures)
- `install_project()` runs under the part of `Config.build_timeout` that `build()` left: the budget is not kept running between the two calls, `build()` records the time left and `install_project()` resumes with it (and ends it again), so `subprocess_timeout()` is capped by it; an exhausted budget is an `AccraBuildError`. Without a build budget nothing changes
- `entry_points()`: the env manager's `entry_points()`; `set[EntryPoint] | AccraError`
- `manifest_paths()`: the files of the present manifests; `covered_manifests()`: other languages' manifest files this language's build also handles (e.g. the `Cargo.toml` a Python build backend compiles), which a caller leaves out of the other languages' builds; empty by default
- `install_project()`, `project_names()`, `has_project_install()`: the env manager's, see below
- `entry_point_for(path)`: how to run one source file of the project, from the env manager; `EntryPoint | AccraError`
- `missing_tools()`: the `toolchain.executables` that are not on PATH (`Config.env`'s, else the process's)
- `install_analyzers(metrics=None)`: installs every analyzer measuring one of `metrics` (all when None) once; `dict` of analyzer name to None or the error that makes it unusable here
- `analyze(entry_point, args, metrics=None, *, env=None, timeout=None, output_dir=None)`: runs every analyzer measuring one of `metrics`, installing it first unless `install_analyzers()` did; `dict` of analyzer name to its `set[Profile]` or `AccraError`, in name order. The options are passed to each analyzer
- `analyze_project(metrics=None)`: runs every project analyzer measuring one of `metrics` (all when None) on the project's sources; `dict` of analyzer name to its `set[Profile]` or `AccraError`, in name order. Selects the env manager when no earlier call did
- `script_profile()`, `check_script(path)`, `read_script(source, symbols, inferred_inputs=())`: usage scripts in the language. The profile (a `ScriptProfile`, None when the plugin has no usage scripts), a check that the script is valid without running it (None, else an `AccraError`), and what the script does with the project's public `symbols` (`ApiSymbol`s; `inferred_inputs` are values the caller handed it), a `ScriptReading` or an `AccraError`. They need no build. Unsupported by default
- `diagnose_run(output)`, `standard_modules()`, `package_documentation(name="", timeout=None)`: reading the language's own messages and documentation, for a caller that works out an entry point's inputs. A `RunDiagnosis` of a failed run's output, the standard library's module names (never dependencies to install), and the project's `PackageDocs` looked up by the name its manifests give, else `name` (None when there is none). They need no build. The defaults return nothing and never raise; an override must not raise either.
- `env_manager`: the caller's, or the one the first call that needs it selected; None before that

`LanguageSpec.toolchain` (a `Toolchain`) declares the system tools the plugin needs; `LanguageSpec.analyzers` and `LanguageSpec.project_analyzers` hold its analyzers.

## EnvironmentManager

A plugin must implement `pre_dependencies_installation()`, `_install_dependency()` and `choose_language_version()`; it may override `post_dependencies_installation()`.

- `present_manifests`, `selected_language_version`, `supported_language_versions()` (what every present manifest allows), `select_language_version()` (the plugin's `choose_language_version(versions)` picks one of those)
- `_get_dependencies()`, `_install_dependency(dependency)`: the plugin contract for collecting and installing dependencies; a plugin's own errors for an install go into the returned `AccraResult.errors`
- `subprocess_timeout()`: pass it to every `subprocess.run`, so `Config.build_timeout` caps each call
- `start_build_budget()`, `resume_build_budget(seconds)` (restarts the budget with `seconds` left, what `build()` did not use, for `install_project()`), `end_build_budget()`, `remaining_build_time()`, `build_budget_exhausted()`

Optional, so a caller can use the built environment without knowing the language (the defaults describe a manager that offers none of it):

- `environment_path()`: where the built environment is; None by default
- `run_environment(base_env)`: the process environment for running commands in it; a copy of `base_env` by default
- `parse_dependency(spec)`: a `Dependency` from a native spec; by default the whole spec is the name
- `installed_dependencies()`, `uninstall_dependencies(dependencies)`, `is_usable(dependency)`: listing, removing and checking what is installed; an error by default
- `install_project()`: installs the project itself into the built environment, with the Dockerfile lines that do the same; nothing by default. The caller decides when
- `project_names()`: the names under which the project refers to itself (modules, packages, crates, artifacts); empty by default
- `has_project_install()`: whether the project has an install step of its own (e.g. a build manifest); without one it runs from its sources. False by default
- `entry_point_for(path)`: how to run the source file `path`; an error by default
- `entry_points()`: what the built project can run and how; empty by default

## Analyzer

`Analyzer(spec)` with `AnalyzerSpec`: `name`, `config`, `metrics` (`Metrics`: load, memory, network, parallelism). A plugin implements:

- `install(env_manager)`: makes the analyzer usable in the built environment (e.g. installs a profiler); `AccraInstallError | None`
- `analyze(entry_point, args, env_manager, *, env=None, timeout=None, output_dir=None)`: runs the entry point with `args` in that environment and measures it; `set[Profile] | AccraAnalysisError`, never raises. `env` is the run's whole process environment (default: `env_manager.run_environment()` of the configured one), `timeout` its time limit (default: the plugin's subprocess timeout), `output_dir` where the reports go (default: a new temporary directory). A run that ended before it could be measured gives an `AccraNoSamplesError`

`ProjectAnalyzer(spec)` (same `AnalyzerSpec`) measures the project from its sources without running it, for static measures such as networking or parallelism use. A plugin implements `analyze_project(env_manager)`: it reads the project of `env_manager` (`Config.cwd`, the manifests' dependencies) and returns `set[Profile] | AccraAnalysisError`, never raising.

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
