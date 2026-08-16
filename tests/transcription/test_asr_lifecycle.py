"""Model lifecycle and the faster-whisper adapter's guards (BE §6.6, §15).

The faster-whisper model itself is not installed here, so its coverage is the import guard, the
failure-message quality, and the conversion from its output shape into word tokens. Real
transcription and real-time-factor measurement are manual steps — see ``docs/checklist.md`` Part 4.
"""

from __future__ import annotations

import types

import numpy as np
import pytest
from app.config.schema import AsrConfig
from app.services.asr import AsrLifecycle, LoadState, MockScript, acceleration
from app.services.asr.contract import AsrLoadError
from app.services.asr.faster_whisper import FasterWhisperBackend, is_available

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def mock_config(**overrides: object) -> AsrConfig:
    settings: dict = {"backend": "mock", "model": "scripted", **overrides}
    return AsrConfig(**settings)


class TestLifecycleLoading:
    async def test_it_starts_unloaded(self) -> None:
        lifecycle = AsrLifecycle(mock_config())
        assert lifecycle.state is LoadState.UNLOADED
        assert not lifecycle.is_ready

    async def test_loading_reaches_ready(self) -> None:
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.load()

        assert lifecycle.state is LoadState.READY
        assert lifecycle.is_ready
        assert lifecycle.model_id == "mock:scripted"

    async def test_progress_is_reported_through_loading_warming_and_ready(self) -> None:
        """A frozen window during load reads as a crash; the UI needs named states."""
        seen: list[LoadState] = []
        lifecycle = AsrLifecycle(mock_config(), on_progress=lambda p: seen.append(p.state))
        await lifecycle.load()

        assert seen == [LoadState.LOADING, LoadState.WARMING, LoadState.READY]

    async def test_progress_carries_a_human_message(self) -> None:
        messages: list[str] = []
        lifecycle = AsrLifecycle(mock_config(), on_progress=lambda p: messages.append(p.message))
        await lifecycle.load()
        assert all(message.strip() for message in messages)

    async def test_an_async_progress_callback_is_awaited(self) -> None:
        seen: list[LoadState] = []

        async def listener(progress) -> None:  # noqa: ANN001
            seen.append(progress.state)

        await AsrLifecycle(mock_config(), on_progress=listener).load()
        assert LoadState.READY in seen

    async def test_a_raising_progress_listener_does_not_fail_the_load(self) -> None:
        def explode(progress) -> None:  # noqa: ANN001
            raise RuntimeError("listener failure")

        lifecycle = AsrLifecycle(mock_config(), on_progress=explode)
        await lifecycle.load()
        assert lifecycle.is_ready

    async def test_warm_up_runs_an_inference_before_reporting_ready(self) -> None:
        """The first real inference is otherwise several times slower than steady state."""
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.load()
        assert lifecycle.backend is not None
        assert lifecycle.backend.pass_count >= 1  # type: ignore[attr-defined]

    async def test_a_load_failure_records_the_state_and_the_reason(self) -> None:
        lifecycle = AsrLifecycle(AsrConfig(backend="faster-whisper", model="small"))
        if is_available():
            pytest.skip("faster-whisper is installed; the failure path cannot be exercised")

        with pytest.raises(AsrLoadError):
            await lifecycle.load()

        assert lifecycle.state is LoadState.FAILED
        assert "asr-whisper" in lifecycle.error


class TestLifecycleTranscription:
    async def test_transcribing_before_load_says_so(self) -> None:
        with pytest.raises(AsrLoadError, match="No model is loaded"):
            AsrLifecycle(mock_config()).transcribe(np.zeros(1000, dtype=np.float32))

    async def test_a_prompt_is_passed_through_when_supported(self) -> None:
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.load()
        lifecycle.transcribe(np.zeros(16_000, dtype=np.float32), prompt="biasing text")
        assert "biasing text" in lifecycle.backend.prompts_seen  # type: ignore[attr-defined]

    async def test_a_prompt_is_dropped_when_the_backend_cannot_use_one(self) -> None:
        from app.services.asr import AsrCapabilities, MockAsrBackend
        from app.services.asr.registry import register

        def factory(config: AsrConfig):  # noqa: ANN202
            return MockAsrBackend(
                MockScript(words=["x"]),
                capabilities=AsrCapabilities(accepts_prompt=False),
                model_id="mock:no-prompt",
            )

        register("mock-no-prompt", factory, lambda: _stub_info())
        lifecycle = AsrLifecycle(mock_config(backend="mock-no-prompt"))
        await lifecycle.load()
        lifecycle.transcribe(np.zeros(16_000, dtype=np.float32), prompt="ignored")

        assert lifecycle.backend.prompts_seen == [None, None]  # type: ignore[attr-defined]


