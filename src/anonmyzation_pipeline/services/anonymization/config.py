"""Engine configuration: plain values, no environment lookup.

The service builds it from its ``Settings`` (``Settings.pipeline_config``);
scripts and other callers build it directly.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PipelineConfig:
    """What the text reader, the NER engine and the detectors need.

    Attributes:
        ocr_server_url: Base URL of the OCR server; empty: the reader never loads.
        ocr_dpi: Resolution of the pages sent to OCR.
        ner_enabled: Use the NER service at all.
        ner_url: Base URL of the NER service; empty: the engine never loads.
        ner_timeout_seconds: Timeout of one NER request.
        ner_retries: Retries of a request that timed out or got 502/503/504.
        ner_retry_backoff_seconds: First wait between retries (doubles each time).
        ner_ca_bundle: PEM bundle of the CA of the NER certificate; empty: system CAs.
        ner_threshold: Minimum NER score.
        ner_person_threshold: Minimum NER score of PERSON entities.
        redaction_policy: Overrides of the taxonomy defaults (``{"AGE": True}``).
    """

    ocr_server_url: str = ""
    ocr_dpi: int = 300
    ner_enabled: bool = True
    ner_url: str = ""
    ner_timeout_seconds: float = 30
    ner_retries: int = 2
    ner_retry_backoff_seconds: float = 1.0
    ner_ca_bundle: str = ""
    ner_threshold: float = 0.5
    ner_person_threshold: float = 0.4
    redaction_policy: dict[str, bool] = field(default_factory=dict)
