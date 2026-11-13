from fastapi import HTTPException

from app.brokers import mcp, t_invest


SCHEMAS = {
    "invest_create_order": "fe695ccfcf625f5f116266bb76bb9165e6300e9a7ff898f90fdafdee82014fdd",
    "invest_get_order_state": "c5b9792241fce60b23521c2ec13b195de63a7fa1a880bfaad47a283c56310bd8",
}


class SandboxOrderSession(mcp.MCPSession):
    def __init__(self, connection):
        if connection.environment != "sandbox":
            raise HTTPException(422, "Заявки через MCP доступны только в Sandbox")
        super().__init__(t_invest.decrypt_token(connection), "sandbox")

    async def checked_tool(self, name):
        catalog = await self.tools()
        if not any(item["name"] == name and item["schema_sha256"] == SCHEMAS[name] for item in catalog):
            raise HTTPException(422, "Схема торгового инструмента MCP недоступна или изменилась")

    async def order_call(self, name, arguments):
        result = await self._send("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise HTTPException(502, "MCP не вернул подтверждённый статус заявки")
        value = self.result_value(result)
        return value if isinstance(value, dict) else {}

    async def submit(self, account, instrument, side, lots):
        name = "invest_create_order"
        await self.checked_tool(name)
        return await self.order_call(name, {
            "accountId": account, "instrumentId": instrument, "quantity": lots,
            "direction": f"ORDER_DIRECTION_{side.upper()}", "orderType": "ORDER_TYPE_MARKET",
            "confirmMarginTrade": False,
        })

    async def status(self, account, exchange_id):
        name = "invest_get_order_state"
        await self.checked_tool(name)
        return await self.order_call(name, {"accountId": account, "orderId": exchange_id})
