from django.urls import path

from . import views

app_name = 'instant'

urlpatterns = [
    path('qr.svg', views.qr_svg, name='qr_svg'),
    path('hash', views.hashes, name='hashes'),
]
