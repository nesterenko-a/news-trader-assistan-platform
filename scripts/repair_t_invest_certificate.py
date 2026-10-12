import sys

from app.brokers.certificates import repair_and_check
from app.brokers.t_invest import BASE


def main() -> int:
    try:
        path = repair_and_check(BASE)
    except Exception as exc:
        print(f"Проверка сертификата Т-Инвестиций не удалась: {exc}", file=sys.stderr)
        return 1
    print(f"Сертификат Минцифры добавлен для приложения: {path}")
    print("Проверка TLS Т-Инвестиций пройдена; API ответил на запрос без токена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
