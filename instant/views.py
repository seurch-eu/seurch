"""Lightweight same-origin endpoints backing the interactive widgets.

Most instant-answer widgets recompute entirely in the browser. These two
helpers cover the cases that are awkward client-side, QR rendering and MD5,
without sending anything to a third party.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.http import require_GET

from . import qr, tools


@login_required
@require_GET
def qr_svg(request):
    text = request.GET.get('text', '')
    svg = qr.make_svg(text)
    if svg is None:
        return HttpResponseBadRequest('Invalid or empty QR text')
    resp = HttpResponse(svg, content_type='image/svg+xml')
    resp['Cache-Control'] = 'private, max-age=300'
    return resp


@login_required
@require_GET
def hashes(request):
    text = request.GET.get('text', '')
    return JsonResponse(tools.compute_hashes(text))
