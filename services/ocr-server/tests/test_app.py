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
from paddle_ocr_server.words import Line, Quad, results

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

    def recognize(self, _img: np.ndarray) -> tuple[str, float]:
        if self.fail:
            raise RuntimeError(SECRET)
        return SECRET, 0.91234


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


def test_recognize_reads_one_line_and_never_logs_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with _client(FakeEngine()) as client:
        r = client.post("/recognize", files={"file": ("w.png", _png(), "image/png")})
        bad = client.post("/recognize", files={"file": ("w.png", b"x", "image/png")})
    with _client(FakeEngine(fail=True)) as client:
        failed = client.post(
            "/recognize", files={"file": ("w.png", _png(), "image/png")}
        )
    assert r.status_code == 200
    assert r.json() == {"text": SECRET, "confidence": 0.9123}
    assert bad.status_code == 400
    assert failed.status_code == 500 and failed.json() == {"error": "OCR failed"}
    assert SECRET not in caplog.text


def test_recognize_is_503_until_models_load() -> None:
    client = TestClient(create_app(Settings(), FakeEngine(ready=False)))  # type: ignore[arg-type]
    r = client.post("/recognize", files={"file": ("w.png", _png(), "image/png")})
    assert r.status_code == 503


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
        return [Line("Referto", 0.95, _quad(10, 10, 80, 20))]

    page_results(img, 144, predict, margin_pt=36, min_score=0.5)
    assert widths == [100, 72, 72, 72, 72]  # page, then 36 pt at 144 dpi = 72 px strips
    widths.clear()
    page_results(img, 144, predict, margin_pt=0, min_score=0.5)
    assert widths == [100]


def _quad(x0: float, y0: float, x1: float, y1: float) -> Quad:
    return ((x0, y0), (x1, y0), (x1, y1), (x0, y1))


def _texts(found: list[dict]) -> list[str]:
    return [r["text"] for r in found]


def test_upright_page_is_read_once() -> None:
    calls: list[tuple[int, ...]] = []

    def predict(img: np.ndarray) -> list[Line]:
        calls.append(img.shape)
        return [Line("Rossi", 0.98, _quad(10, 10, 60, 20))]

    assert _texts(page_results(np.zeros((800, 600, 3), np.uint8), 72, predict, 0, 0.5))
    assert len(calls) == 1


def test_upside_down_page_is_read_turned_and_mapped_back() -> None:
    # the text sits in the bottom-right corner, upside down: turned by 180 degrees
    # it is in the top-left corner, where the fake engine reads it
    img = np.full((800, 600, 3), 255, np.uint8)
    img[760:780, 500:560] = 0

    def predict(a: np.ndarray) -> list[Line]:
        h, w = a.shape[:2]
        top_left = a[: h // 4, : w // 4]
        if top_left.min() < 128:
            ys, xs = np.nonzero(top_left.min(axis=2) < 128)
            box = _quad(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
            return [Line("Rossi", 0.97, box)]
        return [Line("e e r", 0.45, _quad(0, 0, w / 2, 10))]

    (word,) = page_results(img, 72, predict, 0, 0.5)
    assert word["text"] == "Rossi"
    assert word["bbox"] == pytest.approx([500, 760, 560, 780], abs=1)
    # TL -> TR runs right to left: the reading frame of text upside down
    assert word["polygon"][1][0] < word["polygon"][0][0]


def test_poor_reading_is_kept_when_the_turned_page_is_not_better() -> None:
    def predict(_: np.ndarray) -> list[Line]:
        return [Line("Bianchi", 0.6, _quad(10, 10, 60, 20))]

    (word,) = page_results(np.zeros((800, 600, 3), np.uint8), 72, predict, 0, 0.5)
    assert word["text"] == "Bianchi"
    assert word["polygon"][1][0] > word["polygon"][0][0]


def test_a_degraded_page_is_not_swapped_for_a_noisy_turned_reading() -> None:
    # a faded fax read poorly: turned, it gives more text but just as unsure
    img = np.full((800, 600, 3), 255, np.uint8)
    img[770:790, 540:580] = 0

    def predict(a: np.ndarray) -> list[Line]:
        if a[760:, 520:].min() < 128:  # the marker sits bottom-right: upright
            return [Line("Bianchi", 0.6, _quad(10, 10, 60, 20))]
        return [Line("eerr nnee aaee rrnn", 0.7, _quad(10, 10, 200, 20))]

    assert _texts(page_results(img, 72, predict, 0, 0.5)) == ["Bianchi"]


def _sticker(_: np.ndarray) -> list[Line]:
    """A page read well, but for a sticker pasted upside down read as noise."""
    return [
        Line("Paziente: ROSSI", 0.99, _quad(10, 10, 140, 20)),
        Line("e", 0.31, _quad(200, 100, 260, 112)),
        Line("rr", 0.28, _quad(270, 100, 330, 112)),
        Line("Ricovero", 0.97, _quad(10, 200, 80, 212)),
    ]


def test_a_line_upside_down_on_an_upright_page_is_read_turned() -> None:
    seen: list[np.ndarray] = []

    def recognize(img: np.ndarray) -> tuple[str, float]:
        seen.append(img)
        return "Mario Rossi", 0.96

    img = np.zeros((800, 600, 3), np.uint8)
    found = page_results(img, 72, _sticker, 0, 0.5, recognize)

    assert _texts(found) == ["Paziente:", "ROSSI", "Mario", "Rossi", "Ricovero"]
    mario, rossi = found[2]["bbox"], found[3]["bbox"]
    # upside down the text runs right to left: the first word is on the right
    assert mario[0] > rossi[2] - 1
    assert rossi[0] == pytest.approx(200, abs=1)
    assert mario[2] == pytest.approx(330, abs=1)
    # the pieces are passed as one crop, turned, at its own size
    assert len(seen) == 1 and seen[0].shape[1] >= 130


def test_pieces_of_an_upside_down_line_are_read_again_together() -> None:
    # the detector cuts a line upside down into pieces; one piece is read just
    # above the noise threshold ("1138" for "1176"): the pieces on the band are
    # read again as one crop, the confident line below is left alone
    def predict(_: np.ndarray) -> list[Line]:
        return [
            Line("326", 0.14, _quad(200, 100, 240, 112)),
            Line("808", 0.21, _quad(246, 100, 286, 112)),
            Line("1138", 0.51, _quad(292, 100, 330, 112)),
            Line("Cl1", 0.21, _quad(336, 100, 360, 112)),
            Line("Ricovero", 0.97, _quad(200, 130, 280, 142)),
        ]

    seen: list[np.ndarray] = []

    def recognize(img: np.ndarray) -> tuple[str, float]:
        seen.append(img)
        return "Tel. 348 905 1176", 0.97

    img = np.zeros((800, 600, 3), np.uint8)
    found = page_results(img, 72, predict, 0, 0.5, recognize)

    assert _texts(found) == ["Tel.", "348", "905", "1176", "Ricovero"]
    assert len(seen) == 1 and seen[0].shape[1] >= 160


@pytest.mark.parametrize("recognize", [None, lambda _: ("x7 ;", 0.4)])
def test_a_noise_line_that_is_not_upside_down_is_dropped(recognize) -> None:
    img = np.zeros((800, 600, 3), np.uint8)
    found = page_results(img, 72, _sticker, 0, 0.5, recognize)
    assert _texts(found) == ["Paziente:", "ROSSI", "Ricovero"]


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
    reads = ("Referto",)

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
