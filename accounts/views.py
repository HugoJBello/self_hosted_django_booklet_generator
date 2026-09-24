from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .forms import AdminSetPasswordForm, LoginForm, StyledPasswordChangeForm, UserCreateForm

User = get_user_model()


def admin_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not request.user.is_active or not request.user.is_staff:
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapped


def home(request):
    return render(request, "accounts/home.html")


@login_not_required
def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")
    form = LoginForm(request=request, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        next_url = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            next_url = reverse("accounts:home")
        return redirect(next_url)
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


def logout_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    logout(request)
    return redirect("accounts:login")


def change_password(request):
    form = StyledPasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Your password has been updated.")
        return redirect("accounts:password_change")
    return render(request, "accounts/password_form.html", {"form": form, "managed_user": request.user, "own_password": True})


@admin_required
def user_list(request):
    users = User.objects.order_by("username")
    return render(request, "accounts/user_list.html", {"users": users})


@admin_required
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"User {user.username} created.")
        return redirect("accounts:user_list")
    return render(request, "accounts/user_form.html", {"form": form})


@admin_required
def user_password(request, user_id):
    managed_user = get_object_or_404(User, pk=user_id)
    form = AdminSetPasswordForm(managed_user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        if managed_user.pk == request.user.pk:
            update_session_auth_hash(request, managed_user)
        messages.success(request, f"Password for {managed_user.username} updated.")
        return redirect("accounts:user_list")
    return render(request, "accounts/password_form.html", {"form": form, "managed_user": managed_user, "own_password": False})


@admin_required
def user_delete(request, user_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    managed_user = get_object_or_404(User, pk=user_id)
    if managed_user.pk == request.user.pk:
        messages.error(request, "You cannot delete your own account.")
    elif managed_user.is_staff and User.objects.filter(is_staff=True, is_active=True).count() <= 1:
        messages.error(request, "The last active administrator cannot be deleted.")
    else:
        username = managed_user.username
        managed_user.delete()
        messages.success(request, f"User {username} deleted.")
    return redirect("accounts:user_list")
