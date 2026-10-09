"""VLM client: common interface, concurrency limit, retries, per-phase statistics.

The dialects (``ollama.py``, ``openai.py``) implement only the transport (``_send``,
``models_available``, ``pull``). The phase picks the model (``phases.<phase>.model``, one of the
models file catalog), the timeout and the max tokens.
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from anonymization_pipeline import metrics
from anonymization_pipeline.pipeline_config import PipelineConfig
from anonymization_pipeline.pipeline_config.schema import ModelSpec

logger = logging.getLogger(__name__)


async def gather_or_cancel[T](coroutines: Iterable[Awaitable[T]]) -> list[T]:
    """Like ``asyncio.gather``, but on the first error the other requests are cancelled instead of
    running against a client that is about to be closed."""
    tasks = [asyncio.ensure_future(c) for c in coroutines]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


class VLMError(RuntimeError):
    """The backend does not answer, answers with an error or with content that cannot be parsed."""


class VLMModelMissing(VLMError):
    """The model is not in the backend and cannot (or must not) be downloaded."""


class VLMTransientError(VLMError):
    """An error worth retrying (5xx, 429)."""


@dataclass
class VLMResponse:
    text: str
    truncated: bool = False  # answer cut by the token limit
    tokens_in: int = 0
    tokens_out: int = 0
    duration_s: float = 0.0


@dataclass
class Stats:
    calls: int = 0
    errors: int = 0
    truncated: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    duration_s: float = 0.0


class VLMClient:
    """Client of one document run. The HTTP connection pool is shared (``app.state``)."""

    kind = "base"

    def __init__(self, cfg: PipelineConfig, http: httpx.AsyncClient) -> None:
        self.cfg = cfg
        self.backend = cfg.backend
        self.generation = cfg.generation
        self._http = http
        self._headers = (
            {"Authorization": f"Bearer {cfg.backend.api_key}"}
            if cfg.backend.api_key
            else {}
        )
        self._semaphore = asyncio.Semaphore(cfg.backend.concurrency)
        self._pull_lock = asyncio.Lock()
        self.stats: dict[str, Stats] = defaultdict(Stats)

    def url(self, path: str) -> str:
        return self.backend.url.rstrip("/") + path

    async def _post(
        self, path: str, payload: dict[str, Any], timeout: float
    ) -> httpx.Response:
        return await self._http.post(
            self.url(path), json=payload, headers=self._headers, timeout=timeout
        )

    async def _get(self, path: str, timeout: float) -> httpx.Response:
        return await self._http.get(
            self.url(path), headers=self._headers, timeout=timeout
        )

    # --- implemented by the dialects -----------------------------------------------------------

    async def _send(
        self,
        model: str,
        spec: ModelSpec,
        system: str,
        user: str,
        images: list[bytes],
        schema: dict[str, Any] | None,
        max_tokens: int,
        timeout: float,
    ) -> VLMResponse:
        raise NotImplementedError

    async def models_available(self) -> set[str]:
        raise NotImplementedError

    async def pull(self, name: str, force: bool = False) -> None:
        raise NotImplementedError

    def has_model(self, available: set[str], name: str) -> bool:
        """True if the model ``name`` is among ``available``."""
        return name in available

    # --- common ----------------------------------------------------------------------------------

    async def is_available(self) -> bool:
        try:
            await self.models_available()
        except (httpx.HTTPError, VLMError):
            return False
        return True

    async def call(
        self,
        phase: str,
        system: str,
        user: str,
        images: list[bytes] | None = None,
        schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> VLMResponse:
        """One model call, with retries on transient errors.

        Args:
            max_tokens: Token limit of the answer, at most the phase one (None: the phase one).

        Raises:
            VLMError: The backend is unreachable or keeps failing.
        """
        phase_cfg = getattr(self.cfg.phases, phase)
        model, spec = self.cfg.model_for(phase)
        stats = self.stats[phase]
        last: Exception | None = None
        waited = 0.0  # time spent on the concurrency semaphore, over every attempt
        for attempt in range(self.backend.retries + 1):
            if attempt:
                await asyncio.sleep(self.backend.retry_wait_s * 2 ** (attempt - 1))
            queued = time.perf_counter()
            try:
                async with self._semaphore:
                    t0 = time.perf_counter()
                    waited += t0 - queued
                    response = await self._send(
                        model,
                        spec,
                        system,
                        user,
                        list(images or []),
                        schema,
                        min(max_tokens or phase_cfg.max_tokens, phase_cfg.max_tokens),
                        phase_cfg.timeout_s,
                    )
                    response.duration_s = time.perf_counter() - t0
            except (
                httpx.TransportError,
                VLMTransientError,
            ) as exc:  # TimeoutException is a TransportError
                stats.errors += 1
                last = exc
                logger.warning(
                    "VLM call failed (%s, attempt %d/%d): %s",
                    phase,
                    attempt + 1,
                    self.backend.retries + 1,
                    type(exc).__name__,
                )
                continue
            except VLMError:  # missing model, 4xx: retrying is useless
                stats.errors += 1
                metrics.record_call(
                    metrics.Call(
                        phase, metrics.current_page(), 0.0, waited, attempt, error=True
                    )
                )
                raise
            stats.calls += 1
            metrics.record_call(
                metrics.Call(
                    phase,
                    metrics.current_page(),
                    response.duration_s,
                    waited,
                    attempt,
                    response.tokens_in,
                    response.tokens_out,
                    response.truncated,
                )
            )
            stats.truncated += int(response.truncated)
            stats.tokens_in += response.tokens_in
            stats.tokens_out += response.tokens_out
            stats.duration_s += response.duration_s
            return response
        metrics.record_call(
            metrics.Call(
                phase,
                metrics.current_page(),
                0.0,
                waited,
                self.backend.retries,
                error=True,
            )
        )
        raise VLMError(
            f"VLM backend unreachable or failing (phase {phase}): {type(last).__name__}"
        )

    async def call_json[T](
        self,
        phase: str,
        system: str,
        user: str,
        images: list[bytes] | None,
        schema: dict[str, Any],
        parse: Callable[[str], T | None],
        max_tokens: int | None = None,
    ) -> tuple[T, VLMResponse]:
        """Call with a JSON answer: ``parse(text)`` returns None when the answer is not valid, and
        the call is retried, unless the answer was cut by the token limit and generation is
        deterministic (temperature 0, fixed seed): the same request would be cut again (a model
        repeating itself)."""
        g = self.generation
        deterministic = g.temperature == 0 and g.seed is not None
        for attempt in range(self.backend.retries + 1):
            response = await self.call(phase, system, user, images, schema, max_tokens)
            data = parse(response.text)
            if data is not None:
                return data, response
            if response.truncated and deterministic:
                raise VLMError(f"answer cut at the token limit (phase {phase})")
            logger.warning(
                "Answer is not valid JSON (%s, attempt %d/%d, truncated=%s)",
                phase,
                attempt + 1,
                self.backend.retries + 1,
                response.truncated,
            )
        raise VLMError(f"invalid model answer (phase {phase})")

    def summary(self) -> dict[str, dict[str, Any]]:
        return {
            phase: {
                k: round(v, 2) if isinstance(v, float) else v
                for k, v in asdict(s).items()
            }
            for phase, s in self.stats.items()
        }

    @staticmethod
    def check(response: httpx.Response) -> None:
        """Raise the right error for an HTTP error status.

        Raises:
            VLMTransientError: On 5xx and 429.
            VLMError: On the other 4xx.
        """
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("error") or response.text
        except ValueError:
            detail = response.text
        detail = str(detail)[:300]
        if response.status_code >= 500 or response.status_code == 429:
            raise VLMTransientError(f"HTTP {response.status_code}: {detail}")
        raise VLMError(f"HTTP {response.status_code}: {detail}")
