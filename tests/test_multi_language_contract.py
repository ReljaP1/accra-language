"""The contract parts a caller needs for any language: environment facts,
entry points, read-only presence and the
toolchain. All are optional for a plugin and have defaults."""

from pathlib import Path
from typing import override

from accra_language import (
    AccraAnalysisError,
    AccraError,
    AccraInstallError,
    AccraResult,
    Analyzer,
    AnalyzerSpec,
    Config,
    Dependency,
    Dockerfile,
    EntryPoint,
    EnvironmentManager,
    EnvironmentManagerSpec,
    Language,
    LanguageSpec,
    Manifest,
    ManifestSpec,
    MemoryProfile,
    Metrics,
    Toolchain,
)


class FileManifest(Manifest):
    """Present when its file exists in `cwd`."""

    def __init__(self, config: Config, filename: str):
        super().__init__(ManifestSpec(name=filename, version="", config=config))

    @override
    def detect(self) -> bool:
        return (Path(self.spec.config.cwd) / self.spec.name).is_file()

    @override
    def extract_dependencies(self) -> set[Dependency] | None:
        return None

    @override
    def get_supported_language_versions(self) -> set[str] | AccraError:
        return {"1"}


class PlainEnvironmentManager(EnvironmentManager):
    """Implements only what the contract requires."""

    def __init__(self, config: Config, manifests=()):
        super().__init__(
            EnvironmentManagerSpec(
                name="plain",
                version="",
                config=config,
                supported_manifests=set(manifests),
            )
        )

    @override
    def pre_dependencies_installation(self) -> AccraResult:
        return AccraResult()

    @override
    def _install_dependency(self, dependency: Dependency) -> AccraResult:
        return AccraResult()


def _language(config, env_managers=(), toolchain=None):
    spec = LanguageSpec(
        name="toy",
        version="1",
        config=config,
        supported_env_managers=set(env_managers),
        toolchain=toolchain or Toolchain(),
    )
    return Language(spec)


def test_environment_fact_defaults(tmp_path):
    manager = PlainEnvironmentManager(Config(cwd=tmp_path))

    assert manager.environment_path() is None
    base = {"PATH": "/bin"}
    run_env = manager.run_environment(base)
    assert run_env == base and run_env is not base
    assert manager.parse_dependency(" foo>=1 ") == Dependency(name="foo>=1", version="")
    assert isinstance(manager.installed_dependencies(), AccraError)
    uninstall = manager.uninstall_dependencies([Dependency(name="foo", version="")])
    assert not uninstall.success and uninstall.errors
    assert isinstance(manager.is_usable(Dependency(name="foo", version="")), AccraError)
    assert manager.entry_points() == set()


def test_entry_point_and_toolchain_are_frozen_models():
    entry = EntryPoint(name="tool", kind="binary", command=("./tool",))
    assert {entry, EntryPoint(name="tool", kind="binary", command=("./tool",))} == {
        entry
    }
    toolchain = Toolchain(executables=("cargo",), packages=("build-essential",))
    assert hash(toolchain) == hash(
        Toolchain(executables=("cargo",), packages=("build-essential",))
    )


def test_toolchain_installs_for_a_home(tmp_path):
    toolchain = Toolchain(
        executables=("toy",),
        install=("git clone https://example.org/toy {home}/.toy",),
        environment=(("TOY_ROOT", "{home}/.toy"),),
        path=("{home}/.toy/bin", "/usr/bin"),
    )
    assert hash(toolchain) == hash(toolchain.model_copy())
    rendered = toolchain.for_home(tmp_path)
    assert rendered.install == (f"git clone https://example.org/toy {tmp_path}/.toy",)
    assert rendered.environment == (("TOY_ROOT", f"{tmp_path}/.toy"),)
    env = rendered.applied_to({"PATH": "/usr/bin:/bin", "KEEP": "1"})
    assert env == {
        "PATH": f"{tmp_path}/.toy/bin:/usr/bin:/bin",
        "KEEP": "1",
        "TOY_ROOT": f"{tmp_path}/.toy",
    }
    assert rendered.applied_to(env) == env
    assert Toolchain().applied_to({"PATH": "/bin"}) == {"PATH": "/bin"}
    assert Toolchain().install == () and Toolchain().environment == ()


