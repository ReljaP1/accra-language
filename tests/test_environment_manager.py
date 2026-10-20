import re
import subprocess
import time
from typing import override

from accra_language import (
    AccraError,
    AccraFailedDependencyInstallError,
    AccraInstallError,
    AccraResult,
    Config,
    Dependency,
    Dockerfile,
    DockerfileInstruction,
    EnvironmentManager,
    EnvironmentManagerSpec,
    Language,
    LanguageSpec,
    Manifest,
    ManifestSpec,
)
from accra_language.analyzer import (
    LibraryUsage,
    MemoryProfile,
    NetworkProfile,
    ParallelismProfile,
    ParallelismUsage,
    Profile,
)


class FakeManifest(Manifest):
    def __init__(self, config: Config):
        spec = ManifestSpec(name="fake-manifest", version="1.0.0", config=config)
        super().__init__(spec)

    @override
    def detect(self) -> bool:
        return True

    @override
    def extract_dependencies(self) -> set[Dependency] | None:
        return {
            Dependency(name="foo", version="4.5"),
            Dependency(name="bar", version="9.4"),
        }

    @override
    def get_supported_language_versions(self) -> set[str] | AccraError:
        return {"9.0"}


class FakeEnvironmentManager(EnvironmentManager):
    def __init__(self, config: Config):
        manifest = FakeManifest(config)
        spec = EnvironmentManagerSpec(
            name="fake-env-manager",
            version="1.0.0",
            supported_manifests={manifest},
            config=config,
        )

        super().__init__(spec)

    @override
    def choose_language_version(self, versions: set[str]) -> str:
        return max(versions, key=lambda v: tuple(int(p) for p in re.findall(r"\d+", v)))

    @override
    def pre_dependencies_installation(self) -> AccraResult:
        language_ver_result: str | AccraError = self.select_language_version()
        if isinstance(language_ver_result, AccraError):
            return AccraResult(errors=[language_ver_result], success=False)

        return AccraResult(
            dockerfile=Dockerfile(
                pre_dependencies_installation=[
                    DockerfileInstruction(
                        f'RUN echo "Fake language, version {language_ver_result}, installed!"'
                    ),
                    DockerfileInstruction(
                        'RUN echo "Fake environment manager setup complete!"'
                    ),
                ]
            )
        )

    @override
    def _install_dependency(self, dependency: Dependency) -> AccraResult:
        return AccraResult(
            dockerfile=Dockerfile(
                dependencies_installation=[
                    DockerfileInstruction(
                        f'RUN echo "Dependency {dependency.name}-{dependency.version} installed!"'
                    )
                ]
            )
        )

    @override
    def post_dependencies_installation(self) -> AccraResult:
        return AccraResult(
            dockerfile=Dockerfile(
                post_dependencies_installation=[
                    DockerfileInstruction('RUN echo "Build complete!"')
                ]
            )
        )


def test_environment_manager():
    correct = AccraResult(
        dockerfile=Dockerfile(
            pre_dependencies_installation=[
                'RUN echo "Fake language, version 9.0, installed!"',
                'RUN echo "Fake environment manager setup complete!"',
            ],
            dependencies_installation=[
                'RUN echo "Dependency bar-9.4 installed!"',
                'RUN echo "Dependency foo-4.5 installed!"',
            ],
            post_dependencies_installation=['RUN echo "Build complete!"'],
        ),
        errors=[],
        success=True,
    )

    config = Config()

    env_mgr = FakeEnvironmentManager(config)
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )

    assert language.detect()
    assert language.build() == correct


class VersionsManifest(FakeManifest):
    def __init__(self, config: Config, versions: set[str] | None = None, deps=None):
        super().__init__(config)
        self.versions = versions if versions is not None else {"3.5", "3.9", "3.10"}
        self.deps = deps

    @override
    def extract_dependencies(self) -> set[Dependency] | None:
        return self.deps

    @override
    def get_supported_language_versions(self) -> set[str] | AccraError:
        return self.versions


class ConfigurableEnvironmentManager(FakeEnvironmentManager):
    def __init__(self, config: Config, manifests=None, name="fake-env-manager"):
        super().__init__(config)
        self.spec = EnvironmentManagerSpec(
            name=name,
            version="1.0.0",
            supported_manifests=set(),
            config=config,
        )
        self.present_manifests = set(manifests or [])
        self.behaviour = lambda dependency: None

    @override
    def _install_dependency(self, dependency: Dependency) -> AccraResult:
        outcome = self.behaviour(dependency)
        if outcome is not None:
            return outcome
        return super()._install_dependency(dependency)


