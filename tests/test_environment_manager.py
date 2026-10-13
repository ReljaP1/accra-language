import subprocess
from typing import override

from accra_language import (
    AccraError,
    AccraFailedDependencyInstallError,
    AccraInstallError,
    AccraResult,
    Config,
    Dependency,
    DependencyStatus,
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


def test_language_without_env_managers_reports_errors():
    language = Language(LanguageSpec(name="", version="", config=Config()))
    assert language.env_manager is None
    assert isinstance(language.detect(), AccraError)
    result = language.build()
    assert not result.success and result.errors


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
    assert language.env_manager.spec.name == "alpha"


def test_failed_install_is_attributed_and_does_not_stop_the_build():
    config = Config()
    deps = {Dependency(name="bad", version=""), Dependency(name="good", version="")}
    env_mgr = ConfigurableEnvironmentManager(
        config, [VersionsManifest(config, deps=deps)]
    )

    def behaviour(dependency):
        if dependency.name == "bad":
            return AccraResult(errors=[AccraInstallError(message="boom")])

    env_mgr.behaviour = behaviour
    result = env_mgr.dependencies_installation()

    assert result.success
    [error] = result.errors
    assert isinstance(error, AccraFailedDependencyInstallError)
    assert error.dependency.name == "bad"
    assert len(result.dockerfile.dependencies_installation) == 1
    assert set(env_mgr.install_results) == deps


def test_subprocess_exceptions_become_errors():
    config = Config()
    dep = Dependency(name="slow", version="")
    env_mgr = ConfigurableEnvironmentManager(
        config, [VersionsManifest(config, deps={dep})]
    )

    def raising(_):
        raise subprocess.TimeoutExpired(["uv", "pip"], 5, stderr=b"x" * 5000)

    env_mgr.behaviour = raising
    result = env_mgr.install_dependency(dep)
    [error] = result.errors
    assert isinstance(error, AccraFailedDependencyInstallError)
    assert error.command == ["uv", "pip"]
    assert len(error.stderr) == 2000

    env_mgr.pre_dependencies_installation = lambda: (_ for _ in ()).throw(
        FileNotFoundError(2, "No such file", "pyenv")
    )
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
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


def test_dependency_specifier():
    assert Dependency(name="numpy", version="").specifier == "numpy"
    assert Dependency(name="numpy", version=">=1.2").specifier == "numpy>=1.2"
    assert Dependency(name="numpy", version="1.2").specifier == "numpy==1.2"
    assert (
        Dependency(name="pkg", version="@ https://x/y.whl").specifier
        == "pkg @ https://x/y.whl"
    )
    assert Dependency(name="git+https://x/y.git", version="").is_direct
    assert not Dependency(name="numpy", version="").is_direct
    extras = Dependency(name="uvicorn[standard, h11]", version="==1")
    assert extras.base_name == "uvicorn"
    assert extras.extras == ("standard", "h11")


def test_dependency_statuses():
    config = Config()
    deps = {Dependency(name=n, version="") for n in ("bad", "have", "ok", "todo")}
    env_mgr = ConfigurableEnvironmentManager(
        config, [VersionsManifest(config, deps=deps)]
    )

    def behaviour(dependency):
        if dependency.name == "bad":
            return AccraResult(errors=[AccraInstallError(message="boom")])
        if dependency.name == "have":
            return AccraResult(dependency_status=DependencyStatus.SATISFIED)

    env_mgr.behaviour = behaviour
    for dep in deps - {Dependency(name="todo", version="")}:
        env_mgr.install_dependency(dep)

    by_name = {d.name: s for d, s in env_mgr.dependency_statuses().items()}
    assert by_name == {
        "bad": DependencyStatus.FAILED,
        "have": DependencyStatus.SATISFIED,
        "ok": DependencyStatus.INSTALLED,
        "todo": DependencyStatus.PENDING,
    }


def test_build_budget_stops_the_build():
    config = Config(build_timeout=0.0)
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )
    result = language.build()
    assert not result.success
    assert "budget" in result.errors[0].message
    assert env_mgr.remaining_build_time() is None  # budget is cleared after build


def test_subprocess_timeout_is_capped_by_budget():
    config = Config(timeout=100, build_timeout=5)
    env_mgr = ConfigurableEnvironmentManager(config)
    assert env_mgr.subprocess_timeout() == 100
    env_mgr.start_build_budget()
    assert env_mgr.subprocess_timeout() <= 5


def test_optional_capabilities_have_safe_defaults():
    config = Config(env={"A": "1"})
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )
    assert language.interpreter_path() is None
    assert language.environment_path() is None
    assert language.run_environment() == {"A": "1"}
    assert language.observed_imports() == set()
    assert language.teardown().success
    assert language.install_project().success
    assert isinstance(language.check_import("os"), AccraError)
