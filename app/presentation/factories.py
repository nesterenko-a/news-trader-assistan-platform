import html

from app.presentation.view import StrategyView


class TelegramMessageFactory:
    def build(self, view: StrategyView) -> str:
        e = html.escape
        lines = [
            f"<b>{e(view.ticker)} — {e(view.name)}</b>",
            f"<i>Сектор: {e(view.sector)} · Рынок: {e(view.market)}</i>",
            "",
            f"<b>Вердикт: {e(view.verdict)}</b>",
            f"Горизонт: {e(view.horizon)} · Уверенность: {view.confidence} · Net score: {view.net_score}",
        ]
        if view.levels.entry is not None:
            lines.append(
                f"Вход: {view.levels.entry} · Take-profit: {view.levels.take_profit} · "
                f"Stop-loss: {view.levels.stop_loss}"
            )
        if view.rationale:
            lines.append("")
            lines.append(f"Обоснование: {e(view.rationale)}")
        if view.signals:
            lines.append("")
            lines.append("Сигналы:")
            for signal in view.signals[:5]:
                lines.append(
                    f"• [{e(signal.kind_label)}] {e(signal.entity)} "
                    f"({e(signal.sentiment)}): {signal.weight_str}"
                )
        if view.news:
            lines.append("")
            lines.append("Новости:")
            for item in view.news[:5]:
                source = f" — {e(item.source_name)}, {e(item.date_str)}" if item.source_name else ""
                lines.append(f"• <a href=\"{e(item.url)}\">{e(item.title)}</a>{source}")
        lines.append("")
        lines.append(
            f"Подробнее: <a href=\"{e(view.web_url)}\">{e(view.ticker)} — аналитика</a>"
        )
        return "\n".join(lines)


class WebContextFactory:
    def build(self, view: StrategyView) -> dict:
        return {"view": view}
