"""P1: контроль доступа — редиректы без авторизации, доступ к /admin."""

import pytest
from conftest import login

pytestmark = pytest.mark.e2e

PROTECTED_PATHS = [
    "/watchlist",
    "/portfolio",
    "/alerts",
    "/history",
    "/paper",
    "/news",
    "/admin",
]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_protected_page_redirects_anonymous(page, server, path):
    page.goto(server + path)
    assert page.url.startswith(server + "/login")


def test_admin_page_forbidden_for_regular_user(page, server):
    login(page, server, "user", "user123")
    response = page.goto(server + "/admin")
    assert response.status == 403
    assert page.url == server + "/admin"


def test_admin_page_accessible_for_admin(page, server):
    login(page, server, "admin", "admin123")
    page.goto(server + "/admin")
    assert "Администрирование" in page.text_content("body")
    # форма запуска скриптов присутствует
    assert page.locator('form[action="/admin/scripts/run"]').count() >= 1
