"""验收自检 —— 对应执行计划 §9 的 T1~T6。

跑法：
    python tests/selftest.py            离线（用 data/ 快照）
    python tests/selftest.py --live     额外跑一次真实 MCP 取数

所有期望值都来自麦当劳官方营养表（158 款在售餐品），不是估算。
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import nutrition as N  # noqa: E402
from daylog import DayLog  # noqa: E402

PASS, FAIL = [], []


def check(name: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(name)
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}\n         got={got}\n        want={want}")


def load():
    with open(os.path.join(ROOT, "data", "menu.json"), encoding="utf-8") as fh:
        menu = json.load(fh)
    with open(os.path.join(ROOT, "data", "nutrition.json"), encoding="utf-8") as fh:
        nutrition = json.load(fh)
    return menu, nutrition


def main() -> int:
    menu, nutrition = load()
    catalog = N.build_catalog(menu, nutrition)
    print(f"\n目录：{len(catalog)} 款可计算餐品（菜单 {len(menu['meals'])} 款 ∩ 营养表 {len(nutrition)} 款）")

    base_items = [{"name": "巨无霸", "qty": 1}, {"name": "薯条", "qty": 1}, {"name": "可乐", "qty": 1}]
    targets = N.targets_for("weichi", N.DEFAULT_MODES["weichi"]["targets"])
    totals = N.totals_of(base_items, catalog)
    report = N.evaluate(totals, targets)

    print("\nT1 维持档默认目标下的判灯")
    check("T1 热量合计", round(totals["kcal"]), 949)
    check("T1 蛋白质合计", round(totals["protein"]), 31)
    check("T1 脂肪合计", round(totals["fat"]), 38)
    check("T1 碳水合计", round(totals["carb"]), 116)
    check("T1 钠合计", round(totals["sodium"]), 1126)
    check("T1 钙合计", round(totals["calcium"]), 199)
    check("T1 得分", report["score"], 52)
    check("T1 灯色", report["light"], "red")
    check("T1 警示语", report["warn"], "钠超了 69%")
    check(
        "T1 六项灯色",
        [report["lights"][k] for k in ("kcal", "protein", "fat", "carb", "sodium", "calcium")],
        ["amber", "amber", "red", "green", "red", "amber"],
    )

    print("\nT2 智能替换（在真实菜单里穷举，不是写死的）")
    swaps = N.suggest_swaps(base_items, catalog, targets)
    check("T2 替换条数", len(swaps), 2)
    check("T2 最佳替换", [swaps[0]["from"], swaps[0]["to"]], ["薯条", "苹果片"])
    check("T2 替换收益", swaps[0]["gains"], ["钠 −165 mg", "热量 −257 kcal"])
    check("T2 采纳后得分", swaps[0]["scoreTo"], 76)
    check("T2 采纳后灯色", swaps[0]["lightTo"], "amber")
    check("T2 价差（比原来更便宜）", swaps[0]["priceDelta"], -6.5)

    new_items = [{"name": "巨无霸", "qty": 1}, {"name": "苹果片", "qty": 1}, {"name": "可乐", "qty": 1}]
    new_totals = N.totals_of(new_items, catalog)
    print("\nT4 补短板（达标型指标不足时给「加什么」）")
    gaps = N.suggest_gaps(new_items, catalog, targets)
    check("T4 有补短板建议", len(gaps) >= 1, True)
    check("T4 首条建议是补钙补蛋白饮品", gaps[0]["add"], "牛奶")
    check("T4 钙补足到 100%", "达标" in gaps[0]["fills"], True)
    check("T4 采纳后总分", N.evaluate(new_totals, targets)["score"], 76)

    print("\nT3 个性化目标（Mifflin-St Jeor）")
    profile = {"gender": "female", "age": 26, "height": 160, "weight": 48, "activity": 1.2}
    computed = N.compute_modes(profile)
    check("T3 BMR", computed["bmr"], 1189)
    check("T3 TDEE", computed["tdee"], 1427)
    check("T3 维持档单餐热量目标", computed["modes"]["weichi"]["targets"]["kcal"], 476)
    check("T3 维持档单餐脂肪目标", computed["modes"]["weichi"]["targets"]["fat"], 15)
    petite = N.evaluate(totals, computed["modes"]["weichi"]["targets"])
    check("T3 同一顿饭的得分（变为红灯）", petite["score"], 49)
    check("T3 同一顿饭的灯色", petite["light"], "red")

    print("\nT5 跨餐累计")
    log_path = os.path.join(ROOT, "data", "selftest_daylog.json")
    if os.path.exists(log_path):
        os.remove(log_path)
    log = DayLog(log_path)
    log.log("正餐", base_items, totals, price=48.5)
    log.log("下午茶", [{"name": "麦乐鸡", "qty": 1}], N.totals_of([{"name": "麦乐鸡", "qty": 1}], catalog), price=14)
    daily = N.daily_targets(targets)
    summary = log.summary(daily)
    check("T5 已记录餐次", summary["mealCount"], 2)
    check("T5 当日钠累计", round(summary["eaten"]["sodium"]), 1126 + 422)
    check("T5 钠剩余额度", round(summary["remaining"]["sodium"]), round(N.DAILY_SODIUM - 1548))
    os.remove(log_path)

    print("\nT6 等价换算（把数字变成直觉）")
    eq = N.equivalents(totals, weight_kg=65)
    check("T6 米饭碗数", eq["riceBowls"], 3.7)
    check("T6 食盐克数", eq["saltGram"], 2.9)
    check("T6 快走分钟", eq["walkMinutes"], 219)

    print("\nT8 三档推荐必须分化（防止退化成一个方案×三个及格线）")

    # 同一份餐盘，逐档跑（默认模式目标）
    per_mode = {}
    for mode in N.MODE_ORDER:
        t = N.targets_for(mode, N.DEFAULT_MODES[mode]["targets"])
        per_mode[mode] = {
            "swaps": [(x["from"], x["to"]) for x in
                      N.suggest_swaps(base_items, catalog, t, mode=mode)],
            "gaps": [x["add"] for x in
                     N.suggest_gaps(base_items, catalog, t, mode=mode)],
        }

    # ① 硬性：减脂与增肌的「补短板」不得完全相同
    #    （两档诉求相反，若结果一致说明策略没生效）
    check("T8 减脂/增肌补短板不全同",
          per_mode["jianzhi"]["gaps"] != per_mode["zengji"]["gaps"], True)

    # ② 语义：减脂档不得把「高糖高脂甜品」推成替换目标
    dessert_bad = [to for _, to in per_mode["jianzhi"]["swaps"]
                   if any(b in to for b in N.DESSERT_BAN)]
    check("T8 减脂档不推高糖甜品", dessert_bad, [])

    # ③ 语义：减脂档不得把「含糖饮料」推成补短板项
    drink_bad = [a for a in per_mode["jianzhi"]["gaps"]
                 if any(b in a for b in N.SUGARY_DRINK_BAN)]
    check("T8 减脂档不推含糖饮料", drink_bad, [])

    # ④ 语义：增肌档的补短板里应出现高蛋白项（牛奶/鸡/蛋白/燕麦）
    protein_hint = ("牛奶", "鸡", "蛋白", "燕麦")
    check("T8 增肌档补蛋白",
          any(any(h in a for h in protein_hint) for a in per_mode["zengji"]["gaps"]), True)

    # ⑤ 每档都必须给得出建议（不能因为硬约束过强而全空）
    check("T8 三档均能给出建议",
          all(v["swaps"] or v["gaps"] for v in per_mode.values()), True)

    # ⑥ 语义：至少两档的替换项不同（「方案变了」的肉眼证据）
    distinct = {tuple(v["swaps"]) for v in per_mode.values()}
    check("T8 替换方案至少两档不同", len(distinct) >= 2, True)

    print("\nT9 补短板不得同 role 叠加（防止「补着补着多两杯饮料」）")

    role_of = {e["name"]: e["role"] for e in catalog.values()}

    # ① 三档的 top-2 不得两条都是 drink（菜单里饮品占近一半，最容易塌缩）
    for mode in N.MODE_ORDER:
        t = N.targets_for(mode, N.DEFAULT_MODES[mode]["targets"])
        gaps = N.suggest_gaps(base_items, catalog, t, mode=mode)
        roles = [role_of.get(g["add"], "?") for g in gaps]
        two_drinks = len([r for r in roles if r == "drink"]) >= 2
        check(f"T9 {N.DEFAULT_MODES[mode]['name']} 不同时推两杯饮料", two_drinks, False)

    # ② 语义：餐盘里已有效补过钙的饮料（燕麦奶 438mg），不得再推第二杯饮料
    t = N.targets_for("weichi", N.DEFAULT_MODES["weichi"]["targets"])
    with_oat = base_items + [{"name": "燕麦奶", "qty": 1}]
    gaps2 = N.suggest_gaps(with_oat, catalog, t, mode="weichi")
    check("T9 已有燕麦奶后不再推饮料",
          [g["add"] for g in gaps2 if role_of.get(g["add"]) == "drink"], [])

    # ③ 反例保护：可乐是"空饮料"（0 蛋白 0 钙），不能因为盘里有它就禁推补钙饮料
    with_cola = base_items  # base_items 已含可乐
    gaps3 = N.suggest_gaps(with_cola, catalog, t, mode="weichi")
    check("T9 盘里有可乐仍应推补钙/蛋白项", len(gaps3) >= 1, True)

    print("         三档实测：")
    for mode in N.MODE_ORDER:
        print(f"           {N.DEFAULT_MODES[mode]['name']}  swaps={per_mode[mode]['swaps']}  gaps={per_mode[mode]['gaps']}")

    if "--live" in sys.argv:
        print("\nT7 真实 MCP 取数（需要 Token）")
        from mcp_client import McpClient

        client = McpClient()
        live_nutrition = client.fetch_nutrition()
        live_menu = client.fetch_menu("1450713")
        live_catalog = N.build_catalog(live_menu, live_nutrition)
        print(f"         实时菜单 {len(live_menu['meals'])} 款 / 实时可计算 {len(live_catalog)} 款")
        print("         注：菜单按时段（早餐/正餐/下午茶/夜宵）切换，实时条数与离线快照不同属正常")
        check("T7 营养条目数（稳定 158 款）", len(live_nutrition), len(nutrition))
        check("T7 实时菜单非空", len(live_menu["meals"]) >= 60, True)
        check("T7 实时目录可计算", len(live_catalog) >= 20, True)
        live_totals = N.totals_of(base_items, live_catalog)
        check("T7 实时数据判灯与离线一致", N.evaluate(live_totals, targets)["score"], 52)

    print(f"\n{'=' * 46}\n通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + "，".join(FAIL))
    print("=" * 46)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
