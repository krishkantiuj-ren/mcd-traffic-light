"""麦当劳 MCP 客户端 —— JSON-RPC over Streamable HTTP，仅用 Python 标准库。

设计要点：
  * 零第三方依赖，评审 clone 下来即可运行；
  * Token 只从环境变量 MCD_MCP_TOKEN 或本机 ~/.workbuddy/mcp.json 读取，
    绝不写入项目文件、日志或 Git 提交；
  * 429（限流 600 次/分钟）自动退避重试；
  * 任何取数失败都不抛到上层，由调用方降级到本地快照。

参考实现来源：本项目开发期使用的探针脚本 tools/mcp-probe.py。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

BASE_URL = "https://mcp.mcd.cn"
MCP_CONFIG = os.path.join(os.path.expanduser("~"), ".workbuddy", "mcp.json")
PROTOCOL_VERSION = "2025-03-26"


class McpError(RuntimeError):
    """MCP 调用失败。"""


def load_token() -> str:
    """按优先级取 Token：环境变量 -> 本机 MCP 配置。"""
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    if token:
        return token.replace("Bearer ", "").strip()
    try:
        with open(MCP_CONFIG, encoding="utf-8") as fh:
            cfg = json.load(fh)
        auth = cfg["mcpServers"]["mcd-mcp"]["headers"]["Authorization"]
        return auth.split(" ", 1)[1].strip()
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        raise McpError(
            "未找到麦当劳 MCP Token：请设置环境变量 MCD_MCP_TOKEN，"
            "或在 ~/.workbuddy/mcp.json 中配置 mcd-mcp"
        ) from exc


def text_of(resp: dict) -> str:
    """把 tools/call 的返回拼成纯文本。"""
    out = []
    for item in resp.get("result", {}).get("content", []):
        if item.get("type") == "text":
            out.append(item.get("text", ""))
    return "".join(out)


def extract_json(text: str) -> dict:
    """MCP 返回里业务 JSON 混在提示文案中间，取最后一个 success 对象。"""
    match = re.search(r'\{\s*"success"\s*:\s*true.*\}\s*$', text, re.S)
    if not match:
        match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise McpError("响应中未找到 JSON 主体")
    return json.loads(match.group(0))


def parse_nutrition_table(text: str) -> list[dict]:
    """解析 list-nutrition-foods。

    坑：营养表不是 JSON，而是嵌在外层 JSON 的 data 字段里的一长串文本，
    每行 9 个逗号分隔字段：名称,描述,kJ,kcal,蛋白质,脂肪,碳水,钠,钙。
    """
    inner = extract_json(text).get("data", "")
    rows = []
    for line in inner.splitlines():
        line = line.strip()
        if not line or line.startswith(("[", "{")):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 9:
            continue
        try:
            nums = [float(x) if x not in ("null", "") else 0.0 for x in parts[2:]]
        except ValueError:
            continue
        rows.append(
            {
                "name": parts[0],
                "description": None if parts[1] == "null" else parts[1],
                "energyKj": nums[0],
                "energyKcal": nums[1],
                "protein": nums[2],
                "fat": nums[3],
                "carbohydrate": nums[4],
                "sodium": nums[5],
                "calcium": nums[6],
            }
        )
    return rows


class McpClient:
    """麦当劳 MCP 的最小客户端。"""

    def __init__(self, token: str | None = None, base: str = BASE_URL, timeout: int = 25):
        self.token = token or load_token()
        self.base = base
        self.timeout = timeout
        self._id = 0
        self._cache: dict[str, object] = {}

    # ---------- 底层 RPC ----------

    def _rpc(self, method: str, params: dict | None = None, retries: int = 2) -> dict:
        self._id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            self.base,
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                return self._parse_payload(raw)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code == 429 and attempt < retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise McpError(f"HTTP {exc.code}: {exc.reason}") from exc
            except Exception as exc:  # noqa: BLE001 - 网络抖动重试
                last_exc = exc
                if attempt < retries:
                    time.sleep(1.0)
                    continue
        raise McpError(f"MCP 调用失败: {last_exc}")

    @staticmethod
    def _parse_payload(raw: str) -> dict:
        """响应可能是 SSE（每行 data: {...}），先剥前缀再取最后一条 JSON。"""
        lines = [ln[6:].strip() if ln.startswith("data: ") else ln.strip() for ln in raw.splitlines()]
        candidates = [ln for ln in lines if ln.startswith("{")]
        if not candidates:
            raise McpError("响应为空")
        return json.loads(candidates[-1])

    # ---------- 业务封装 ----------

    def call(self, tool: str, args: dict | None = None) -> str:
        return text_of(self._rpc("tools/call", {"name": tool, "arguments": args or {}}))

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list", {}).get("result", {}).get("tools", [])

    def fetch_nutrition(self) -> dict[str, dict]:
        """158 款在售餐品的真实营养值，键为餐品名。"""
        if "nutrition" not in self._cache:
            rows = parse_nutrition_table(self.call("list-nutrition-foods"))
            self._cache["nutrition"] = {r["name"]: r for r in rows}
        return self._cache["nutrition"]  # type: ignore[return-value]

    def fetch_menu(self, store_code: str, be_type: int = 1, order_type: int = 1) -> dict:
        """门店菜单：分类 + 餐品（含官方商品图、现价、原价）。"""
        key = f"menu:{store_code}:{be_type}:{order_type}"
        if key not in self._cache:
            payload = extract_json(
                self.call(
                    "query-meals",
                    {"beType": be_type, "orderType": order_type, "storeCode": store_code},
                )
            ).get("data", {})
            self._cache[key] = {
                "storeCode": store_code,
                "beType": be_type,
                "orderType": order_type,
                "categories": [
                    {
                        "name": (c.get("name") or "").replace("\n", "/"),
                        "codes": [m.get("code") for m in c.get("meals", [])],
                    }
                    for c in payload.get("categories", [])
                ],
                "meals": payload.get("meals", {}),
            }
        return self._cache[key]  # type: ignore[return-value]

    def nearby_stores(self, city: str, keyword: str, be_type: int = 1) -> list[dict]:
        payload = extract_json(
            self.call(
                "query-nearby-stores",
                {"beType": be_type, "searchType": 2, "city": city, "keyword": keyword},
            )
        )
        data = payload.get("data") or []
        return data if isinstance(data, list) else data.get("stores", [])

    def my_coupons(self) -> list[dict]:
        """卡包中的券。失败返回空列表（券是增强项，不该阻断主流程）。"""
        try:
            payload = extract_json(self.call("query-my-coupons", {"page": "1", "pageSize": "200"}))
            data = payload.get("data") or {}
            coupons = data.get("coupons") or data.get("list") or []
            return [c for c in coupons if isinstance(c, dict)]
        except Exception:  # noqa: BLE001
            return []

    def available_coupons(self) -> list[dict]:
        try:
            payload = extract_json(self.call("available-coupons"))
            data = payload.get("data") or {}
            coupons = data.get("coupons") or data.get("list") or []
            return [c for c in coupons if isinstance(c, dict)]
        except Exception:  # noqa: BLE001
            return []

    def price_of(self, store_code: str, items: list[dict], be_type: int = 1, order_type: int = 1):
        """真实含优惠价格。失败返回 None，由本地价目降级。"""
        try:
            payload = extract_json(
                self.call(
                    "calculate-price",
                    {
                        "storeCode": store_code,
                        "beType": be_type,
                        "orderType": order_type,
                        "items": items,
                        "needTableware": False,
                    },
                )
            )
            return payload.get("data")
        except Exception:  # noqa: BLE001
            return None


if __name__ == "__main__":  # 手工自检：python src/mcp_client.py
    client = McpClient()
    tools = client.list_tools()
    print(f"工具数量: {len(tools)}")
    nutrition = client.fetch_nutrition()
    print(f"营养条目: {len(nutrition)}")
    menu = client.fetch_menu("1450713")
    print(f"分类 {len(menu['categories'])} 个，餐品 {len(menu['meals'])} 款")
