"""Demo web page (upload, progress, side-by-side preview, edits, downloads).

Served only when ``ANONPIP_UI_ENABLED=true``: in the platform the doctor-facing
frontend is ``fe``, this page is for local testing and demos.
"""

from importlib import resources

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from anonmyzation_pipeline.errors import NotFoundError

router = APIRouter(tags=["ui"])

_PAGE = resources.files("anonmyzation_pipeline.web").joinpath("static/index.html")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    """The single-page UI."""
    if not request.app.state.settings.ui_enabled:
        raise NotFoundError()
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))