def test_highest_version_is_selected_numerically():
    config = Config()
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    assert env_mgr.select_language_version() == "3.10"


def test_no_manifests_returns_error_instead_of_raising():
    env_mgr = ConfigurableEnvironmentManager(Config())
    assert isinstance(env_mgr.select_language_version(), AccraError)

    language = Language(
        LanguageSpec(
            name="", version="", config=Config(), supported_env_managers={env_mgr}
        )
    )
    assert isinstance(language.detect(), AccraError)


def test_caller_chosen_language_version():
    config = Config(language_version="3.7")
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    assert env_mgr.select_language_version() == "3.7"


def test_language_without_env_managers_returns_errors():
    # constructing never raises; detect() and build() return the error
    language = Language(LanguageSpec(name="py", version="", config=Config()))
    assert language.env_manager is None

    error = language.detect()
    assert isinstance(error, AccraError)
    assert "has no environment manager" in error.message

    build = language.build()
    assert not build.success
    [build_error] = build.errors
    assert "has no environment manager" in build_error.message


def test_plugin_decides_which_version_is_chosen():
    config = Config()
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    env_mgr.choose_language_version = lambda versions: min(versions)
    assert env_mgr.select_language_version() == "3.10"  # lexicographic minimum


def test_env_manager_choice_is_deterministic():
    config = Config()
    managers = {
        ConfigurableEnvironmentManager(config, name=name)
        for name in ("zeta", "alpha", "mid")
    }
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers=managers
        )
    )
    assert language.env_manager is None  # picked by detect(), not at construction
    language.detect()
    assert language.env_manager.spec.name == "alpha"

    # build() picks the same one when detect() was not called
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers=managers
        )
    )
    language.build()
    assert language.env_manager.spec.name == "alpha"


def test_caller_chosen_env_manager_is_kept():
    config = Config()
    alpha, zeta = (
        ConfigurableEnvironmentManager(config, name=name) for name in ("alpha", "zeta")
    )
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={alpha, zeta}
        ),
        env_manager=zeta,
    )
    language.detect()
    assert language.env_manager is zeta


def test_subprocess_exceptions_become_errors():
    config = Config()
    dep = Dependency(name="slow", version="")
    env_mgr = ConfigurableEnvironmentManager(
        config, [VersionsManifest(config, deps={dep})]
    )

    def raising(_):
        raise subprocess.TimeoutExpired(["uv", "pip"], 5, stderr=b"x" * 5000)

    env_mgr.behaviour = raising
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )
    build = language.build()
    # a dependency that times out is recorded as failed; the build goes on
    assert build.success
    [error] = build.errors
    assert isinstance(error, AccraFailedDependencyInstallError)
    assert error.dependency == dep
    assert error.command == ["uv", "pip"]
    assert len(error.stderr) == 2000

    env_mgr.behaviour = lambda _: None
    env_mgr.pre_dependencies_installation = lambda: (_ for _ in ()).throw(
        FileNotFoundError(2, "No such file", "pyenv")
    )
    build = language.build()
    assert not build.success
    assert "pyenv" in build.errors[0].command


def test_profiles_are_hashable():
    profiles = {
        MemoryProfile(duration_ms=1, peak_memory_mb=1.0, total_memory_mb=2.0),
        NetworkProfile(duration_ms=1, imports=[LibraryUsage(name="requests")]),
        ParallelismProfile(
            duration_ms=1,
            multithreading=ParallelismUsage(used=True, modules=["threading"]),
        ),
    }
    assert len(profiles) == 3
    assert all(isinstance(p, Profile) for p in profiles)


def test_all_names_exist():
    import accra_language

    assert all(hasattr(accra_language, name) for name in accra_language.__all__)


def test_build_budget_stops_the_build():
    config = Config(build_timeout=0.01)
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    env_mgr.pre_dependencies_installation = lambda: time.sleep(0.05) or AccraResult()
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )
    result = language.build()
    assert not result.success
    assert "budget" in result.errors[0].message
    assert env_mgr.remaining_build_time() is None  # budget is cleared after build


