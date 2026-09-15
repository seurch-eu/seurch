"""Lightweight middleware used by the deployment proxy."""

from django.http import HttpResponse


class HealthCheckMiddleware:
    """Answer health check at ``/up`` with a plain 200."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/up' or request.path == '/up/':
            return HttpResponse('OK', content_type='text/plain')
        return self.get_response(request)
