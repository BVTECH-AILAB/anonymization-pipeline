from anonymization_pipeline import metrics


def test_distribution() -> None:
    assert metrics.distribution([]) is None
    assert metrics.distribution([2.0]) == {
        "mean": 2.0,
        "p50": 2.0,
        "p95": 2.0,
        "max": 2.0,
    }
    d = metrics.distribution([float(x) for x in range(1, 101)])
    assert d == {"mean": 50.5, "p50": 50.5, "p95": 95.05, "max": 100.0}


def test_without_a_collector_nothing_is_recorded() -> None:
    with metrics.measure("x"):
        pass
    metrics.record_call(metrics.Call("detection", None, 1.0, 0.0, 0))

    assert metrics.current() is None
