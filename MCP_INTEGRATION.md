# MCP 接入说明

本项目基于**麦当劳官方 MCP Server**（`https://mcp.mcd.cn`）开发，所有餐品、营养、价格、券数据均来自实时接口。

---

## 一、接入配置

```json
{
  "mcpServers": {
    "mcd-mcp": {
      "type": "streamablehttp",
      "url": "https://mcp.mcd.cn",
      "headers": { "Authorization": "Bearer ${MCD_MCP_TOKEN}" }
    }
  }
}
```

- 协议：**Streamable HTTP**，JSON-RPC 2.0
- `protocolVersion`：`2025-03-26`
- Token 申请：https://open.mcd.cn/mcp → 手机号登录 → 控制台 → 激活 → 复制
- 限流：**600 次/分钟**，超出返回 `429`

> 仓库中只提供带环境变量占位符的 `mcp-config.example.json`，不含任何真实凭证。

---

## 二、实际用到的 Tool

| Tool | 用途 | 调用时机 | 关键参数 |
|---|---|---|---|
| `query-nearby-stores` | 按城市/关键词找门店，拿到 `storeCode` | 启动引导、用户切换门店 | `beType=1`（到店）、`searchType=2`、`city`、`keyword` |
| `query-meals` | 拉门店实时菜单：分类、商品图、现价、原价 | 启动时一次，缓存复用 | `beType=1`、`orderType=1`、`storeCode` |
| `list-nutrition-foods` | 158 款在售餐品的官方营养值（能量/蛋白/脂肪/碳水/钠/钙） | 启动时一次，缓存复用 | 无 |
| `query-my-coupons` | 卡包里的券 —— 用于计算"替换会不会浪费券" | 启动时一次 | `page`、`pageSize` |
| `available-coupons` | 当前可领取的麦麦省券 | 启动时一次 | 无 |
| `calculate-price` | 含优惠的真实价格（可选增强，失败自动降级本地价目） | 校验总价时 | `storeCode`、`beType`、`orderType`、`items[]` |

---

## 三、调用流程

```
启动 server.py
   │
   ├─ 1. query-nearby-stores(city="上海", keyword="人民广场")
   │        └─ 取第一家门店的 storeCode（例：1450713）
   │
   ├─ 2. query-meals(beType=1, orderType=1, storeCode)
   │        └─ 分类 + 餐品（name / image / currentPrice / originalPrice）
   │
   ├─ 3. list-nutrition-foods()
   │        └─ 158 款营养表 → 与菜单按名称对齐，构建"可计算目录"
   │
   ├─ 4. query-my-coupons() + available-coupons()
   │        └─ 券信息进入替换算法的约束项
   │
   └─ 5. 用户每次评估：全部本地计算（判灯 / 打分 / 替换 / 补短板 / 累计）
            不产生新的 MCP 请求 —— 这是 600 次/分钟限流下的必要设计
```

**关键设计**：MCP 只用来**取数**，不用来算数。
一次启动只打 5 次接口，之后所有交互都在本地完成，因此：

- 响应是毫秒级的（不依赖网络往返）
- 结果可复现、可单测（`tests/selftest.py` 断言精确数值）
- 不会因为模型幻觉把热量算错

---

## 四、踩过的接口坑（供其他开发者参考）

1. **营养表不是 JSON**
   `list-nutrition-foods` 返回的外层是 JSON，但营养数据在 `data` 字段里以**一长串换行文本**存在，
   每行 9 个逗号分隔字段：`名称,描述,kJ,kcal,蛋白质,脂肪,碳水,钠,钙`。需要二次解析。
   见 `src/mcp_client.py: parse_nutrition_table`。

2. **响应可能是 SSE**
   部分响应为 `text/event-stream`，每行以 `data: ` 开头，解析前需剥掉前缀。

3. **菜单名与营养表名不同**
   菜单是 `薯条 / 可乐 / 玉米杯 / 麦咖啡美式`，营养表是 `中薯条 / 可乐中杯 / 小杯玉米杯 / 热美式中杯`。
   需要「归一化 + 双向包含 + 排除套餐名（三件套等）+ 默认规格优先取中杯」的匹配策略。
   见 `src/nutrition.py: match_nutrition`。当前在实时菜单上可对齐约 27–33 款可计算餐品。

4. **菜单按时段切换**
   早餐 / 正餐 / 下午茶 / 夜宵时段菜单不同，因此"菜单条数"不是固定值，
   自检里的实时校验用的是区间而不是精确数。

5. **限流**
   客户端对 `429` 做两次指数退避重试；同时通过"启动时批量取数 + 本地缓存"把请求量压到最低。

---

## 五、真实取数示例

`query-meals`（截取，`storeCode=1450713`）：

```json
{
  "success": true,
  "data": {
    "categories": [
      { "name": "巨无霸/牛鱼肉堡", "meals": [{ "code": "MS_1100" }, { "code": "MS_..." }] },
      { "name": "小食甜品/其他", "meals": [{ "code": "MS_G4437" }] }
    ],
    "meals": {
      "MS_1100": {
        "name": "巨无霸",
        "image": "https://menu-img.mcd.cn/pcm/prod/menu/20260309/product/MS_1100_320.png",
        "currentPrice": "25.5",
        "originalPrice": "25.5"
      }
    }
  }
}
```

`list-nutrition-foods`（`data` 字段内的文本，节选）：

```
巨无霸,null,2146,513,27,26,42,961,171
中薯条,null,1208,289,4,12,38,165,18
可乐中杯,null,616,147,0,0,36,0,10
```

> 上面这两段都可以用 `python tests/selftest.py` 复现验证：项目断言
> 巨无霸套餐（巨无霸 + 中薯条 + 可乐中杯）合计 949 kcal / 钠 1126mg / 得分 58。

---

## 六、业务价值

| 接口能力 | 变成用户能感知的东西 |
|---|---|
| `list-nutrition-foods` 的六维数据 | 红绿灯判读 + 具象换算（几碗米饭 / 几克盐 / 走多久） |
| `query-meals` 的真实商品图与现价 | 与官方小程序同级的「左图右文」餐盘，替换方案带真实价差 |
| `query-nearby-stores` | 门店切换，营养判读跟着门店菜单走 |
| `query-my-coupons` | 替换方案不平白浪费用户的券 |
| `calculate-price` | 建议方案的价格变化可核验（"采纳后反而便宜 ¥6.5"） |

没有 MCP，这个产品只能给出通用建议；有了 MCP，它能回答
**"在这家店、这个时段、按我这个身体数据、用我手里的券，这一顿该怎么点"**。
