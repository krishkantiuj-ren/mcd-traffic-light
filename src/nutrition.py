"""营养引擎 —— 判灯、打分、个性化目标、替换推荐、补短板。

全部是纯函数，不碰网络也不碰磁盘，方便单测与复用。
判灯口径：以《中国居民膳食指南(2022)》成人每日推荐量 ÷ 3 作为默认单餐目标，
人群档与身体数据只改变"目标线"，不改变判灯规则。

※ 设计原则：取数交给 MCP，算术交给代码。
  绝不让大模型口算热量与价格 —— 这里全部是确定性算法。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 基础定义

# cap = 限制型（越少越好）；min = 达标型（越多越好）
METRICS: list[dict] = [
    {"key": "kcal", "label": "能量", "unit": "kcal", "cap": True},
    {"key": "protein", "label": "蛋白质", "unit": "g", "cap": False},
    {"key": "fat", "label": "脂肪", "unit": "g", "cap": True},
    {"key": "carb", "label": "碳水", "unit": "g", "cap": False},
    {"key": "sodium", "label": "钠", "unit": "mg", "cap": True},
    {"key": "calcium", "label": "钙", "unit": "mg", "cap": False},
]
METRIC_KEYS = [m["key"] for m in METRICS]
NUT_FIELDS = {
    "kcal": "energyKcal",
    "protein": "protein",
    "fat": "fat",
    "carb": "carbohydrate",
    "sodium": "sodium",
    "calcium": "calcium",
}

# 默认单餐目标（通用成人参考，未定制身体数据时使用）
DEFAULT_MODES: dict[str, dict] = {
    "jianzhi": {"name": "减脂档", "targets": {"kcal": 500, "protein": 28, "fat": 17, "carb": 40, "sodium": 600, "calcium": 267}},
    "weichi": {"name": "维持档", "targets": {"kcal": 700, "protein": 25, "fat": 23, "carb": 50, "sodium": 667, "calcium": 267}},
    "zengji": {"name": "增肌档", "targets": {"kcal": 850, "protein": 38, "fat": 27, "carb": 65, "sodium": 700, "calcium": 300}},
}
MODE_ORDER = ["jianzhi", "weichi", "zengji"]
ACTIVITY_LEVELS = {"久坐": 1.2, "轻度": 1.375, "中度": 1.55}
# 每日与体重无关的上限/推荐量（世界卫生组织 / 膳食指南）
DAILY_SODIUM = 2000.0
DAILY_CALCIUM = 800.0

# 餐段划分（按本地小时）
SLOTS = [("早餐", 5, 10), ("正餐", 10, 16), ("下午茶", 16, 5)]
SLOT_ORDER = ["早餐", "正餐", "下午茶"]

COMBO_TOKENS = ("套", "餐", "盒", "拼", "桶", "份", "组")
ROLE_RULES = [
    ("drink", ("饮品", "麦咖啡")),
    ("side", ("小食", "蘸酱", "甜品", "其他")),
    ("fried", ("炸鸡",)),
    ("burger", ("堡", "卷", "鸡肉", "人气", "单人")),
]


# ---------------------------------------------------------------- 名称归一化

def norm_name(name: str | None) -> str:
    """去掉换行、商标符、括号后缀，便于菜单名与营养表名对齐。"""
    text = (name or "").replace("\n", "").replace("™", "").strip()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return text.strip()


def _has_combo_residue(residual: str) -> bool:
    return any(token in residual for token in COMBO_TOKENS)


def match_nutrition(menu_name: str, nutrition: dict[str, dict]) -> str | None:
    """把菜单餐品名匹配到营养表条目。

    菜单是"薯条 / 可乐 / 玉米杯 / 麦咖啡美式"这类基础名，
    营养表是"中薯条 / 可乐中杯 / 小杯玉米杯 / 热美式中杯"这类规格名，
    所以需要：精确 -> 营养名包含菜单名 -> 菜单名包含营养名，并排除套餐名（三件套等）。
    """
    menu = norm_name(menu_name)
    if not menu:
        return None
    if menu in nutrition:
        return menu
    candidates = [
        key
        for key in nutrition
        if menu in key and not _has_combo_residue(key.replace(menu, ""))
    ]
    if not candidates:
        candidates = [
            key
            for key in nutrition
            if key in menu and not _has_combo_residue(menu.replace(key, ""))
        ]
    if not candidates:
        return None
    # 默认规格：优先"中"，其次"小"，再次"热"，最后取最短名
    for prefer in ("中", "小", "热"):
        subset = [c for c in candidates if prefer in c]
        if subset:
            return min(subset, key=len)
    return min(candidates, key=len)


def role_of(category: str) -> str:
    for role, keywords in ROLE_RULES:
        if any(kw in category for kw in keywords):
            return role
    return "other"


# ---------------------------------------------------------------- 目录构建

def build_catalog(menu: dict, nutrition: dict[str, dict]) -> dict[str, dict]:
    """把菜单（图/价/分类）与营养表合并成可计算目录。"""
    category_of: dict[str, str] = {}
    for cat in menu.get("categories", []):
        for code in cat.get("codes", []):
            category_of[code] = cat.get("name", "")

    catalog: dict[str, dict] = {}
    for code, meal in (menu.get("meals") or {}).items():
        name = norm_name(meal.get("name"))
        nut_key = match_nutrition(meal.get("name"), nutrition)
        if not name or not nut_key or name in catalog:
            continue
        category = category_of.get(code, "")
        nut = nutrition[nut_key]
        catalog[name] = {
            "name": name,
            "code": code,
            "nutKey": nut_key,
            "category": category,
            "role": role_of(category),
            "image": meal.get("image") or "",
            "price": _to_float(meal.get("currentPrice")),
            "originalPrice": _to_float(meal.get("originalPrice")),
            "nut": {k: float(nut.get(f) or 0) for k, f in NUT_FIELDS.items()},
        }
    return catalog


def _to_float(value) -> float:
    try:
        return float(str(value).replace("¥", "").strip())
    except (TypeError, ValueError):
        return 0.0


def resolve(catalog: dict[str, dict], name: str) -> dict | None:
    """按显示名 / 营养表名 / 归一化名查找目录条目。"""
    if name in catalog:
        return catalog[name]
    target = norm_name(name)
    for entry in catalog.values():
        if entry["nutKey"] == name or entry["nutKey"] == target or norm_name(entry["name"]) == target:
            return entry
    for entry in catalog.values():
        if target and (target in entry["name"] or entry["name"] in target):
            return entry
    return None


def empty_totals() -> dict:
    return {k: 0.0 for k in METRIC_KEYS}


def totals_of(items: list[dict], catalog: dict[str, dict]) -> dict:
    """items: [{"name": "巨无霸", "qty": 1}, ...]"""
    totals = empty_totals()
    for item in items:
        entry = resolve(catalog, item.get("name", ""))
        if not entry:
            continue
        qty = float(item.get("qty", 1) or 1)
        for key in METRIC_KEYS:
            totals[key] += entry["nut"][key] * qty
    return totals


def price_of(items: list[dict], catalog: dict[str, dict]) -> float:
    total = 0.0
    for item in items:
        entry = resolve(catalog, item.get("name", ""))
        if entry:
            total += entry["price"] * float(item.get("qty", 1) or 1)
    return round(total, 2)


# ---------------------------------------------------------------- 目标与判灯

def compute_modes(profile: dict | None) -> dict:
    """按身体数据计算三档单餐目标（Mifflin-St Jeor + 活动系数）。

    profile: {"gender": "male|female", "age": 28, "height": 170, "weight": 65,
              "activity": 1.2}
    """
    if not profile:
        return {
            "bmr": None,
            "tdee": None,
            "modes": {k: {"name": v["name"], "targets": dict(v["targets"])} for k, v in DEFAULT_MODES.items()},
            "customized": False,
        }
    gender = profile.get("gender", "male")
    age = float(profile.get("age") or 28)
    height = float(profile.get("height") or 170)
    weight = float(profile.get("weight") or 65)
    activity = float(profile.get("activity") or 1.2)

    bmr = 10 * weight + 6.25 * height - 5 * age + (5 if gender == "male" else -161)
    tdee = round(bmr * activity)
    daily_kcal = {
        "jianzhi": max(1200.0, tdee - 400),
        "weichi": float(tdee),
        "zengji": float(tdee + 300),
    }
    protein_per_kg = {"jianzhi": 1.8, "weichi": 1.2, "zengji": 1.8}
    fat_ratio = {"jianzhi": 0.22, "weichi": 0.27, "zengji": 0.25}

    modes = {}
    for mode in MODE_ORDER:
        kcal_daily = daily_kcal[mode]
        protein_daily = weight * protein_per_kg[mode]
        fat_daily = kcal_daily * fat_ratio[mode] / 9
        carb_daily = max(
            60.0, (kcal_daily - protein_daily * 4 - fat_daily * 9) / 4
        )
        modes[mode] = {
            "name": DEFAULT_MODES[mode]["name"],
            "targets": {
                "kcal": round(kcal_daily / 3),
                "protein": max(10, round(protein_daily / 3)),
                "fat": max(5, round(fat_daily / 3)),
                "carb": max(20, round(carb_daily / 3)),
                "sodium": round(DAILY_SODIUM / 3),
                "calcium": round(DAILY_CALCIUM / 3),
            },
        }
    return {"bmr": round(bmr), "tdee": tdee, "modes": modes, "customized": True}


def targets_for(mode: str, targets: dict) -> dict:
    return {k: float(v) for k, v in targets.items()}


def level_of(metric: dict, value: float, target: float) -> str:
    """单个指标的红黄绿。"""
    if target <= 0:
        return "green"
    ratio = value / target
    if metric["cap"]:
        if ratio > 1.5:
            return "red"
        return "amber" if ratio > 1 else "green"
    if ratio >= 1:
        return "green"
    return "amber" if ratio >= 0.7 else "red"


def evaluate(totals: dict, targets: dict) -> dict:
    """逐项判灯 + 总分 + 整体灯色 + 最严重超标项。"""
    metrics = []
    score = 100
    for metric in METRICS:
        value = round(totals.get(metric["key"], 0.0), 1)
        target = float(targets.get(metric["key"], 0))
        light = level_of(metric, value, target)
        if light == "red":
            score -= 15
        elif light == "amber":
            score -= 6
        metrics.append(
            {
                **metric,
                "value": value,
                "target": target,
                "ratio": round(value / target, 3) if target else 0.0,
                "light": light,
            }
        )
    score = max(0, score)
    light = "green" if score >= 80 else ("amber" if score >= 55 else "red")
    worst = None
    for metric in METRICS:
        if not metric["cap"]:
            continue
        target = float(targets.get(metric["key"], 0))
        if target <= 0:
            continue
        ratio = totals.get(metric["key"], 0.0) / target
        if ratio > 1 and (worst is None or ratio > worst["ratio"]):
            worst = {"key": metric["key"], "label": metric["label"], "ratio": ratio}
    warn = f"{worst['label']}超了 {round((worst['ratio'] - 1) * 100)}%" if worst else "各项均在目标内"
    return {
        "metrics": metrics,
        "score": score,
        "light": light,
        "warn": warn,
        "worst": worst,
        "lights": {m["key"]: m["light"] for m in metrics},
    }


def equivalents(totals: dict, weight_kg: float = 60.0) -> dict:
    """把数字变成直觉：几碗米饭、几克盐、要走多久。"""
    kcal = totals.get("kcal", 0.0)
    sodium = totals.get("sodium", 0.0)
    return {
        "riceBowls": round(kcal / 256, 1),          # 一碗米饭约 256 kcal
        "saltGram": round(sodium * 2.54 / 1000, 1),  # 钠 -> 食盐
        "walkMinutes": round(kcal / (4 * weight_kg / 60)) if kcal else 0,   # 快走 4 METs
        "runMinutes": round(kcal / (8 * weight_kg / 60)) if kcal else 0,    # 慢跑 8 METs
    }


# ---------------------------------------------------------------- 替换与补短板

def _coupon_value(coupon: dict) -> float:
    name = str(coupon.get("name") or coupon.get("couponName") or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*元", name)
    if match:
        return float(match.group(1))
    for key in ("value", "amount", "discountAmount"):
        if coupon.get(key) not in (None, ""):
            return _to_float(coupon.get(key))
    return 5.0


def coupon_loss(entry: dict, partner: dict, coupons: list[dict] | None) -> tuple[float, str | None]:
    """换掉一个商品会不会浪费手里的券。"""
    if not coupons:
        return 0.0, None
    names = {entry["name"], entry["nutKey"]}
    for coupon in coupons:
        text = str(coupon.get("name") or coupon.get("couponName") or "")
        if any(n and n in text for n in names):
            if partner["name"] in text or partner["nutKey"] in text:
                continue  # 换过去的商品同样能用券
            return _coupon_value(coupon), text or entry["name"]
    return 0.0, None


def suggest_swaps(
    items: list[dict],
    catalog: dict[str, dict],
    targets: dict,
    coupons: list[dict] | None = None,
    max_price_delta: float = 5.0,
    top: int = 2,
) -> list[dict]:
    """在同类餐品里找"更健康"的替换，返回收益最高且代价可接受的方案。

    采用**贪心逐次**策略：每挑出一个替换就把它落到餐盘上，再基于新餐盘找下一个。
    这样多个替换之间是叠加生效的，不会出现"单独看都没用、合起来才有用"被漏掉的情况。
    合格条件：总分不降，且要么涨分、要么明显缩小超标幅度（improvement > 0）。
    """
    current = [{"name": it.get("name", ""), "qty": float(it.get("qty", 1) or 1)} for it in items]
    chosen: list[dict] = []
    used_slots: set[int] = set()
    pool = list(catalog.values())

    for _ in range(max(0, top)):
        base_totals = totals_of(current, catalog)
        base = evaluate(base_totals, targets)
        best: dict | None = None

        for idx, item in enumerate(current):
            if idx in used_slots:
                continue
            entry = resolve(catalog, item["name"])
            if not entry:
                continue
            qty = item["qty"]
            for partner in pool:
                if partner["name"] == entry["name"] or partner["role"] != entry["role"]:
                    continue
                loss, lost_coupon = coupon_loss(entry, partner, coupons)
                price_delta = round(partner["price"] * qty - entry["price"] * qty, 2)
                if price_delta + loss > max_price_delta:
                    continue
                trial = list(current)
                trial[idx] = {"name": partner["name"], "qty": qty}
                after_totals = totals_of(trial, catalog)
                after = evaluate(after_totals, targets)
                score_gain = after["score"] - base["score"]
                improvement = _improvement(base_totals, after_totals, targets)
                if score_gain < 0 or (score_gain == 0 and improvement <= 0.05):
                    continue
                gains = _metric_gains(entry, partner, qty)
                if not gains:
                    continue
                candidate = {
                    "from": entry["name"],
                    "to": partner["name"],
                    "fromImage": entry["image"],
                    "toImage": partner["image"],
                    "gain": score_gain,
                    "scoreTo": after["score"],
                    "lightTo": after["light"],
                    "priceDelta": price_delta,
                    "couponLoss": loss,
                    "lostCoupon": lost_coupon,
                    "gains": gains,
                    "slot": idx,
                    "newTotals": after_totals,
                    "_rank": score_gain * 10 + improvement - 0.5 * (price_delta + loss),
                }
                if best is None or candidate["_rank"] > best["_rank"]:
                    best = candidate

        if best is None:
            break
        used_slots.add(best["slot"])
        current[best["slot"]] = {"name": best["to"], "qty": current[best["slot"]]["qty"]}
        best.pop("_rank", None)
        best["totalsAfter"] = {k: round(v, 1) for k, v in best.pop("newTotals").items()}
        chosen.append(best)

    return chosen


def _improvement(before: dict, after: dict, targets: dict) -> float:
    """连续型收益：限制型指标的超标幅度缩小 + 达标型指标的缺口缩小。"""
    total = 0.0
    for metric in METRICS:
        target = float(targets.get(metric["key"], 0))
        if target <= 0:
            continue
        b = before.get(metric["key"], 0.0) / target
        a = after.get(metric["key"], 0.0) / target
        if metric["cap"]:
            total += max(0.0, b - a) if b > 1 else 0.0
        else:
            total += max(0.0, min(b, 1.0) - min(a, 1.0))
    return round(total, 4)


def _metric_gains(from_entry: dict, to_entry: dict, qty: float) -> list[str]:
    """把替换收益写成人话，只保留有意义的项。"""
    gains = []
    for key, label, unit in (("sodium", "钠", "mg"), ("kcal", "热量", "kcal"), ("fat", "脂肪", "g")):
        delta = (to_entry["nut"][key] - from_entry["nut"][key]) * qty
        if delta <= -5:
            gains.append(f"{label} −{_fmt(abs(delta))} {unit}")
    for key, label, unit in (("protein", "蛋白质", "g"), ("calcium", "钙", "mg")):
        delta = (to_entry["nut"][key] - from_entry["nut"][key]) * qty
        if delta >= 5:
            gains.append(f"{label} +{_fmt(delta)} {unit}")
    return gains[:2]


def _fmt(value: float) -> str:
    return str(int(round(value))) if abs(value - round(value)) < 0.05 else f"{value:.1f}"


def suggest_gaps(
    items: list[dict],
    catalog: dict[str, dict],
    targets: dict,
    top: int = 2,
    max_added_kcal: float = 200.0,
) -> list[dict]:
    """补短板：达标型指标不足时，推荐加什么而不是减什么。"""
    totals = totals_of(items, catalog)
    deficits = {}
    for metric in METRICS:
        if metric["cap"]:
            continue
        missing = float(targets.get(metric["key"], 0)) - totals.get(metric["key"], 0.0)
        if missing > 0:
            deficits[metric["key"]] = missing
    if not deficits:
        return []

    in_tray = {resolve(catalog, it.get("name", ""))["name"] for it in items if resolve(catalog, it.get("name", ""))}
    candidates = []
    for entry in catalog.values():
        if entry["name"] in in_tray or entry["role"] not in ("side", "drink", "dessert"):
            continue
        kcal = entry["nut"]["kcal"]
        if kcal > max_added_kcal:
            continue
        filled = sum(min(missing, entry["nut"][key]) for key, missing in deficits.items())
        if filled <= 0:
            continue
        # 营养密度：每 100 kcal 能补多少缺口 —— 避免推荐"热量高但勉强够量"的选项
        density = sum(entry["nut"][key] for key in deficits) / max(kcal, 40) * 100
        candidates.append(
            {
                "add": entry["name"],
                "image": entry["image"],
                "price": entry["price"],
                "kcal": round(kcal),
                "fills": _fill_text(entry, deficits, targets, totals),
                "efficiency": round(density, 2),
                "fillsAmount": round(filled, 1),
            }
        )
    candidates.sort(key=lambda c: (c["fillsAmount"], c["efficiency"]), reverse=True)
    return candidates[:top]


def _fill_text(entry: dict, deficits: dict, targets: dict, totals: dict) -> str:
    parts = []
    for key, label, unit in (("calcium", "钙", "mg"), ("protein", "蛋白质", "g"), ("carb", "碳水", "g")):
        if key not in deficits:
            continue
        added = entry["nut"][key]
        if added < 5:
            continue
        after = totals[key] + added
        tag = "达标" if after >= targets[key] else f"到 {round(after / targets[key] * 100)}%"
        parts.append(f"{label} +{_fmt(added)}{unit}（{tag}）")
    return " · ".join(parts[:2])


# ---------------------------------------------------------------- 跨餐累计

def daily_targets(targets_per_meal: dict, meals_per_day: int = 3) -> dict:
    """单餐目标 ×3 作为每日基准；钠/钙直接按每日推荐量。"""
    daily = {k: float(v) * meals_per_day for k, v in targets_per_meal.items()}
    daily["sodium"] = DAILY_SODIUM
    daily["calcium"] = DAILY_CALCIUM
    return daily


def slot_of_hour(hour: int) -> str:
    if 5 <= hour < 10:
        return "早餐"
    if 10 <= hour < 16:
        return "正餐"
    return "下午茶"
