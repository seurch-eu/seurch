from django.conf import settings

from . import preferences
from .models import CustomBang


def theme(request):
    return {'theme_pref': preferences.load(request).get('theme', 'system')}


def custom_bangs(request):
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'user_bangs': {}}
    pairs = CustomBang.objects.filter(user=user).values_list('trigger', 'url_template')
    return {'user_bangs': dict(pairs)}


def footer_links(request):
    return {'footer_links': settings.FOOTER_LINKS.items()}


def feature_flags(request):
    return {'status_page_enabled': settings.STATUS_PAGE_ENABLED}


def version(request):
    return {
        'git_ref': settings.GIT_REF,
        'git_sha': settings.GIT_SHA,
        'source_url': settings.SOURCE_URL,
    }
