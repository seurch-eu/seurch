from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, PasswordChangeView, PasswordResetView
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from . import ratelimit
from .models import SESSION_LINK_SESSION_KEY, SessionLink


class ChangePasswordView(PasswordChangeView):
    template_name = 'accounts/password_change.html'
    success_url = reverse_lazy('search:settings')

    def form_valid(self, form):
        messages.success(self.request, 'Password changed successfully.')
        return super().form_valid(form)


class ThrottledLoginView(LoginView):
    """``LoginView`` with a cache-backed lockout against credential guessing.

    Failures are counted per username (stops a distributed attack on one
    account) and per client IP with ten times the headroom (stops one machine
    spraying many accounts without instantly locking out a whole shared/NAT
    IP). Once either counter reaches its limit, every attempt, right password
    or not, gets the same "too many attempts" error until the window
    (``LOGIN_THROTTLE_WINDOW``) expires, so a locked account leaks nothing
    about which guess was correct. Counters live in the shared cache, one
    budget across all gunicorn workers.
    """

    def _username(self):
        return (self.request.POST.get('username') or '').strip().lower()[:150]

    def _locked_out(self):
        limit = settings.LOGIN_THROTTLE_LIMIT
        return (
            ratelimit.is_limited('login-user', self._username(), limit)
            or ratelimit.is_limited('login-ip', ratelimit.client_ip(self.request), limit * 10)
        )

    def _reject(self, form):
        form.add_error(None, _('Too many failed sign-in attempts. Please try again later.'))
        return super().form_invalid(form)

    def form_valid(self, form):
        if self._locked_out():
            return self._reject(form)
        # A successful sign-in forgives the account's earlier failures; the
        # IP counter keeps running so spraying can't reset itself this way.
        ratelimit.clear('login-user', self._username())
        return super().form_valid(form)

    def form_invalid(self, form):
        if self._locked_out():
            return self._reject(form)
        window = settings.LOGIN_THROTTLE_WINDOW
        if self._username():
            ratelimit.hit('login-user', self._username(), window)
        ratelimit.hit('login-ip', ratelimit.client_ip(self.request), window)
        return super().form_invalid(form)


class ThrottledPasswordResetView(PasswordResetView):
    """``PasswordResetView`` capped per client IP.

    Over-budget requests skip sending the email but return the very same
    redirect as a sent one: revealing the throttle would hand an attacker an
    oracle, and the reset flow deliberately never discloses whether anything
    was sent anyway."""

    def form_valid(self, form):
        over = ratelimit.hit(
            'password-reset', ratelimit.client_ip(self.request),
            settings.PASSWORD_RESET_THROTTLE_WINDOW,
        ) > settings.PASSWORD_RESET_THROTTLE_LIMIT
        if over:
            return redirect(self.get_success_url())
        return super().form_valid(form)


def private_session_login(request, token):
    """Log in via a Session Link (see ``accounts.models.SessionLink``).

    Lets a signed-in user open a private/incognito window - which carries no
    cookies from their normal session - and land there already signed in,
    without retyping credentials. An invalid, stale (regenerated-away), or
    unknown token just bounces to the login page like any unauthenticated
    visit; it never reveals whether the token used to be valid.

    A ``q`` parameter is forwarded to the results page after login, so the
    URL doubles as a browser custom-search-engine target (``…/?q=%s``): typed
    straight into a private window's address bar, it signs the user in and
    runs the search in one request, with no login form ever shown.
    """
    link = SessionLink.authenticate(token)
    if link is None:
        return redirect('login')
    login(request, link.user)
    request.session[SESSION_LINK_SESSION_KEY] = True
    link.touch_last_used()
    if not request.GET.get('q', '').strip():
        return redirect('search:index')
    return redirect(f"{reverse('search:results')}?{request.GET.urlencode()}")
