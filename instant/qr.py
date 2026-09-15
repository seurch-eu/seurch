"""QR code generation via segno (pure Python, no network, no third party).

Returns an inline SVG string so the browser never makes an external request,
the QR is rendered entirely from data the user typed.
"""

import io
import logging
import re

import segno

logger = logging.getLogger(__name__)

MAX_QR_LEN = 1200

# Dark-on-white is scannable regardless of page theme, so the SVG is rendered
# with fixed colours rather than following the colour scheme.
_QR_DARK = '#0f172a'
_QR_LIGHT = '#ffffff'

_SIZE_RE = re.compile(r'width="(\d+(?:\.\d+)?)"\s+height="(\d+(?:\.\d+)?)"')


def make_svg(text):
    """Return a responsive inline SVG for ``text``, or None on failure."""
    text = (text or '').strip()
    if not text or len(text) > MAX_QR_LEN:
        return None
    try:
        qr = segno.make(text, error='m')
        buff = io.BytesIO()
        qr.save(
            buff, kind='svg', scale=8, border=2,
            dark=_QR_DARK, light=_QR_LIGHT,
            xmldecl=False, svgns=True,
        )
        svg = buff.getvalue().decode('utf-8')
        return _make_responsive(svg)
    except Exception as exc:
        logger.warning('qr generation error: %s', exc)
        return None


def _make_responsive(svg):
    """Add a viewBox and drop the fixed pixel size so CSS controls the box."""
    m = _SIZE_RE.search(svg)
    if not m:
        return svg
    w, h = m.group(1), m.group(2)
    return _SIZE_RE.sub(
        f'viewBox="0 0 {w} {h}" width="100%" height="100%" '
        f'preserveAspectRatio="xMidYMid meet"',
        svg,
        count=1,
    )
