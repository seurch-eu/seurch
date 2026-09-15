from django.conf import settings
from django.urls import include, path

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
]

if settings.PUBLIC_API_ENABLED:
    urlpatterns.append(path('api/v1/', include('api.urls')))

urlpatterns += [
    path('', include('accounts.urls')),
    path('instant/', include('instant.urls')),
    path('', include('search.urls')),
]
