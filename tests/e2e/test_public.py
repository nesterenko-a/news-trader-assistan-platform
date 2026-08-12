"""P0: публичные страницы — главная (поиск/фильтры), карточка бумаги, macro, indicators."""

import pytest

pytestmark = pytest.mark.e2e


def _rows(page) -> str:
    return page.locator("table.table tbody").text_content()


def test_index_renders_and_search(page, server):
    page.goto(server + "/")
    assert "Аналитика на основе новостного фона" in page.text_content("h1")
    page.fill('input[name="ticker"]', "AFLT")
    page.click('form.search button[type="submit"]')
    page.wait_for_url("**/securities/AFLT")
    body = page.text_content("body")
    assert "AFLT" in body


def test_index_sector_filter(page, server):
    page.goto(server + "/")
    page.select_option('select[name="sector"]', "Авиаперевозки")
    page.wait_for_url("**sector=*")
    rows = _rows(page)
    assert "AFLT" in rows
    assert "LKOH" not in rows


def test_index_type_filter(page, server):
    page.goto(server + "/")
    # дефолт — "all", поэтому сначала переключаем на "stocks" (триггерит submit);
    # radio скрыты CSS — кликаем по label
    page.locator('label:has(input[name="type"][value="stocks"])').click()
    page.wait_for_url("**type=stocks*")
    page.locator('label:has(input[name="type"][value="all"])').click()
    page.wait_for_url("**type=all*")
    rows = _rows(page)
    assert "AFLT" in rows
    assert "LKOH" in rows


def test_security_card_chart_ranges(page, server):
    page.goto(server + "/securities/AFLT")
    assert "История цены" in page.text_content("h2")
    # сидинг содержит свечи — график рендерится, а не «Нет исторических данных»
    assert "Нет исторических данных" not in page.text_content("body")
    assert page.locator("svg.chart polyline").count() >= 1
    # на карточке есть и Volume Profile (данные свечей оживили блок)
    assert "Профиль объёма (Volume Profile)" in page.text_content("body")
    # диапазоны цены — в первом блоке .chart-range (второй — периоды VP)
    range_links = page.locator(".chart-range").first.locator("a")
    assert [
        range_links.nth(i).inner_text() for i in range(range_links.count())
    ] == ["1 день", "7 дней", "1 год", "5 лет", "Всё"]
    page.locator(".chart-range").first.locator("a:has-text('1 год')").click()
    page.wait_for_url("**/securities/AFLT?range=1y*")
    assert (
        page.locator(".chart-range").first.locator("a.active").inner_text() == "1 год"
    )


def test_security_card_shows_news(page, server):
    # сидинг добавляет статью, связанную с сущностью Сбербанка
    page.goto(server + "/securities/SBER")
    assert "Новости (1)" in page.text_content("body")
    assert "Сбербанк отчитался о росте прибыли" in page.text_content("body")
    assert page.locator(".news-item").count() == 1


def test_security_card_screenshot_button(page, server):
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(server + "/securities/AFLT")
    button = page.get_by_role("button", name="Сделать скриншот")
    assert button.is_visible()
    button.click()
    page.wait_for_timeout(500)
    assert errors == []


def test_macro_page_renders(page, server):
    page.goto(server + "/macro")
    assert "Макрокалендарь" in page.text_content("h1")
    assert page.locator('select[data-filter="region"]').count() == 1
    assert page.locator('select[data-filter="impact"]').count() == 1
    assert page.locator('select[data-filter="scope"]').count() == 1
    visible = page.locator("#macro-table tbody tr:visible")
    assert visible.count() == 3
    # фильтрация по региону (клиентский JS)
    page.select_option('select[data-filter="region"]', "RU")
    assert visible.count() == 2
    # комбинация с фильтром значимости
    page.select_option('select[data-filter="impact"]', "high")
    assert visible.count() == 1


@pytest.mark.parametrize(
    "name,label",
    [
        ("ema", "EMA (скользящие средние)"),
        ("macd", "MACD"),
        ("oi", "Открытый интерес (OI)"),
        ("volume_profile", "Профиль объёма (Volume Profile)"),
        ("support_resistance", "Поддержка/сопротивление"),
    ],
)
def test_indicators_tabs_switch(page, server, name, label):
    page.goto(server + "/indicators")
    page.click(f'.seg-item.seg-link:has-text("{label}")')
    page.wait_for_url(f"**/indicators?name={name}*")
    assert label in page.locator(".seg-item.seg-active").inner_text()
    assert page.locator('button[type="submit"]').count() >= 1
