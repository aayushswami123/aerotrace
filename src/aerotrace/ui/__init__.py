"""Presentation for `aerotrace check`: the terminal text and the offline HTML report.

Both read the versioned `aerotrace-check` JSON (see `schema.py` and docs/plans/launch-ui.md). Nothing in
this package decides what breaks or how sure a link is; the producer (analysis) puts every status,
certainty, reason and count into the JSON. This package only checks its shape and formats it.
"""

from aerotrace.ui.html import render as render_html
from aerotrace.ui.html import write_html
from aerotrace.ui.schema import ContractError, check
from aerotrace.ui.terminal import render as render_text
from aerotrace.ui.terminal import style_for

__all__ = ["ContractError", "check", "render_html", "render_text", "style_for", "write_html"]
