"""本地服务器 —— 零依赖 HTTP 服务，同时提供 Web 界面与 JSON API。

启动：
    python src/server.py [端口]        # 默认 8000
浏览器打开 http://127.0.0.1:8000

设计说明：
  * 取数优先走麦当劳官方 MCP（实时），失败自动降级到 data/ 快照，界面会显示"离线数据"角标；
  * 计算全部由 nutrition.py 完成，前端只做展示，不做任何营养学判断；
  * 不写入任何用户凭证，日志只打印本地地址。
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import nutrition as N  # noqa: E402
from daylog import DayLog  # noqa: E402

WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
DEFAULT_STORE = {"code": "1450713", "name": "上海黄浦华旭国际大厦餐厅"}

STATE: dict = {"offline": True, "store": dict(DEFAULT_STORE), "menu": None, "nutrition": None,
               "catalog": {}, "coupons": {"mine": [], "available": []}, "note": ""}


# ------------------------------------------------------------------ 数据装载

def load_snapshot() -> None:
    with open(os.path.join(DATA_DIR, "menu.json"), encoding="utf-8") as fh:
        STATE["menu"] = json.load(fh)
    with open(os.path.join(DATA_DIR, "nutrition.json"), encoding="utf-8") as fh:
        STATE["nutrition"] = json.load(fh)
    STATE["catalog"] = N.build_catalog(STATE["menu"], STATE["nutrition"])


def load_live() -> None:
    """尽量用实时数据；任何一步失败都退回快照。"""
    load_snapshot()
    try:
        from mcp_client import McpClient

        client = McpClient()
        stores = client.nearby_stores("上海", "人民广场")
        if stores:
            first = stores[0]
            code = first.get("storeCode") or first.get("store_code") or DEFAULT_STORE["code"]
            name = first.get("storeName") or first.get("name") or DEFAULT_STORE["name"]
            STATE["store"] = {"code": str(code), "name": str(name)}
        menu = client.fetch_menu(STATE["store"]["code"])
        nutrition = client.fetch_nutrition()
        catalog = N.build_catalog(menu, nutrition)
        if len(catalog) < 10:
            raise RuntimeError("实时目录过小，疑似接口异常")
        STATE.update({"menu": menu, "nutrition": nutrition, "catalog": catalog, "offline": False})
        STATE["coupons"] = {"mine": client.my_coupons(), "available": client.available_coupons()}
        STATE["note"] = f"实时数据｜{len(catalog)} 款可计算餐品"
    except Exception as exc:  # noqa: BLE001 - 降级是预期行为
        STATE["offline"] = True
        STATE["note"] = f"离线快照（实时取数失败：{type(exc).__name__}）"


# ------------------------------------------------------------------ 业务处理

def bootstrap_payload() -> dict:
    catalog = STATE["catalog"]
    categories = []
    for cat in (STATE["menu"] or {}).get("categories", []):
        entries = [catalog[e["name"]] for e in [catalog.get(n) for n in _names_of(cat)] if e]
        if entries:
            categories.append({"name": cat["name"], "items": [e["name"] for e in entries]})
    return {
        "offline": STATE["offline"],
        "note": STATE["note"],
        "store": STATE["store"],
        "modes": {
            k: {"name": v["name"], "targets": v["targets"], "key": k}
            for k, v in N.DEFAULT_MODES.items()
        },
        "modeOrder": N.MODE_ORDER,
        "metrics": N.METRICS,
        "categories": categories,
        "items": sorted(catalog.values(), key=lambda e: (e["category"], e["name"])),
        "coupons": STATE["coupons"],
    }


def _names_of(cat: dict) -> list[str]:
    names = []
    for code in cat.get("codes", []):
        meal = (STATE["menu"] or {}).get("meals", {}).get(code)
        if meal:
            names.append(N.norm_name(meal.get("name")))
    return names


def evaluate_payload(body: dict) -> dict:
    items = body.get("items") or []
    mode = body.get("mode") or "weichi"
    profile = body.get("profile") or None
    computed = N.compute_modes(profile)
    modes = computed["modes"]
    targets = N.targets_for(mode, modes.get(mode, modes["weichi"])["targets"])

    catalog = STATE["catalog"]
    totals = N.totals_of(items, catalog)
    report = N.evaluate(totals, targets)
    swaps = N.suggest_swaps(items, catalog, targets, mode=mode, coupons=STATE["coupons"]["mine"])
    gaps = N.suggest_gaps(items, catalog, targets, mode=mode)

    price_before = N.price_of(items, catalog)
    after_items = _apply_swaps(items, swaps)
    price_after = N.price_of(after_items, catalog)
    after_report = N.evaluate(N.totals_of(after_items, catalog), targets)

    weight = float((profile or {}).get("weight") or 60)
    day = DayLog().summary(N.daily_targets(targets))
    return {
        "mode": mode,
        "modeName": modes.get(mode, {}).get("name", ""),
        "strategy": N.strategy_of(mode)["focus"],
        "profile": computed,
        "targets": targets,
        "totals": {k: round(v, 1) for k, v in totals.items()},
        "report": report,
        "swaps": swaps,
        "gaps": gaps,
        "equivalents": N.equivalents(totals, weight),
        "price": {"before": price_before, "after": price_after, "delta": round(price_after - price_before, 2)},
        "afterReport": {"score": after_report["score"], "light": after_report["light"]},
        "day": day,
        "coupons": STATE["coupons"]["mine"][:20],
    }


def _apply_swaps(items: list[dict], swaps: list[dict]) -> list[dict]:
    current = [{"name": it.get("name"), "qty": it.get("qty", 1)} for it in items]
    for swap in swaps:
        for idx, item in enumerate(current):
            if item["name"] == swap["from"]:
                current[idx] = {"name": swap["to"], "qty": item["qty"]}
                break
    return current


def stores_payload(query: dict) -> dict:
    city = (query.get("city") or ["上海"])[0]
    keyword = (query.get("keyword") or [""])[0]
    try:
        from mcp_client import McpClient

        return {"stores": McpClient().nearby_stores(city, keyword)}
    except Exception as exc:  # noqa: BLE001
        return {"stores": [], "error": str(exc)}


# ------------------------------------------------------------------ HTTP 层

class Handler(BaseHTTPRequestHandler):
    server_version = "McdTrafficLight/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - 静音默认访问日志
        return

    def _send(self, code: int, payload, ctype: str = "application/json; charset=utf-8") -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            with open(os.path.join(WEB_DIR, "index.html"), "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
            return
        if path == "/api/bootstrap":
            self._send(200, bootstrap_payload())
            return
        if path == "/api/day":
            targets = N.targets_for("weichi", N.DEFAULT_MODES["weichi"]["targets"])
            self._send(200, DayLog().summary(N.daily_targets(targets)))
            return
        if path == "/api/coupons":
            self._send(200, STATE["coupons"])
            return
        if path == "/api/stores":
            self._send(200, stores_payload(parse_qs(parsed.query)))
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/api/evaluate":
            self._send(200, evaluate_payload(body))
            return
        if path == "/api/day":
            catalog = STATE["catalog"]
            items = body.get("items") or []
            totals = N.totals_of(items, catalog)
            record = DayLog().log(
                body.get("slot") or "正餐", items, totals, price=N.price_of(items, catalog)
            )
            targets = N.targets_for(body.get("mode") or "weichi",
                                    N.DEFAULT_MODES.get(body.get("mode") or "weichi", N.DEFAULT_MODES["weichi"])["targets"])
            self._send(200, {"record": record, "day": DayLog().summary(N.daily_targets(targets))})
            return
        if path == "/api/day/reset":
            DayLog().reset_today()
            self._send(200, {"ok": True})
            return
        if path == "/api/reload":
            load_live()
            self._send(200, bootstrap_payload())
            return
        self._send(404, {"error": "not found"})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print("正在连接麦当劳 MCP…")
    load_live()
    status = "实时数据" if not STATE["offline"] else "离线快照"
    print(f"数据源：{status}（{STATE['note']}）")
    print(f"可计算餐品：{len(STATE['catalog'])} 款")
    print(f"门店：{STATE['store']['name']}")
    print(f"\n打开浏览器访问：http://127.0.0.1:{port}\n")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
