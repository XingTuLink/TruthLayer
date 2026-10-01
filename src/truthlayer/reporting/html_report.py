"""Static, self-contained HTML rendering of ReportDTO (#32, #56).

One Jinja2 template with inline CSS — no frontend build step, no external
assets. Autoescape is mandatory because entity names and evidence quotes
are model-generated, untrusted text.
"""

from __future__ import annotations

import base64
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from truthlayer.reporting.dto import ReportDTO

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_ASSETS_DIR = Path(__file__).parent / "assets"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    # The .j2 suffix is NOT auto-enabled by default — list it explicitly so
    # model-generated entity names/evidence quotes can never inject markup.
    autoescape=select_autoescape(("html", "htm", "xml", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _brand_logo_uri() -> str | None:
    """Inline the packaged brand logo as a data URI so the report stays a
    single offline file. Returns None when no logo asset is present — the
    template then falls back to its built-in shield mark."""
    logo = _ASSETS_DIR / "logo.png"
    if not logo.is_file():
        return None
    encoded = base64.b64encode(logo.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def render_html(report: ReportDTO) -> str:
    template = _env.get_template("report.html.j2")
    return template.render(report=report, logo_uri=_brand_logo_uri())
