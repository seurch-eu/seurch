from django.contrib.auth import views as auth_views
from django.urls import path

from accounts.views import (
    ChangePasswordView,
    ThrottledLoginView,
    ThrottledPasswordResetView,
    private_session_login,
)

urlpatterns = [
    path('login/', ThrottledLoginView.as_view(template_name='accounts/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='/login/'), name='logout'),

    path('accounts/password-change/', ChangePasswordView.as_view(), name='password_change'),

    path('private/<str:token>/', private_session_login, name='private_session_login'),

    path(
        'password-reset/',
        ThrottledPasswordResetView.as_view(
            template_name='accounts/password_reset.html',
            email_template_name='accounts/password_reset_email.txt',
            html_email_template_name='accounts/password_reset_email.html',
            subject_template_name='accounts/password_reset_subject.txt',
            success_url='/password-reset/done/',
        ),
        name='password_reset',
    ),
    path(
        'password-reset/done/',
        auth_views.PasswordResetDoneView.as_view(
            template_name='accounts/password_reset_done.html',
        ),
        name='password_reset_done',
    ),
    path(
        'password-reset/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='accounts/password_reset_confirm.html',
            success_url='/password-reset/complete/',
        ),
        name='password_reset_confirm',
    ),
    path(
        'password-reset/complete/',
        auth_views.PasswordResetCompleteView.as_view(
            template_name='accounts/password_reset_complete.html',
        ),
        name='password_reset_complete',
    ),
]
