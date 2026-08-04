from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

DB_DOWN_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>База данных недоступна</title>
<style>
body{margin:0;background:#0f1420;color:#e6ebf5;font-family:"Segoe UI",system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#1a2233;border:1px solid #2b3750;border-radius:12px;padding:24px 32px;max-width:520px;text-align:center}
.badge{display:inline-block;padding:4px 12px;border-radius:10px;background:rgba(229,72,77,.15);color:#e5484d;font-weight:700;font-size:12px;margin-bottom:12px}
h1{font-size:20px;margin:0 0 8px}
p{color:#8b97ad;font-size:14px;line-height:1.5;margin:0}
</style>
</head>
<body>
<div class="card">
  <span class="badge">Критичная ошибка</span>
  <h1>База данных недоступна</h1>
  <p>Сервис не может получить доступ к базе данных. Повторите попытку позже или проверьте, что PostgreSQL запущен.</p>
</div>
</body>
</html>"""


def is_db_error(exc: Exception) -> bool:
    if isinstance(exc, (OperationalError, InterfaceError, DBAPIError, ConnectionError)):
        return True
    for cls in type(exc).__mro__:
        if cls.__module__.startswith("asyncpg"):
            return True
    return False


class DatabaseGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        try:
            return await call_next(request)
        except Exception as exc:
            if is_db_error(exc):
                return HTMLResponse(DB_DOWN_PAGE, status_code=503)
            raise