def test_zero_build_timeout_means_no_budget():
    config = Config(build_timeout=0)
    env_mgr = ConfigurableEnvironmentManager(config)
    env_mgr.start_build_budget()
    assert env_mgr.remaining_build_time() is None


def test_subprocess_timeout_is_capped_by_budget():
    config = Config(timeout=100, build_timeout=5)
    env_mgr = ConfigurableEnvironmentManager(config)
    assert env_mgr.subprocess_timeout() == 100
    env_mgr.start_build_budget()
    assert env_mgr.subprocess_timeout() <= 5


# -- failures that are not subprocess/OS errors --------


def _config(tmp_path):
    return Config(cwd=tmp_path)


class RaisingInstallManager(FakeEnvironmentManager):
    """foo, bar, baz sorted: bar installs, baz raises, foo installs."""

    def __init__(self, config, raising):
        super().__init__(config)
        self.raising = raising
        self.attempted: list[str] = []

    def _get_dependencies(self):
        return {Dependency(name=n, version="1") for n in ("bar", "baz", "foo")}

    def _install_dependency(self, dependency):
        self.attempted.append(dependency.name)
        if dependency.name == "baz":
            raise self.raising
        return AccraResult()


def _install_with(tmp_path, exc):
    manager = RaisingInstallManager(_config(tmp_path), exc)
    return manager, manager.dependencies_installation()


def test_install_timeout_mid_list_is_recorded_and_rest_installed(tmp_path):
    exc = subprocess.TimeoutExpired(cmd=["uv", "pip", "install", "baz"], timeout=5)
    manager, result = _install_with(tmp_path, exc)
    assert result.success is True
    assert manager.attempted == ["bar", "baz", "foo"]
    [error] = result.errors
    assert isinstance(error, AccraFailedDependencyInstallError)
    assert error.dependency.name == "baz"
    assert error.environment_manager is manager
    assert error.command == ["uv", "pip", "install", "baz"]
    assert "timed out" in error.message


def test_install_value_error_mid_list_is_recorded(tmp_path):
    manager, result = _install_with(tmp_path, ValueError("bad spec"))
    assert result.success is True
    assert manager.attempted == ["bar", "baz", "foo"]
    [error] = result.errors
    assert isinstance(error, AccraFailedDependencyInstallError)
    assert error.dependency.name == "baz"
    assert "bad spec" in error.message


def test_failed_dependency_error_needs_no_environment_manager():
    error = AccraFailedDependencyInstallError(
        message="x", dependency=Dependency(name="a", version="1")
    )
    assert error.environment_manager is None


def test_earlier_dockerfile_lines_are_kept(tmp_path):
    class Keeping(RaisingInstallManager):
        def _install_dependency(self, dependency):
            result = super()._install_dependency(dependency)
            result.dockerfile = Dockerfile(
                dependencies_installation=[
                    DockerfileInstruction(f"RUN install {dependency.name}")
                ]
            )
            return result

    manager = Keeping(_config(tmp_path), ValueError("boom"))
    result = manager.dependencies_installation()
    lines = [str(i) for i in result.dockerfile.dependencies_installation]
    assert any("bar" in line for line in lines)
    assert any("foo" in line for line in lines)
    assert not any("baz" in line for line in lines)


