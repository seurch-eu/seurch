from django.urls import path

from . import views

app_name = 'api'

urlpatterns = [
    path('', views.ApiRootView.as_view(), name='root'),
    path('web/', views.WebSearchView.as_view(), name='web'),
    path('images/', views.ImageSearchView.as_view(), name='images'),
    path('images/similar/', views.SimilarImageSearchView.as_view(), name='images-similar'),
    path('news/', views.NewsSearchView.as_view(), name='news'),
    path('videos/', views.VideoSearchView.as_view(), name='videos'),
    path('maps/', views.MapsView.as_view(), name='maps'),
    path('translate/', views.TranslateView.as_view(), name='translate'),
    path('translate/languages/', views.LanguagesView.as_view(), name='translate-languages'),
    path('instant/', views.InstantView.as_view(), name='instant'),
    path('cards/', views.CardsView.as_view(), name='cards'),
    path('suggest/', views.SuggestView.as_view(), name='suggest'),
    path('status/', views.StatusView.as_view(), name='status'),
    path('key/', views.KeyInfoView.as_view(), name='key'),
]