class TestLifecycleSwapAndUnload:
    async def test_swapping_replaces_the_loaded_model(self) -> None:
        lifecycle = AsrLifecycle(mock_config(model="first"))
        await lifecycle.load()
        assert lifecycle.model_id == "mock:first"

        await lifecycle.swap(mock_config(model="second"))
        assert lifecycle.model_id == "mock:second"
        assert lifecycle.is_ready

    async def test_unloading_returns_to_the_unloaded_state(self) -> None:
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.load()
        await lifecycle.unload()

        assert lifecycle.state is LoadState.UNLOADED
        assert lifecycle.backend is None
        assert not lifecycle.is_ready

    async def test_unloading_when_nothing_is_loaded_is_harmless(self) -> None:
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.unload()
        assert lifecycle.state is LoadState.UNLOADED

    async def test_loading_twice_unloads_the_first_model(self) -> None:
        """Otherwise two models sit in device memory at once."""
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.load()
        first = lifecycle.backend
        await lifecycle.load()

        assert lifecycle.backend is not first
        assert first is not None and not first.is_loaded

    async def test_status_is_json_safe(self) -> None:
        lifecycle = AsrLifecycle(mock_config())
        await lifecycle.load()
        status = lifecycle.status()

        assert status["state"] == "ready"
        assert isinstance(status["capabilities"], dict)


