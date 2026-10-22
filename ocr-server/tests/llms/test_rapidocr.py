"""The RapidOCR engine: parsing, loading, GPU memory and device choice."""

import logging
from typing import Any

import numpy as np
import pytest

from src.config import Settings
from src.llms import rapidocr as engine
from src.llms.rapidocr import RapidEngine, parse_rapid
from src.services.words import results


def test_rapid_result_keeps_its_word_boxes() -> None:
    class Res:
        txts = ("Mario Rossi", "x")
        scores = (0.9, 0.8)
        boxes = [
            np.array([[0, 0], [100, 0], [100, 10], [0, 10]]),
            np.array([[0, 20], [10, 20], [10, 30], [0, 30]]),
        ]
        word_results = (
            (
                ("Mario", 0.9, [[0, 0], [45, 0], [45, 10], [0, 10]]),
                ("Rossi", 0.9, [[52, 0], [100, 0], [100, 10], [52, 10]]),
            ),
            (("", 1.0, None),),
        )

    lines = parse_rapid(Res())
    assert [line.text for line in lines] == ["Mario Rossi", "x"]
    assert len(lines[0].words) == 2
    out = results(lines)
    # recogniser boxes used as they are
    assert out[0]["bbox"] == [0.0, 0.0, 45.0, 10.0]
    assert out[1]["bbox"] == [52.0, 0.0, 100.0, 10.0]


class _FakeRapid:
    calls: list[tuple[int, ...]] = []
    kwargs: list[dict] = []
    reads: tuple[str, ...] = ("Referto",)
    last_params: dict = {}

    def __init__(self, params: dict) -> None:
        self.params = params
        _FakeRapid.last_params = params

    def __call__(self, img: np.ndarray, **kwargs: object) -> object:
        _FakeRapid.calls.append(img.shape)
        _FakeRapid.kwargs.append(kwargs)
        return type("Res", (), {"txts": _FakeRapid.reads})()


def test_load_reads_a_sample_so_health_waits_for_every_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # RapidOCR loads its models on the first image: without a first read /health
    # said ready before any model was loaded or downloaded
    monkeypatch.setattr(engine, "RapidOCR", _FakeRapid)
    _FakeRapid.calls, _FakeRapid.reads = [], ("Referto",)
    ocr = RapidEngine(Settings(device="cpu"))
    ocr.load()
    assert len(_FakeRapid.calls) == 1 and ocr.ready


def test_line_orientation_classifier_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # PP-OCRv5's line classifier turns upright lines by 180 degrees: they come
    # back as noise and whole lines with personal data are lost
    monkeypatch.setattr(engine, "RapidOCR", _FakeRapid)
    _FakeRapid.calls, _FakeRapid.reads = [], ("Referto",)
    ocr = RapidEngine(Settings(device="cpu"))
    ocr.load()
    assert _FakeRapid.last_params["Global.use_cls"] is False
    ocr.predict(np.zeros((10, 10, 3), np.uint8))
    assert _FakeRapid.kwargs[-1]["use_cls"] is False
    # every line comes back: settle_lines decides on the low ones
    assert _FakeRapid.kwargs[-1]["text_score"] == 0.0
    assert ocr.recognize(np.zeros((10, 10, 3), np.uint8)) == ("", 0.0)
    assert _FakeRapid.kwargs[-1]["use_det"] is False


def test_load_fails_when_the_sample_reads_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine, "RapidOCR", _FakeRapid)
    _FakeRapid.calls, _FakeRapid.reads = [], ()
    ocr = RapidEngine(Settings(device="cpu"))
    with pytest.raises(RuntimeError):
        ocr.load()
    assert not ocr.ready


class _FakeOrtSession:
    def __init__(self, providers: tuple[str, ...] = ("CUDAExecutionProvider",)) -> None:
        self.options: list[Any] = []
        self.providers = list(providers)

    def get_providers(self) -> list[str]:
        return self.providers

    def run(self, names: list[str], feed: dict, run_options: object) -> list:
        self.options.append(run_options)
        return [names, feed]

    def get_inputs(self) -> list[str]:
        return ["x"]


def _rapid_with_sessions(
    providers: tuple[str, ...] = ("CUDAExecutionProvider", "CPUExecutionProvider"),
) -> tuple[Any, list[_FakeOrtSession]]:
    sessions = [_FakeOrtSession(providers) for _ in range(3)]

    def model(s: _FakeOrtSession) -> object:
        inner = type("Inner", (), {"session": s})()
        return type("Model", (), {"session": inner})()

    det, cls, rec = (model(s) for s in sessions)
    ocr = type("Ocr", (), {"text_det": det, "text_cls": cls, "text_rec": rec})()
    return ocr, sessions


def test_gpu_memory_is_released_after_every_run() -> None:
    # without it ONNX Runtime keeps the peak of every page size: 7.8 GB on an 8 GB
    # card for a 4 GB real peak, and nothing left for the NER models
    ocr, sessions = _rapid_with_sessions()
    assert engine.release_gpu_memory_after_runs(ocr) is True
    for s, model in zip(
        sessions, (ocr.text_det, ocr.text_cls, ocr.text_rec), strict=True
    ):
        assert model.session.session.run(["y"], {"x": 1}) == [["y"], {"x": 1}]
        assert model.session.session.get_inputs() == ["x"]
        (options,) = s.options
        assert (
            options.get_run_config_entry("memory.enable_memory_arena_shrinkage")
            == "gpu:0"
        )


def test_gpu_memory_limit_setting() -> None:
    assert Settings().gpu_mem_limit_mb == 0  # no cap: the real peak is about 4 GB
    with pytest.raises(ValueError):
        Settings(gpu_mem_limit_mb=-1)


def test_models_that_fell_back_to_the_cpu_are_left_alone() -> None:
    # no GPU on the node: ONNX Runtime falls back to the CPU, where a "gpu:0"
    # shrinkage request fails every run
    ocr, sessions = _rapid_with_sessions(("CPUExecutionProvider",))
    assert engine.release_gpu_memory_after_runs(ocr) is False
    assert ocr.text_det.session.session is sessions[0]


def _no_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "RapidOCR", _FakeRapid)
    monkeypatch.setattr(engine, "gpu_count", lambda: 0)
    monkeypatch.setattr(
        engine.onnxruntime,
        "get_available_providers",
        lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    _FakeRapid.calls, _FakeRapid.reads = [], ("Referto",)


def test_without_a_gpu_the_cpu_is_chosen_before_loading(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # asking ONNX Runtime for CUDA on a node without a GPU prints three
    # "EP Error" blocks outside the log format before it falls back to the CPU
    _no_gpu(monkeypatch)
    caplog.set_level(logging.INFO)
    ocr = RapidEngine(Settings(device="auto"))
    ocr.load()
    assert ocr.ready
    assert "EngineConfig.onnxruntime.use_cuda" not in _FakeRapid.last_params
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "No usable GPU" in warnings[0].getMessage()


def test_device_gpu_without_a_gpu_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_gpu(monkeypatch)
    ocr = RapidEngine(Settings(device="gpu"))
    with pytest.raises(RuntimeError):
        ocr.load()
    assert not ocr.ready


def test_gpu_count_without_the_nvidia_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_: str) -> None:
        raise OSError("libcuda.so.1: cannot open shared object file")

    monkeypatch.setattr(engine.ctypes, "CDLL", missing)
    assert engine.gpu_count() == 0
