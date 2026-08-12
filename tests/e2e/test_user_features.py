"""P1: приватные функции — watchlist, портфель, алерты, история + обратная связь, paper."""

import uuid

import pytest
from conftest import register

pytestmark = pytest.mark.e2e


def _new_user(page, server) -> str:
    name = "u_" + uuid.uuid4().hex[:8]
    register(page, server, name, "secret123")
    return name


def test_watchlist_add_and_remove(page, server):
    _new_user(page, server)
    page.goto(server + "/watchlist")
    assert "Список пуст" in page.text_content("body")

    page.fill('form[action="/api-watchlist-add"] input[name="ticker"]', "AFLT")
    with page.expect_navigation():
        page.click('form[action="/api-watchlist-add"] button[type="submit"]')
    body = page.text_content("body")
    assert "AFLT" in body
    assert "Список пуст" not in body

    with page.expect_navigation():
        page.click('form[action="/api-watchlist-remove"] button[type="submit"]')
    assert "Список пуст" in page.text_content("body")


def test_portfolio_add_and_close(page, server):
    _new_user(page, server)
    page.goto(server + "/portfolio")
    assert "Позиций нет" in page.text_content("body")

    add = page.locator('form[action="/api-portfolio-add"]')
    add.locator('input[name="ticker"]').fill("SBER")
    add.locator('input[name="quantity"]').fill("10")
    add.locator('input[name="avg_price"]').fill("100")
    with page.expect_navigation():
        add.locator('button[type="submit"]').click()
    assert "SBER" in page.text_content("body")

    # закрытие позиции: открываем модалку и выбираем оценку
    page.locator('button[onclick^="openCloseModal"]').click()
    with page.expect_navigation():
        page.locator('#close-modal button[name="rating"][value="neutral"]').click()
    assert "SBER" not in page.text_content("body")


def test_alerts_settings_save(page, server):
    _new_user(page, server)
    page.goto(server + "/alerts")
    form = page.locator('form[action="/api-alerts-settings"]')
    form.locator('input[name="min_impact"]').fill("0.6")
    with page.expect_navigation():
        form.locator('button[type="submit"]').click()
    assert (
        page.locator('form[action="/api-alerts-settings"] input[name="min_impact"]')
        .input_value()
        == "0.6"
    )


def test_history_feedback(page, server):
    _new_user(page, server)
    page.goto(server + "/history")
    assert "SBER" in page.text_content("body")

    with page.expect_navigation():
        page.locator('button[name="rating"][value="worked"]').first.click()
    assert page.locator('button[name="rating"][value="worked"].active').count() >= 1


def test_paper_page_and_reset(page, server):
    _new_user(page, server)
    page.goto(server + "/paper")
    assert "Виртуальный портфель" in page.text_content("body")
    with page.expect_navigation():
        page.click('form[action="/api-paper-reset"] button[type="submit"]')
    assert page.url == server + "/paper"