class TestFasterWhisperGuards:
    def test_it_is_importable_without_the_optional_dependency(self) -> None:
        """Otherwise the whole ASR package fails to import, taking the mock backend with it."""
        assert FasterWhisperBackend(model="small").backend_id == "faster-whisper"

    def test_loading_without_the_dependency_names_the_install_command(self) -> None:
        if is_available():
            pytest.skip("faster-whisper is installed")

        with pytest.raises(AsrLoadError) as excinfo:
            FasterWhisperBackend().load()

        message = str(excinfo.value)
        assert "uv sync" in message
        assert "mock" in message

    def test_it_declares_that_it_is_not_streaming_native(self) -> None:
        """Whisper is trained on complete segments, so it needs the full commit machinery."""
        caps = FasterWhisperBackend().capabilities
        assert not caps.streaming_native
        assert caps.word_timestamps
        assert caps.max_audio_seconds == 30.0

    def test_the_model_id_records_model_and_precision(self) -> None:
        backend = FasterWhisperBackend(model="medium", precision="float16")
        assert backend.model_id == "faster-whisper:medium:float16"

    def test_transcribing_before_load_is_an_error(self) -> None:
        with pytest.raises(AsrLoadError):
            FasterWhisperBackend().transcribe(np.zeros(1000, dtype=np.float32))

    def test_output_is_converted_into_word_tokens(self) -> None:
        """The nested segment/word shape faster-whisper returns is flattened here."""
        backend = FasterWhisperBackend(model_factory=_fake_whisper_factory)
        backend.load()
        result = backend.transcribe(np.ones(16_000, dtype=np.float32))

        assert [word.text for word in result.words] == ["the", "matrix"]
        assert result.words[0].start == pytest.approx(0.1)
        assert result.words[1].confidence == pytest.approx(0.87)

    def test_empty_audio_short_circuits(self) -> None:
        backend = FasterWhisperBackend(model_factory=_fake_whisper_factory)
        backend.load()
        assert backend.transcribe(np.zeros(0, dtype=np.float32)).is_empty()

    def test_blank_words_are_dropped(self) -> None:
        backend = FasterWhisperBackend(model_factory=_blank_word_factory)
        backend.load()
        assert backend.transcribe(np.ones(1000, dtype=np.float32)).is_empty()

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (RuntimeError("CUDA out of memory"), "smaller model"),
            (RuntimeError("libcudnn.so.9: cannot open shared object file"), "CUDA runtime"),
            (OSError("No such file or directory"), "could not be found"),
            (ValueError("something unexpected"), "Could not load"),
        ],
    )
    def test_load_failures_name_the_likely_cause(
        self, error: Exception, expected: str, monkeypatch
    ) -> None:
        """ "Failed to load" alone leaves the user with nothing to act on (BE §15)."""
        monkeypatch.setattr(acceleration, "_detect_hardware", lambda: ("cuda", "NVIDIA RTX 4090"))

        def failing_factory(*args: object, **kwargs: object):  # noqa: ANN202
            raise error

        with pytest.raises(AsrLoadError, match=expected):
            FasterWhisperBackend(model_factory=failing_factory).load()

    def test_an_amd_machine_is_not_told_to_check_its_nvidia_driver(self, monkeypatch) -> None:
        """CTranslate2 drives ROCm through the CUDA API, so the exception says CUDA either way.

        Observed exactly once, and it cost an evening: a `uv sync` replaced the ROCm build with the
        PyPI one, and the resulting load error sent the owner of a Radeon looking for an NVIDIA
        driver. The hardware is known, so the message says what is really wrong.
        """
        monkeypatch.setattr(acceleration, "_detect_hardware", lambda: ("rocm", "AMD Radeon 8060S"))
        monkeypatch.setattr(acceleration, "_ctranslate2_gpu_support", lambda: (False, []))
        monkeypatch.setattr(acceleration, "_missing_rocm_libraries", list)

        def failing_factory(*args: object, **kwargs: object):  # noqa: ANN202
            raise RuntimeError("CUDA driver version is insufficient for CUDA runtime version")

        with pytest.raises(AsrLoadError) as excinfo:
            FasterWhisperBackend(model_factory=failing_factory).load()

        message = str(excinfo.value)
        assert "NVIDIA" not in message
        assert "ROCm" in message
        assert "uv pip install" in message

    def test_unload_releases_the_model(self) -> None:
        backend = FasterWhisperBackend(model_factory=_fake_whisper_factory)
        backend.load()
        backend.unload()
        assert not backend.is_loaded


def _fake_whisper_factory(*args: object, **kwargs: object):  # noqa: ANN202
    """Stands in for ``WhisperModel``, returning faster-whisper's nested output shape."""

    def transcribe(audio, **kwargs: object):  # noqa: ANN001, ANN202
        word = lambda text, start, end, prob: types.SimpleNamespace(  # noqa: E731
            word=text, start=start, end=end, probability=prob
        )
        segments = [
            types.SimpleNamespace(words=[word(" the", 0.1, 0.3, 0.99)]),
            types.SimpleNamespace(words=[word(" matrix", 0.3, 0.8, 0.87)]),
        ]
        return iter(segments), types.SimpleNamespace(language="en")

    return types.SimpleNamespace(transcribe=transcribe)


def _blank_word_factory(*args: object, **kwargs: object):  # noqa: ANN202
    def transcribe(audio, **kwargs: object):  # noqa: ANN001, ANN202
        blank = types.SimpleNamespace(word="   ", start=0.0, end=0.1, probability=None)
        return iter([types.SimpleNamespace(words=[blank])]), types.SimpleNamespace(language="en")

    return types.SimpleNamespace(transcribe=transcribe)


def _stub_info():  # noqa: ANN202
    from app.services.asr import AsrCapabilities
    from app.services.asr.registry import BackendInfo

    return BackendInfo(
        backend_id="mock-no-prompt",
        name="Mock without prompting",
        family="test",
        available=True,
        unavailable_reason="",
        capabilities=AsrCapabilities(accepts_prompt=False),
        models=[{"name": "scripted", "size": "0 MB"}],
    )
