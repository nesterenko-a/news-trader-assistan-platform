"""P0: авторизация — формы /login и /register, полный цикл регистрация → вход → выход."""

import uuid

import pytest

pytestmark = pytest.mark.e2e

USERNAME = "user"
PASSWORD = "user123"


def _has_nt_token(page) -> bool:
    return any(c["name"] == "nt_token" for c in page.context.cookies())


def test_login_invalid_credentials(page, server):
    page.goto(server + "/login")
    page.fill('input[name="username"]', "nosuchuser")
    page.fill('input[name="password"]', "wrongpass")
    page.click('button[type="submit"]')
    page.wait_for_selector("p.error")
    assert "Неверное имя пользователя или пароль" in page.text_content("p.error")
    assert not _has_nt_token(page)


def test_login_valid_user(page, server):
    page.goto(server + "/login")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_url(server + "/")
    assert _has_nt_token(page)


def test_register_short_values_blocked(page, server):
    page.goto(server + "/register")
    page.fill('input[name="username"]', "ab")
    page.fill('input[name="password"]', "123")
    page.click('button[type="submit"]')
    # HTML5-валидация (minlength 3/6) блокирует отправку формы
    assert page.url == server + "/register"
    assert not _has_nt_token(page)


def test_register_duplicate_user(page, server):
    page.goto(server + "/register")
    page.fill('input[name="username"]', USERNAME)
    page.fill('input[name="password"]', PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_selector("p.error")
    assert "Пользователь уже существует" in page.text_content("p.error")


def test_register_login_logout_cycle(page, server):
    name = "user_" + uuid.uuid4().hex[:8]
    page.goto(server + "/register")
    page.fill('input[name="username"]', name)
    page.fill('input[name="password"]', "secret123")
    page.click('button[type="submit"]')
    page.wait_for_url(server + "/")
    assert _has_nt_token(page)

    # приватная страница доступна после регистрации
    page.goto(server + "/watchlist")
    assert "Watchlist" in page.text_content("h1")

    # выход удаляет сессию
    page.goto(server + "/logout")
    assert not _has_nt_token(page)

    # после выхода приватная страница снова требует авторизации
    page.goto(server + "/watchlist")
    assert page.url.startswith(server + "/login")