def test_presence_is_read_only_and_manifest_based(tmp_path):
    config = Config(cwd=tmp_path)
    language = _language(
        config, [PlainEnvironmentManager(config, [FileManifest(config, "toy.toml")])]
    )
    assert language.is_present() is False

    (tmp_path / "toy.toml").write_text("")
    config = Config(cwd=tmp_path)
    language = _language(
        config, [PlainEnvironmentManager(config, [FileManifest(config, "toy.toml")])]
    )
    assert language.is_present() is True
    assert language.env_manager is None  # nothing was selected or built
    assert sorted(p.name for p in tmp_path.iterdir()) == ["toy.toml"]


def test_missing_tools_uses_the_config_path(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "toytool"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    config = Config(cwd=tmp_path, env={"PATH": str(bin_dir)})
    language = _language(
        config, toolchain=Toolchain(executables=("toytool", "no-such-tool-accra"))
    )

    assert language.missing_tools() == ("no-such-tool-accra",)


def test_entry_points_come_from_the_env_manager(tmp_path):
    config = Config(cwd=tmp_path)
    manager = PlainEnvironmentManager(config)
    entry = EntryPoint(name="main", kind="script", command=("toy", "main"))
    manager.entry_points = lambda: {entry}
    language = _language(config, [manager])

    assert language.entry_points() == {entry}

    assert isinstance(_language(Config(cwd=tmp_path)).entry_points(), AccraError)


class FakeAnalyzer(Analyzer):
    def __init__(self, config, name, metric, install_error=None, profiles=None):
        super().__init__(AnalyzerSpec(name=name, config=config, metrics={metric}))
        self.install_error = install_error
        self.profiles = profiles if profiles is not None else set()
        self.runs = []

    @override
    def install(self, env_manager):
        self.installs = getattr(self, "installs", 0) + 1
        return self.install_error

    @override
    def analyze(self, entry_point, args, env_manager, **options):
        self.options = options
        self.runs.append((entry_point.name, list(args), env_manager.spec.name))
        return self.profiles


def test_analyze_runs_the_matching_analyzers(tmp_path):
    config = Config(cwd=tmp_path)
    memory = FakeAnalyzer(
        config,
        "mem",
        Metrics.MEMORY,
        profiles={
            MemoryProfile(duration_ms=5, peak_memory_mb=1.0, total_memory_mb=2.0)
        },
    )
    load = FakeAnalyzer(
        config,
        "cpu",
        Metrics.LOAD,
        install_error=AccraInstallError(message="profiler unavailable"),
    )
    broken = FakeAnalyzer(
        config,
        "net",
        Metrics.NETWORK,
        profiles=AccraAnalysisError(message="net failed"),
    )
    spec = LanguageSpec(
        name="toy",
        version="1",
        config=config,
        supported_env_managers={PlainEnvironmentManager(config)},
        analyzers={memory, load, broken},
    )
    language = Language(spec)
    entry = EntryPoint(name="main", kind="script", command=("toy", "main"))

    results = language.analyze(entry, ["--n", "3"])

    assert list(results) == ["cpu", "mem", "net"]  # name order
    assert isinstance(results["cpu"], AccraInstallError) and load.runs == []
    assert results["mem"] == memory.profiles
    assert memory.runs == [("main", ["--n", "3"], "plain")]
    assert isinstance(results["net"], AccraError)

    only_memory = language.analyze(entry, [], metrics={Metrics.MEMORY})
    assert list(only_memory) == ["mem"]

    no_manager = Language(
        LanguageSpec(name="toy", version="1", config=config, analyzers={memory})
    )
    assert isinstance(no_manager.analyze(entry, [])["mem"], AccraError)

    # installed once per language, whatever the number of runs
    assert memory.installs == 1
    assert language.install_analyzers({Metrics.MEMORY}) == {"mem": None}
    assert memory.installs == 1
    assert isinstance(language.install_analyzers()["cpu"], AccraInstallError)

    # the run's environment, timeout and report directory reach the analyzer
    language.analyze(
        entry, [], {Metrics.MEMORY}, env={"A": "1"}, timeout=5, output_dir=tmp_path
    )
    assert memory.options == {"env": {"A": "1"}, "timeout": 5, "output_dir": tmp_path}
    language.analyze(entry, [], {Metrics.MEMORY})
    assert memory.options == {}


def test_entry_point_for_a_source_file(tmp_path):
    # How to run one file
    config = Config(cwd=tmp_path)
    manager = PlainEnvironmentManager(config)
    language = _language(config, [manager])
    assert isinstance(language.entry_point_for(tmp_path / "main.toy"), AccraError)

    entry = EntryPoint(name="main", kind="script", command=("toy", "main.toy"))
    manager.entry_point_for = lambda path: entry
    assert language.entry_point_for(tmp_path / "main.toy") == entry

    assert isinstance(_language(config).entry_point_for(tmp_path / "x"), AccraError)


def test_profiles_carry_exit_code_and_report():
    from accra_language import AccraAnalysisError, AccraNoSamplesError

    profile = MemoryProfile(duration_ms=1, peak_memory_mb=1.0, total_memory_mb=1.0)
    assert profile.exit_code is None and profile.report is None
    failing = MemoryProfile(
        duration_ms=1, peak_memory_mb=1.0, total_memory_mb=1.0, exit_code=2, report="r"
    )
    assert failing.exit_code == 2 and failing.report == "r"
    assert issubclass(AccraNoSamplesError, AccraAnalysisError)


def test_dockerfile_parts_compose(tmp_path):
    first = Dockerfile(
        pre_dependencies_installation=["RUN install-python", "RUN uv venv"],
        dependencies_installation=["RUN uv pip install six"],
    )
    second = Dockerfile(
        pre_dependencies_installation=["RUN install-rust"],
        post_dependencies_installation=["RUN cargo build"],
    )
    first.extend(second)

    assert first.project_steps() == [
        "RUN install-python",
        "RUN uv venv",
        "RUN install-rust",
        "RUN uv pip install six",
        "RUN cargo build",
    ]
    assert str(Dockerfile(pre_dependencies_installation=["RUN x"])).startswith("RUN x")


def test_manifest_paths(tmp_path):
    (tmp_path / "toy.toml").write_text("")
    config = Config(cwd=tmp_path)
    manager = PlainEnvironmentManager(
        config, [FileManifest(config, "toy.toml"), FileManifest(config, "absent.toml")]
    )
    language = _language(config, [manager])

    assert language.manifest_paths() == {tmp_path / "toy.toml"}


def test_project_install_and_names_defaults(tmp_path):
    config = Config(cwd=tmp_path)
    manager = PlainEnvironmentManager(config)
    language = _language(config, [manager])

    installed = language.install_project()
    assert installed.success and not installed.errors
    assert language.project_names() == set()

    assert not _language(config).install_project().success  # no env manager
    assert _language(config).project_names() == set()

    # without an install step of its own, the project runs from its sources
    assert language.has_project_install() is False
    manager.has_project_install = lambda: True
    assert language.has_project_install() is True
    assert _language(config).has_project_install() is False  # no env manager


def test_supported_versions_are_available_without_choosing(tmp_path):
    (tmp_path / "toy.toml").write_text("")
    config = Config(cwd=tmp_path)
    manager = PlainEnvironmentManager(config, [FileManifest(config, "toy.toml")])

    assert manager.supported_language_versions() == {"1"}
    assert manager.selected_language_version is None
    assert manager.select_language_version() == "1"
    assert isinstance(
        PlainEnvironmentManager(config).supported_language_versions(), AccraError
    )


def test_usage_scripts_are_unsupported_by_default(tmp_path):
    from accra_language import ApiSymbol, ScriptProfile, ScriptReading

    config = Config(cwd=tmp_path)
    language = _language(config, [PlainEnvironmentManager(config)])
    assert language.script_profile() is None
    assert isinstance(language.check_script(tmp_path / "s.toy"), AccraError)
    assert isinstance(
        language.read_script("x", [ApiSymbol(module="m.toy", name="f")]), AccraError
    )

    profile = ScriptProfile(language="Toy", file_suffix=".toy", code_fence="toy")
    assert profile.conventions == ()
    # What describes running the language's programs defaults to none
    assert (profile.run_command, profile.cli_frameworks, profile.project_files) == (
        "",
        (),
        (),
    )
    symbol = ApiSymbol(module="m.toy", name="f")
    assert (symbol.kind, symbol.returns_value, symbol.is_driver) == (
        "function",
        True,
        False,
    )
    reading = ScriptReading(calls_project_api=True, demonstrated_symbols=("f",))
    assert reading.distinct_api_symbols == 0 and reading.parse_error is None


def test_runs_and_documentation_are_unread_by_default(tmp_path):
    from accra_language import PackageDocs, RunDiagnosis, ScriptProfile

    config = Config(cwd=tmp_path)
    language = _language(config, [PlainEnvironmentManager(config)])
    assert language.diagnose_run("anything at all") == RunDiagnosis()
    assert language.standard_modules() == frozenset()
    assert language.package_documentation("toy", timeout=1) is None

    profile = ScriptProfile(language="Toy", file_suffix=".toy", code_fence="toy")
    assert (profile.command_pattern, profile.usage_patterns) == ("", ())
    assert profile.package_registry == ""
    docs = PackageDocs(registry="ToyHub", package_name="toy", source_url="u")
    assert (docs.summary, docs.description_excerpt, docs.project_urls) == ("", "", ())


def test_project_analyzers_run_without_an_entry_point(tmp_path):
    # static analyses of the sources (network, parallelism)
    from accra_language import (
        FileModules,
        NetworkProfile,
        ParallelismProfile,
        ParallelismUsage,
        ProjectAnalyzer,
    )

    class Static(ProjectAnalyzer):
        def __init__(self, config, name, metric, result):
            super().__init__(AnalyzerSpec(name=name, config=config, metrics={metric}))
            self.result = result
            self.seen = []

        @override
        def analyze_project(self, env_manager):
            self.seen.append(env_manager.spec.name)
            return self.result

    config = Config(cwd=tmp_path)
    network = Static(
        config,
        "net",
        Metrics.NETWORK,
        {NetworkProfile(duration_ms=1, requires_network=True)},
    )
    threads = Static(
        config,
        "par",
        Metrics.PARALLELISM,
        {
            ParallelismProfile(
                duration_ms=1,
                multithreading=ParallelismUsage(
                    used=True,
                    modules=("threading",),
                    files=(FileModules(path="a.py", modules=("threading",)),),
                ),
            )
        },
    )
    broken = Static(
        config, "zzz", Metrics.NETWORK, AccraAnalysisError(message="unreadable")
    )
    spec = LanguageSpec(
        name="toy",
        version="1",
        config=config,
        supported_env_managers={PlainEnvironmentManager(config)},
        project_analyzers={network, threads, broken},
    )
    language = Language(spec)

    results = language.analyze_project()
    assert list(results) == ["net", "par", "zzz"]
    assert results["net"] == network.result and network.seen == ["plain"]
    [profile] = results["par"]
    assert profile.multithreading.files[0].path == "a.py"
    assert isinstance(results["zzz"], AccraError)
    assert list(language.analyze_project({Metrics.PARALLELISM})) == ["par"]

    no_manager = Language(
        LanguageSpec(
            name="toy", version="1", config=config, project_analyzers={network}
        )
    )
    assert isinstance(no_manager.analyze_project()["net"], AccraError)
    assert (
        Language(LanguageSpec(name="toy", version="1", config=config)).analyze_project()
        == {}
    )


def test_toolchain_path_and_quoting():
    toolchain = Toolchain(path=("/opt/x/bin", "/bin"))
    env = toolchain.applied_to({})
    assert env["PATH"] == "/opt/x/bin:/bin:/usr/bin" or env["PATH"].startswith(
        "/opt/x/bin:/bin"
    )
    assert (
        toolchain.applied_to({"PATH": "/usr/bin:/bin:/opt/x/bin:/bin"})["PATH"]
        == "/opt/x/bin:/bin:/usr/bin"
    )
    odd = Toolchain(
        install=("echo {home}",), environment=(("H", "{home}"),), path=("{home}/b",)
    ).for_home("/tmp/a b; rm")
    assert odd.install == ("echo '/tmp/a b; rm'",)
    assert odd.environment == (("H", "/tmp/a b; rm"),)
    assert odd.path == ("/tmp/a b; rm/b",)
    assert Toolchain(install=("echo {home}",)).for_home("/home/appuser").install == (
        "echo /home/appuser",
    )


def test_package_docs_hashable_and_profile_patterns():
    import pytest

    from accra_language.script import PackageDocs, ScriptProfile

    docs = PackageDocs(
        registry="PyPI", package_name="p", source_url="u", project_urls={"a": "b"}
    )
    assert docs.project_urls == (("a", "b"),)
    assert hash(docs) == hash(docs.model_copy())
    with pytest.raises(ValueError, match="command_pattern"):
        ScriptProfile(
            language="X", file_suffix=".x", code_fence="x", command_pattern="("
        )
    with pytest.raises(ValueError, match="usage_patterns"):
        ScriptProfile(
            language="X", file_suffix=".x", code_fence="x", usage_patterns=("ok", "[")
        )
