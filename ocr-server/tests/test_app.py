import io
import logging

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from paddle_ocr_server import engine
from paddle_ocr_server.app import create_app
from paddle_ocr_server.config import Settings
from paddle_ocr_server.engine import (
    RapidEngine,
    margin_results,
    page_results,
    parse_rapid,
)
from paddle_ocr_server.words import Line, results

SECRET = "RSSMRA85M01H501U"  # noqa: S105 - textbook codice fiscale, not a credential


class FakeEngine:
    def __init__(self, ready: bool = True, fail: bool = False) -> None:
        self._ready = ready
        self.fail = fail
        self.dpi: float | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    def load(self) -> None:
        self._ready = True

    def read(self, _img: np.ndarray, dpi: float) -> list[dict]:
        if self.fail:
            raise RuntimeError(SECRET)
        self.dpi = dpi
        return [{"text": SECRET, "bbox": [0, 0, 10, 10], "confidence": 0.9}]


def _png(w: int = 40, h: int = 20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _client(engine: FakeEngine, **settings: object) -> TestClient:
    return TestClient(create_app(Settings(**settings), engine))  # type: ignore[arg-type]


def test_ocr_follows_liteparse_contract_and_never_logs_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = FakeEngine()
    caplog.set_level(logging.DEBUG)
    with _client(engine) as client:
        r = client.post(
            "/ocr",
            files={"file": ("p.png", _png(), "image/png")},
            data={"language": "it"},
            headers={"X-Render-DPI": "300"},
        )
    assert r.status_code == 200
    assert r.json() == {
        "results": [{"text": SECRET, "bbox": [0, 0, 10, 10], "confidence": 0.9}]
    }
    assert engine.dpi == 300
    assert SECRET not in caplog.text


def test_default_dpi_without_header() -> None:
    engine = FakeEngine()
    with _client(engine, default_dpi=150) as client:
        client.post("/ocr", files={"file": ("p.png", _png(), "image/png")})
    assert engine.dpi == 150


def test_health_is_503_until_models_load() -> None:
    engine = FakeEngine(ready=False)
    app = create_app(Settings(), engine)  # type: ignore[arg-type]
    client = TestClient(app)  # no lifespan: nothing loads
    assert client.get("/health").status_code == 503
    assert (
        client.post("/ocr", files={"file": ("p.png", _png(), "image/png")}).status_code
        == 503
    )
    with TestClient(app) as started:
        assert started.get("/health").json() == {"status": "healthy"}


def test_bad_image_and_engine_failure(caplog: pytest.LogCaptureFixture) -> None:
    with _client(FakeEngine()) as client:
        r = client.post("/ocr", files={"file": ("p.png", b"not an image", "image/png")})
        assert r.status_code == 400 and r.json() == {"error": "unreadable image"}
    with _client(FakeEngine(), max_upload_mb=1) as client:
        r = client.post(
            "/ocr", files={"file": ("p.png", b"0" * (1024 * 1024 + 1), "image/png")}
        )
        assert r.status_code == 400
    with _client(FakeEngine(fail=True)) as client:
        r = client.post("/ocr", files={"file": ("p.png", _png(), "image/png")})
    assert r.status_code == 500 and r.json() == {"error": "OCR failed"}
    assert SECRET not in caplog.text


def test_margin_pass_keeps_the_confident_rotation() -> None:
    img = np.zeros((300, 200, 3), np.uint8)
    upright = ((0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0))
    calls: list[tuple[int, int]] = []

    def predict(strip: np.ndarray) -> list[Line]:
        calls.append(strip.shape[:2])
        # first call of each strip is the clockwise rotation: make it the good one
        return [Line("Mario Rossi", 0.9 if len(calls) % 2 else 0.3, upright)]

    out = margin_results(img, 20, predict, 0.5)
    assert calls == [(20, 300)] * 4  # rotated strips: 20 px high, page-height wide
    assert [r["text"] for r in out] == ["Mario", "Rossi", "Mario", "Rossi"]
    # clockwise reading: bottom to top on the page, inside the strip
    left = out[0]["polygon"]
    assert left[1][1] < left[0][1]
    assert all(0 <= x <= 20 for x, _ in left)
    right = out[2]["bbox"]
    assert right[0] >= 180


def test_page_results_margin_width_follows_dpi() -> None:
    img = np.zeros((100, 400, 3), np.uint8)
    widths: list[int] = []

    def predict(a: np.ndarray) -> list[Line]:
        widths.append(a.shape[0])
        return []

    page_results(img, 144, predict, margin_pt=36, min_score=0.5)
    assert widths == [100, 72, 72, 72, 72]  # page, then 36 pt at 144 dpi = 72 px strips
    widths.clear()
    page_results(img, 144, predict, margin_pt=0, min_score=0.5)
    assert widths == [100]


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
    reads = ("Referto",)

    def __init__(self, params: dict) -> None:
        self.params = params
        _FakeRapid.last_params = params

    def __call__(self, img: np.ndarray, **_: object) -> object:
        _FakeRapid.calls.append(img.shape)
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
        self.options: list[object] = []
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
) -> tuple[object, list[_FakeOrtSession]]:
    sessions = [_FakeOrtSession(providers) for _ in range(3)]

    def model(s: _FakeOrtSession) -> object:
        inner = type("Inner", (), {"session": s})()
        return type("Model", (), {"session": inner})()

    ocr = type("Ocr", (), {})()
    ocr.text_det, ocr.text_cls, ocr.text_rec = (model(s) for s in sessions)  # type: ignore[attr-defined]
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
