---
name: mcd-traffic-light
description: 麦麦营养红绿灯 —— 点餐时判读这一餐的营养红绿灯、给出健康替换与补短板建议。当用户提到麦当劳点餐、看餐品的营养/热量/钠含量、减脂增肌怎么点、这一顿健康不健康、帮我换更健康的搭配、我的营养目标是多少时使用。
---

# 麦麦营养红绿灯

基于麦当劳官方 MCP 的餐品营养判读 Skill。**判灯与计算全部走确定性算法**（`src/nutrition.py`），
不要用自然语言口算热量或钠含量。

## 何时使用

- 用户问「这顿麦当劳健康吗 / 多少卡 / 钠高不高」
- 用户要「减脂/增肌怎么点」「帮我换一个更健康的搭配」
- 用户要设置自己的营养目标（身高体重）
- 用户问「今天还能吃什么 / 今天已经吃了多少」

## 依赖

- 麦当劳 MCP 连接器 `mcd-mcp`（Streamable HTTP，`https://mcp.mcd.cn`）
- Python 3.10+（仅标准库，无需 pip 安装）

## 使用方式

### 方式一：开界面（推荐给用户看结果）

```bash
python src/server.py 8000
# 浏览器打开 http://127.0.0.1:8000
```

### 方式二：在对话里直接算

```bash
# 评估一套搭配（默认维持档）
curl -s -X POST http://127.0.0.1:8000/api/evaluate \
  -H "Content-Type: application/json" \
  -d '{"items":[{"name":"巨无霸","qty":1},{"name":"薯条","qty":1},{"name":"可乐","qty":1}],"mode":"weichi"}'
```

或直接调用引擎：

```python
import sys; sys.path.insert(0, "src")
import json, nutrition as N
menu = json.load(open("data/menu.json", encoding="utf-8"))
nutrition = json.load(open("data/nutrition.json", encoding="utf-8"))
cat = N.build_catalog(menu, nutrition)
items = [{"name": "巨无霸", "qty": 1}, {"name": "薯条", "qty": 1}, {"name": "可乐", "qty": 1}]
targets = N.targets_for("weichi", N.DEFAULT_MODES["weichi"]["targets"])
totals = N.totals_of(items, cat)
print(N.evaluate(totals, targets)["score"], N.suggest_swaps(items, cat, targets))
```

### 方式三：直接用连接器的 MCP 工具取数

需要实时菜单 / 营养表 / 门店时，优先调用 `mcd-mcp` 提供的工具：
`query-nearby-stores`、`query-meals`、`list-nutrition-foods`、
`query-my-coupons`、`available-coupons`、`calculate-price`。
注意 `query-meals` 需要 `beType=1, orderType=1, storeCode`。

## 输出约定

1. **先给结论**：分数 + 灯色（≥80 绿 / 55–79 黄 / <55 红），一句话说最严重的问题
2. **再给依据**：六项指标的数值与目标（含单位）
3. **然后给动作**：替换方案（带具体收益数值与价差）+ 补短板建议
4. **最后给直觉**：几碗米饭 / 几克盐 / 快走多少分钟
5. 涉及用户个人目标时，先确认或使用已保存的身体数据；不要猜测身高体重

## 红线

- 只谈麦当劳自己的餐品，**不做任何跨品牌对比**
- 不做医疗建议，不说教式劝退；只做客观呈现 + 给更优选择
- 不把 Token 写进任何文件或回复
