"""Client of the OCR server: the three endpoints of the engine's OCR contract.

``src/services/anonymizer/OCR_CONTRACT.md`` describes them; the reference server
is ``ocr-server/``. Page images are health data: nothing here logs them or
what the server reads.
"""

import httpx


class OcrClient:
    """``GET /health``, ``POST /ocr`` and ``POST /recognize`` over one HTTP client.

    Args:
        server_url: Base URL of the OCR server (``http://paddle-ocr:8829``);
            empty: ``check`` always fails.
        client: HTTP client (tests inject a stub transport).
        language: Language sent with every page.
    """

    def __init__(
        self, server_url: str, client: httpx.Client, language: str = "it"
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.language = language
        self._client = client

    def check(self) -> None:
        """Check that the server is configured and healthy.

        Raises:
            RuntimeError: No server configured, or ``/health`` is not 200.
        """
        if not self.server_url:
            raise RuntimeError("no OCR server configured")
        r = self._client.get(f"{self.server_url}/health")
        if r.status_code != 200:
            raise RuntimeError(f"OCR server not ready: status={r.status_code}")

    def read(self, png: bytes, dpi: int) -> list[dict]:
        """Every word of one page image, as the contract's ``results``.

        Args:
            png: The page as displayed, rendered at ``dpi``.
            dpi: Resolution of the image (``X-Render-DPI``).

        Raises:
            httpx.HTTPStatusError: The server answered with an error.
        """
        r = self._client.post(
            f"{self.server_url}/ocr",
            files={"file": ("page.png", png, "image/png")},
            data={"language": self.language},
            headers={"X-Render-DPI": str(dpi)},
        )
        r.raise_for_status()
        return r.json().get("results", [])

    def recognize(self, png: bytes) -> tuple[str, float]:
        """Text and confidence of an image holding one word or line (no detection).

        Raises:
            httpx.HTTPStatusError: The server answered with an error.
        """
        r = self._client.post(
            f"{self.server_url}/recognize",
            files={"file": ("word.png", png, "image/png")},
        )
        r.raise_for_status()
        body = r.json()
        return str(body.get("text", "")), float(body.get("confidence", 0.0))
