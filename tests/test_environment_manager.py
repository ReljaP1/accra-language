from typing import override

from accra_language import (
    AccraError,
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
