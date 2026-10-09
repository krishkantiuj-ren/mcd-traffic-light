"""跨餐累计 —— 把"这一顿"变成"这一天的预算"。

单餐判灯只能回答"这顿行不行"，营养管理真正要回答的是
"我今天还能吃什么"。日志只落在本机 data/day_log.json，不上传任何数据。
"""
from __future__ import annotations

import datetime as _dt
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "data", "day_log.json")


class DayLog:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self.entries: dict[str, dict] = {}
        self.load()

    # ---------- 存储 ----------

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.entries = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            self.entries = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.entries, fh, ensure_ascii=False, indent=1)

    # ---------- 读写 ----------

    def log(self, slot: str, items: list[dict], totals: dict, price: float = 0.0, date: str | None = None) -> dict:
        day = date or _dt.date.today().isoformat()
        record = {
            "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "slot": slot,
            "items": items,
            "totals": {k: round(float(v), 1) for k, v in totals.items()},
            "price": round(float(price), 2),
        }
        self.entries.setdefault(day, {"meals": []})["meals"].append(record)
        self.save()
        return record

    def today(self, date: str | None = None) -> dict:
        day = date or _dt.date.today().isoformat()
        return self.entries.get(day, {"meals": []})

    def eaten(self, date: str | None = None) -> dict:
        meals = self.today(date).get("meals", [])
        totals: dict[str, float] = {}
        for meal in meals:
            for key, value in meal.get("totals", {}).items():
                totals[key] = totals.get(key, 0.0) + float(value)
        return totals

    def summary(self, daily_targets: dict, date: str | None = None) -> dict:
        meals = self.today(date).get("meals", [])
        eaten = self.eaten(date)
        remaining = {
            key: round(max(0.0, float(target) - eaten.get(key, 0.0)), 1)
            for key, target in (daily_targets or {}).items()
        }
        return {
            "date": date or _dt.date.today().isoformat(),
            "mealCount": len(meals),
            "meals": meals,
            "eaten": {k: round(v, 1) for k, v in eaten.items()},
            "remaining": remaining,
            "targets": daily_targets,
        }

    def reset_today(self, date: str | None = None) -> None:
        day = date or _dt.date.today().isoformat()
        self.entries.pop(day, None)
        self.save()
