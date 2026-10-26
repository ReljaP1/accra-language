import re
import time
from typing import override

import pytest

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


def newest_first(versions: set[str]) -> list[str]:
    return sorted(
        versions,
        key=lambda v: tuple(int(p) for p in re.findall(r"\d+", v)),
        reverse=True,
    )


class FakeEnvironmentManager(EnvironmentManager):
    def __init__(self, config: Config):
        manifest = FakeManifest(config)
        spec = EnvironmentManagerSpec(
            name="fake-env-manager",
            version="1.0.0",
            supported_manifests={manifest},
            config=config,
            version_order=newest_first,
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
            version_order=newest_first,
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


def test_the_caller_can_order_the_versions():
    config = Config()
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    assert env_mgr.ranked_language_versions() == ["3.10", "3.9", "3.5"]
    assert env_mgr.ranked_language_versions(order=sorted) == ["3.10", "3.5", "3.9"]
    assert env_mgr.select_language_version(order=sorted) == "3.10"  # alphabetical


def test_versions_are_alphabetical_without_an_order():
    config = Config()
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    env_mgr.spec = env_mgr.spec.model_copy(update={"version_order": None})
    assert env_mgr.ranked_language_versions() == ["3.10", "3.5", "3.9"]


def test_an_order_that_misses_or_invents_versions_still_ranks_every_one_once():
    config = Config()
    env_mgr = ConfigurableEnvironmentManager(config, [VersionsManifest(config)])
    ranked = env_mgr.ranked_language_versions(order=lambda _: ["3.9", "9.9", "3.9"])
    assert ranked == ["3.9", "3.10", "3.5"]
    assert env_mgr.select_language_version(order=lambda _: []) == "3.10"


def test_ranking_without_a_manifest_is_an_error():
    env_mgr = ConfigurableEnvironmentManager(Config())
    assert isinstance(env_mgr.ranked_language_versions(), AccraError)


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


def test_a_plugin_exception_is_not_swallowed():
    config = Config()
    dep = Dependency(name="slow", version="")
    env_mgr = ConfigurableEnvironmentManager(
        config, [VersionsManifest(config, deps={dep})]
    )

    def raising(_):
        raise ValueError("bug in the plugin")

    env_mgr.behaviour = raising
    language = Language(
        LanguageSpec(
            name="", version="", config=config, supported_env_managers={env_mgr}
        )
    )
    with pytest.raises(ValueError, match="bug in the plugin"):
        language.build()

    env_mgr.behaviour = lambda _: None
    env_mgr.pre_dependencies_installation = lambda: (_ for _ in ()).throw(
        FileNotFoundError(2, "No such file", "pyenv")
    )
    with pytest.raises(FileNotFoundError):
        language.build()


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


def test_failed_dependency_error_names_its_environment_manager(tmp_path):
    manager = FakeEnvironmentManager(_config(tmp_path))
    error = AccraFailedDependencyInstallError(
        message="x",
        dependency=Dependency(name="a", version="1"),
        environment_manager=manager,
    )
    assert error.environment_manager is manager
    with pytest.raises(ValueError):
        AccraFailedDependencyInstallError(
            message="x", dependency=Dependency(name="a", version="1")
        )


def test_a_failed_dependency_does_not_stop_the_others(tmp_path):
    class Failing(FakeEnvironmentManager):
        def _get_dependencies(self):
            return {Dependency(name=n, version="1") for n in ("bar", "baz", "foo")}

        def _install_dependency(self, dependency):
            if dependency.name == "baz":
                return AccraResult(
                    errors=[
                        AccraFailedDependencyInstallError(
                            message="no",
                            dependency=dependency,
                            environment_manager=self,
                        )
                    ]
                )
            return AccraResult(
                dockerfile=Dockerfile(
                    dependencies_installation=[
                        DockerfileInstruction(f"RUN install {dependency.name}")
                    ]
                )
            )

    manager = Failing(_config(tmp_path))
    result = manager.dependencies_installation()
    assert result.success
    [error] = result.errors
    assert error.dependency.name == "baz"
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
        self.spec = self.spec.model_copy(update={"version_order": min_first})


def min_first(versions):
    return sorted(versions)


def test_manifest_raising_value_error_is_not_swallowed(tmp_path):
    manager = _VersionManager(_config(tmp_path), ValueError("bad specifier"))
    with pytest.raises(ValueError, match="bad specifier"):
        manager.supported_language_versions()


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


def test_detect_raises_when_a_manifest_raises(tmp_path):
    manager = _VersionManager(_config(tmp_path), ValueError("bad"))
    language = _language(manager, tmp_path)
    with pytest.raises(ValueError):
        language.detect()


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


def test_read_only_hooks_do_not_swallow_a_raise(tmp_path):
    class Bad(_VersionManager):
        def project_names(self):
            raise ValueError("x")

    language = _language(Bad(_config(tmp_path), {"1.0"}), tmp_path)
    with pytest.raises(ValueError):
        language.project_names()

    class BadLanguage(Language):
        def is_present(self):
            raise ValueError("x")

    language = _language(
        _VersionManager(_config(tmp_path), {"1.0"}), tmp_path, BadLanguage
    )
    with pytest.raises(ValueError):
        language.is_present()