class _VersionManifest(FakeManifest):
    def __init__(self, config, outcome):
        super().__init__(config)
        self.outcome = outcome

    def get_supported_language_versions(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _VersionManager(FakeEnvironmentManager):
    def __init__(self, config, outcome):
        super().__init__(config)
        self.present_manifests = {_VersionManifest(config, outcome)}

    def choose_language_version(self, versions):
        return min(versions)


def test_manifest_raising_value_error_gives_error(tmp_path):
    manager = _VersionManager(_config(tmp_path), ValueError("bad specifier"))
    result = manager.supported_language_versions()
    assert isinstance(result, AccraInstallError)
    assert "fake-manifest" in result.message and "bad specifier" in result.message


def test_manifest_frozenset_is_accepted(tmp_path):
    manager = _VersionManager(_config(tmp_path), frozenset({"1.0", "2.0"}))
    assert manager.supported_language_versions() == {"1.0", "2.0"}
    assert manager.select_language_version() == "1.0"


def test_manifest_none_or_list_is_an_error_naming_the_manifest(tmp_path):
    for outcome in (None, ["1.0"]):
        manager = _VersionManager(_config(tmp_path), outcome)
        result = manager.supported_language_versions()
        assert isinstance(result, AccraInstallError)
        assert "fake-manifest" in result.message


def test_chooser_raising_or_returning_unknown_is_an_error(tmp_path):
    class Raising(_VersionManager):
        def choose_language_version(self, versions):
            raise RuntimeError("no idea")

    class Unknown(_VersionManager):
        def choose_language_version(self, versions):
            return "99"

    for cls in (Raising, Unknown):
        manager = cls(_config(tmp_path), {"1.0"})
        assert isinstance(manager.select_language_version(), AccraInstallError)
        assert manager.selected_language_version is None


def test_detect_returns_error_when_manifest_raises(tmp_path):
    manager = _VersionManager(_config(tmp_path), ValueError("bad"))
    language = _language(manager, tmp_path)
    assert isinstance(language.detect(), AccraError)


def _language(manager, tmp_path, cls=None):
    class Lang(cls or Language):
        pass

    spec = LanguageSpec(
        name="fake",
        version="1",
        config=_config(tmp_path),
        supported_env_managers={manager},
    )
    return Lang(spec, manager)


def test_read_only_hooks_of_manager_that_raise(tmp_path):
    class Bad(_VersionManager):
        def project_names(self):
            raise ValueError("x")

        def has_project_install(self):
            raise ValueError("x")

    language = _language(Bad(_config(tmp_path), {"1.0"}), tmp_path)
    assert language.project_names() == set()
    assert language.has_project_install() is False


def test_overridden_language_hooks_that_raise_are_guarded(tmp_path):
    class Bad(Language):
        def is_present(self):
            raise ValueError("x")

        def manifest_paths(self):
            raise ValueError("x")

        def covered_manifests(self):
            raise ValueError("x")

        def missing_tools(self):
            raise ValueError("x")

    language = _language(_VersionManager(_config(tmp_path), {"1.0"}), tmp_path, Bad)
    assert language.is_present() is False
    assert language.manifest_paths() == set()
    assert language.covered_manifests() == set()
    assert language.missing_tools() == ()


def test_base_hooks_guard_a_raising_manifest(tmp_path):
    manager = _VersionManager(_config(tmp_path), {"1.0"})

    class Boom(Manifest):
        def detect(self):
            return True

        def path(self):
            raise ValueError("x")

        def extract_dependencies(self):
            return None

        def get_supported_language_versions(self):
            return {"1"}

    manager.present_manifests = {
        Boom(ManifestSpec(name="b", version="1", config=_config(tmp_path)))
    }
    language = _language(manager, tmp_path)
    assert language.manifest_paths() == set()


def test_build_converts_any_exception(tmp_path):
    class Bad(_VersionManager):
        def pre_dependencies_installation(self):
            raise ValueError("x")

    result = _language(Bad(_config(tmp_path), {"1.0"}), tmp_path).build()
    assert result.success is False
    assert "ValueError" in result.errors[0].message


def test_install_project_runs_under_remaining_build_budget():
    config = Config(build_timeout=100, timeout=1000)
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    seen = []
    env_mgr.install_project = lambda: (
        seen.append(env_mgr.subprocess_timeout()) or (AccraResult())
    )
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )
    assert language.build().success
    assert language.install_project().success
    assert seen and seen[0] <= 100
    assert env_mgr.remaining_build_time() is None  # ended again

    language._build_time_left = 0.0
    result = language.install_project()
    assert not result.success and "budget" in result.errors[0].message


def test_analyzer_install_cache_resets_on_build_and_caches_failures():
    from test_multi_language_contract import FakeAnalyzer

    from accra_language import AccraInstallError
    from accra_language.analyzer import Metrics

    config = Config()
    bad = FakeAnalyzer(
        config, "bad", Metrics.LOAD, install_error=AccraInstallError(message="no")
    )
    good = FakeAnalyzer(config, "good", Metrics.MEMORY)
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    language = Language(
        LanguageSpec(
            name="",
            version="",
            config=config,
            supported_env_managers={env_mgr},
            analyzers={bad, good},
        )
    )
    language.install_analyzers()
    language.install_analyzers()
    assert bad.installs == 1 and good.installs == 1
    language.build()
    language.install_analyzers()
    assert bad.installs == 2 and good.installs == 2
