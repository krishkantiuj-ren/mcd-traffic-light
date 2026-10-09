"""拉取麦当劳菜单与营养数据，落盘成开发用 JSON。

用法：
    python tools/fetch-menu.py [storeCode] [beType] [orderType]

默认：storeCode=1450713（上海黄浦华旭国际大厦），beType=1（到店），orderType=1。
产出：
    data/menu.json      分类 + 餐品（含 image / currentPrice / originalPrice / discountType / tags）
    data/nutrition.json 餐品名 -> 营养数据
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import util as _util

_spec = _util.spec_from_file_location(
    "mcp_probe", os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp-probe.py")
)
_probe = _util.module_from_spec(_spec)
_spec.loader.exec_module(_probe)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")


def text_of(resp):
    out = ""
    for item in resp.get("result", {}).get("content", []):
        if item.get("type") == "text":
            out += item.get("text", "")
    return out


def extract_json(s):
    m = re.search(r'\{\s*"success"\s*:\s*true.*\}\s*$', s, re.S)
    if not m:
        raise RuntimeError("响应里没有找到 JSON 主体")
    return json.loads(m.group(0))


def parse_nutrition(s):
    """list-nutrition-foods 的真实格式：外层 JSON 的 data 字段里，
    装着一个以换行分隔的 '名字,描述,kj,kcal,蛋白,脂肪,碳水,钠,钙' 字符串表。
    所以必须先解外层 JSON，再取 data 逐行解析。"""
    inner = extract_json(s).get("data", "")
    rows = []
    for line in inner.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("{"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 9:
            continue
        name = parts[0]
        try:
            nums = [float(x) if x not in ("null", "") else None for x in parts[2:]]
        except ValueError:
            continue
        rows.append({
            "name": name,
            "description": None if parts[1] == "null" else parts[1],
            "energyKj": nums[0], "energyKcal": nums[1],
            "protein": nums[2], "fat": nums[3], "carbohydrate": nums[4],
            "sodium": nums[5], "calcium": nums[6],
        })
    return rows


def main():
    store = sys.argv[1] if len(sys.argv) > 1 else "1450713"
    be_type = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    order_type = int(sys.argv[3]) if len(sys.argv) > 3 else 1

    os.makedirs(DATA, exist_ok=True)

    print("拉取餐品列表...")
    raw = text_of(_probe.rpc("tools/call", {
        "name": "query-meals",
        "arguments": {"beType": be_type, "orderType": order_type, "storeCode": store},
    }))
    payload = extract_json(raw).get("data", {})
    meals = payload.get("meals", {})
    categories = payload.get("categories", [])

    menu = {
        "storeCode": store,
        "beType": be_type,
        "orderType": order_type,
        "categories": [{"name": c.get("name"), "codes": [m.get("code") for m in c.get("meals", [])]}
                       for c in categories],
        "meals": meals,
    }
    with open(os.path.join(DATA, "menu.json"), "w", encoding="utf-8") as f:
        json.dump(menu, f, ensure_ascii=False, indent=1)
    print(f"  -> data/menu.json  分类 {len(categories)} 个，餐品 {len(meals)} 款")

    print("拉取营养数据...")
    nraw = text_of(_probe.rpc("tools/call", {"name": "list-nutrition-foods", "arguments": {}}))
    rows = parse_nutrition(nraw)
    nutrition = {r["name"]: r for r in rows}
    with open(os.path.join(DATA, "nutrition.json"), "w", encoding="utf-8") as f:
        json.dump(nutrition, f, ensure_ascii=False, indent=1)
    print(f"  -> data/nutrition.json  餐品 {len(nutrition)} 款")

    with_img = [m for m in meals.values() if m.get("image")]
    print(f"\n带商品图的餐品: {len(with_img)} / {len(meals)}")
    print("分类:", [c["name"].replace("\n", "") for c in categories])


if __name__ == "__main__":
    main()
