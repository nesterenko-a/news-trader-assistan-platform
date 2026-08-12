"""P1: news manager (CRUD, toggles, «Вернуть стандартные ленты») и админ-запуск скрипта с деталями."""

import pytest
from conftest import login

pytestmark = pytest.mark.e2e

FEED_NAME = "E2E Test Feed"
FEED_URL = "https://example.com/rss"


def test_news_add_invalid_url_error(page, server):
    login(page, server, "user", "user123")
    page.goto(server + "/news")
    add = page.locator('form[action="/news/rss/add"]')
    add.locator('input[name="name"]').fill("Bad Feed")
    add.locator('input[name="url"]').fill("ftp://example.com/rss")
    with page.expect_navigation():
        add.locator('button[type="submit"]').click()
    assert "Допустимы только http/https" in page.text_content("body")


def test_news_add_toggle_remove(page, server):
    login(page, server, "user", "user123")
    page.goto(server + "/news")
    add = page.locator('form[action="/news/rss/add"]')
    add.locator('input[name="name"]').fill(FEED_NAME)
    add.locator('input[name="url"]').fill(FEED_URL)
    with page.expect_navigation():
        add.locator('button[type="submit"]').click()
    row = page.locator(f"tr:has-text('{FEED_NAME}')")
    assert row.count() == 1

    # toggle «LLM-разбор» (асинхронный POST без перезагрузки)
    checkbox = row.locator('input[data-field="use_llm"]')
    with page.expect_response("**/news/rss/toggle"):
        checkbox.check()
    page.reload()
    assert row.locator('input[data-field="use_llm"]').is_checked()

    # удаление ленты
    with page.expect_navigation():
        row.locator('button.feed-remove').click()
    assert page.locator(f"tr:has-text('{FEED_NAME}')").count() == 0


def test_news_restore_defaults(page, server):
    login(page, server, "user", "user123")
    page.goto(server + "/news")
    with page.expect_navigation():
        page.click('form[action="/news/rss/restore"] button[type="submit"]')
    assert page.locator("table.table tbody tr").count() >= 1


def test_admin_run_script_and_detail(page, server):
    login(page, server, "admin", "admin123")
    page.goto(server + "/admin")
    form = page.locator(
        'form[action="/admin/scripts/run"]:has(input[name="script"][value="seed_db"])'
    )
    assert form.count() == 1
    with page.expect_navigation():
        form.locator('button[type="submit"]').click()
    assert "Наполнить справочники" in page.text_content("body")
    assert page.locator("#run-live").count() == 1
    # Завершения скрипта не ждём: на SQLite-стенде фоновый запуск скрипта с
    # create_all (DDL) зависает из-за блокировки файла БД uvicorn-процессом
    # (см. реестр расхождений в docs/21-web-e2e-tests.md); teardown сессии
    # убивает дерево процессов (taskkill /T).
