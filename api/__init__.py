"""The public JSON API (Django REST Framework), mounted under ``/api/v1/``.

This app was generated with an LLM and then reviewed by hand. The ``fetch_*``
service functions it calls are not: they are hand-written and shared with the
website, so what was generated is the layer around them, the endpoints, the
response shape, the key handling and the throttling. Read
``api.serializers`` before assuming a field is guaranteed, and treat a passing
test in ``api.tests`` (generated too) as a check, not as a specification.

See the "AI-generated code and docs" section of the README.
"""
