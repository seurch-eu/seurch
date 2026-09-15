from django.urls import path, re_path

from . import views

app_name = 'search'

urlpatterns = [
    path('', views.index, name='index'),
    path('status/', views.provider_status, name='provider_status'),
    path('status/health', views.status_monitor, name='status_monitor'),
    path('status/health/', views.status_monitor),
    path(
        'status/health/<slug:provider>', views.provider_status_monitor,
        name='provider_status_monitor',
    ),
    path('status/health/<slug:provider>/', views.provider_status_monitor),
    path('about/', views.about, name='about'),
    path('api/', views.api, name='api'),
    path('search/', views.results, name='results'),
    path('search/cards/', views.web_cards, name='cards'),
    path('suggest/', views.suggest, name='suggest'),
    path('search/image-similar/', views.image_similar, name='image_similar'),
    path('search/image/', views.image_detail, name='image_detail'),
    path('opensearch-suggest/', views.opensearch_suggest, name='opensearch_suggest'),
    path('opensearch.xml', views.opensearch_xml, name='opensearch_xml'),
    path('settings/', views.settings_view, name='settings'),
    re_path(
        r'^settings/(?P<pane>' + '|'.join(views._PANE_SLUGS.values()) + r')/$',
        views.settings_view, name='settings_pane',
    ),
    path('settings/theme', views.settings_theme, name='settings_theme'),
    path('settings/export.json', views.settings_export, name='settings_export'),
    path('settings/import', views.settings_import, name='settings_import'),
    path('settings/block-site/', views.block_site, name='block_site'),
    path('settings/custom-bangs.json', views.custom_bangs_json, name='custom_bangs_json'),
    path('image-proxy/', views.image_proxy, name='image_proxy'),
]
