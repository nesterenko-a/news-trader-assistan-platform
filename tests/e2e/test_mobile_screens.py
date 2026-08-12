"""P2: мобильное бургер-меню и рендер ключевых страниц без JS-ошибок."""

import pytest
from conftest import login

pytestmark = pytest.mark.e2e


def _menu_open(page) -> bool:
    cls = page.locator("#user-menu").get_attribute("class") or ""
    return "open" in cls


def test_mobile_menu_opens_and_navigates(page, server):
    login(page, server, "user", "user123")
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(server + "/")
    toggle = page.locator(".menu-toggle")
    assert toggle.is_visible()
    assert not _menu_open(page)

    toggle.click()
    assert _menu_open(page)
    # пункт меню кликабелен
    with page.expect_navigation():
        page.locator('#user-menu a[href="/watchlist"]').click()
    assert page.url == server + "/watchlist"
    assert "Watchlist" in page.text_content("h1")


def test_mobile_menu_closes_on_escape(page, server):
    login(page, server, "user", "user123")
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(server + "/")
    toggle = page.locator(".menu-toggle")
    toggle.click()
    assert _menu_open(page)
    # закрытие — клавишей Escape (см. реестр расхождений док.21: повторный
    # клик по toggle меню не закрывает; закрытие — клик вне меню или Escape)
    page.keyboard.press("Escape")
    assert not _menu_open(page)


@pytest.mark.parametrize(
    "path",
    ["/", "/securities/AFLT", "/macro", "/indicators", "/login", "/register"],
)
def test_key_pages_render_without_console_errors(page, server, path):
    errors: list[str] = []
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
    page.goto(server + path)
    page.wait_for_load_state("domcontentloaded")
    assert errors == []
