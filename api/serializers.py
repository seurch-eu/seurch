"""Output serializers for the public API.

These shape the dicts the ``fetch_*`` service functions already return into a
stable, documented JSON contract - flattening the few template-oriented nested
fields (``thumbnail.src``, ``meta_url.hostname``, ``video.duration``) and
dropping internal/presentational ones. Every field carries a ``default`` so a
provider that omits a key yields an empty value rather than an error.

Knowledge cards and instant answers are intentionally *not* serialized here:
their shape varies by provider / answer type, so the views return them as-is
(provider-shaped JSON) - see ``api.views``.
"""

from rest_framework import serializers


class WebResultSerializer(serializers.Serializer):
    title = serializers.CharField(default='')
    url = serializers.CharField(default='')
    description = serializers.CharField(default='', allow_blank=True)
    display_url = serializers.CharField(default='', allow_blank=True)
    favicon_url = serializers.CharField(default='', allow_blank=True)
    age = serializers.CharField(default='', allow_blank=True)
    # Which engine(s) returned the result: 'brave'/'mojeek'/'marginalia'/'staan',
    # or 'both'/'all' for cross-engine agreement; source_label is the readable form.
    source = serializers.CharField(default='', allow_blank=True)
    source_label = serializers.CharField(default='', allow_blank=True)
    sitelinks = serializers.JSONField(default=list)


class ImageResultSerializer(serializers.Serializer):
    title = serializers.CharField(default='', allow_blank=True)
    url = serializers.CharField(default='')
    source = serializers.CharField(default='', allow_blank=True)
    thumbnail = serializers.CharField(source='thumbnail.src', default='', allow_blank=True)


class NewsResultSerializer(serializers.Serializer):
    title = serializers.CharField(default='', allow_blank=True)
    url = serializers.CharField(default='')
    description = serializers.CharField(default='', allow_blank=True)
    age = serializers.CharField(default='', allow_blank=True)
    source = serializers.CharField(default='', allow_blank=True)
    hostname = serializers.CharField(source='meta_url.hostname', default='', allow_blank=True)
    thumbnail = serializers.CharField(source='thumbnail.src', default='', allow_blank=True)


class VideoResultSerializer(serializers.Serializer):
    title = serializers.CharField(default='', allow_blank=True)
    url = serializers.CharField(default='')
    age = serializers.CharField(default='', allow_blank=True)
    source = serializers.CharField(default='', allow_blank=True)
    duration = serializers.CharField(source='video.duration', default='', allow_blank=True)
    hostname = serializers.CharField(source='meta_url.hostname', default='', allow_blank=True)
    thumbnail = serializers.CharField(source='thumbnail.src', default='', allow_blank=True)


class PlaceSerializer(serializers.Serializer):
    name = serializers.CharField(default='', allow_blank=True)
    display_name = serializers.CharField(default='', allow_blank=True)
    lat = serializers.FloatField(default=None)
    lon = serializers.FloatField(default=None)
    category = serializers.CharField(default='', allow_blank=True)
    type = serializers.CharField(default='', allow_blank=True)
    addresstype = serializers.CharField(default='', allow_blank=True)
    importance = serializers.FloatField(default=0)
    embed_url = serializers.CharField(default='', allow_blank=True)
    mini_embed_url = serializers.CharField(default='', allow_blank=True)
    osm_url = serializers.CharField(default='', allow_blank=True)
    directions_url = serializers.CharField(default='', allow_blank=True)
    geo_uri = serializers.CharField(default='', allow_blank=True)


class TranslationSerializer(serializers.Serializer):
    translated_text = serializers.CharField(default='', allow_blank=True)
    # Empty unless the source language was auto-detected.
    detected_lang = serializers.CharField(default='', allow_blank=True)


class LanguageSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


class ProviderStatusSerializer(serializers.Serializer):
    slug = serializers.CharField()
    name = serializers.CharField()
    group = serializers.CharField()
    # How the status was determined: 'query' (observed from real searches) or
    # 'probe' (active health check).
    monitor = serializers.CharField()
    # 'up', 'down' or 'unknown' (nothing observed yet).
    state = serializers.CharField()
    checked_at = serializers.DateTimeField(default=None)
    last_ok_at = serializers.DateTimeField(default=None)
    last_error = serializers.CharField(default='', allow_blank=True)
    source = serializers.CharField(default='', allow_blank=True)


class ApiKeySerializer(serializers.Serializer):
    """Read-only view of an API key (never includes the secret)."""

    name = serializers.CharField()
    prefix = serializers.CharField(source='display_prefix')
    created_at = serializers.DateTimeField()
    last_used_at = serializers.DateTimeField(default=None)
    revoked = serializers.BooleanField()
