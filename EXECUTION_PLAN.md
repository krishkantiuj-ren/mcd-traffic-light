# 麦麦营养红绿灯 · 执行计划（供 DeepSeek 执行）

> 本文档是**自包含**的执行规范：不依赖任何外部对话上下文。
> 目标：做出「2026 麦当劳程序员创意开发大赛」参赛项目并发布到 GitHub 公开仓库。
> 执行环境：Windows 10/11，工作区 `C:\Users\hi\WorkBuddy\2026-10-09-20-57-20\`。

---

## 0. 项目一句话

点餐时实时评估这顿饭的营养，用**红/黄/绿灯**标出哪项超标、哪项不够，给出**一键健康替换**方案，
并支持**个性化目标（身高体重自动计算）、跨餐累计、补短板推荐、优惠券不浪费**。
数据全部来自麦当劳官方 MCP 实时接口，界面参考 `design/mockup-v4.html`（已定稿，不要重新设计）。

**产品哲学（写 README 时照这个讲）**：取数交给 MCP，算术交给代码，表达交给 Agent。
不让大模型口算价格和营养——所有计算都是确定性算法。

---

## 1. 现状盘点（已完成，直接复用）

| 资产 | 路径 | 说明 |
|---|---|---|
| UI 设计定稿 | `design/mockup-v4.html` | V4.1 沉浸式红绿灯顶栏 + 三档切换 + 个性化定制弹层。**照它实现，别重设计** |
| 菜单数据快照 | `data/menu.json` | 101 款餐品：name/image/currentPrice/originalPrice/discountType，14 个分类 |
| 营养数据快照 | `data/nutrition.json` | 158 款餐品：energyKcal/protein/fat/carbohydrate/sodium/calcium |
| MCP 探针脚本 | `tools/mcp-probe.py` | 可工作的 MCP 客户端参考实现（JSON-RPC over streamable HTTP） |
| 官方参赛声明 | `mcd-traffic-light/CONTEST_DECLARATION.md` | **已就位，内容不可改动一个字** |
| 项目目录 | `mcd-traffic-light/` | 已创建 src/ web/ data/ assets/ |

Token 获取：从 `C:\Users\hi\.workbuddy\mcp.json` 的 `mcpServers["mcd-mcp"].headers.Authorization`
读取（形如 `Bearer xxx`）。**绝不写进任何项目文件、Git 提交或日志**。

---

## 2. 目标仓库结构

```
mcd-traffic-light/
├── README.md                  # 必须：项目介绍/安装/使用示例/目标用户
├── CONTEST_DECLARATION.md     # 必须：官方原文（已就位，勿动）
├── MCP_INTEGRATION.md         # 必须：用了哪些 MCP Tool、调用流程、业务价值
├── mcp-config.example.json    # 必须：脱敏配置，只写环境变量占位符
├── workbuddy.md               # 选填但要做：拿 3000 WorkBuddy 专项积分
├── SKILL.md                   # Agent Skill 规范（YAML frontmatter + 指令）
├── EXECUTION_PLAN.md          # 本文档（发布前可删）
├── .gitignore                 # 必须排除：.env、mcd_token.txt、data/day_log.json、__pycache__
├── .env.example               # MCD_MCP_TOKEN=your_token_here
├── src/
│   ├── mcp_client.py          # MCP 调用层（见 §4）
│   ├── nutrition.py           # 营养引擎（见 §5，全部纯函数，无 IO）
│   ├── daylog.py              # 跨餐累计存储（JSON 文件）
│   └── server.py              # 本地服务器（见 §6）
├── web/
│   └── index.html             # 真实 UI（按 mockup-v4.html 实现，接 /api）
└── data/
    ├── menu.json              # 从工作区 data/ 复制，作为离线兜底
    └── nutrition.json         # 同上
```

**技术选型**：Python 3.10+ **标准库 only**（http.server + urllib + json），零 pip 依赖——
评审 clone 下来 `python src/server.py` 就能跑。前端纯 HTML/JS，无构建步骤。

---

## 3. 已验证的 MCP 事实（不要重新摸索）

- 端点 `https://mcp.mcd.cn`，协议 **Streamable HTTP**，Header `Authorization: Bearer <TOKEN>`，
  `Content-Type: application/json`，`Accept: application/json, text/event-stream`。限流 **600 次/分钟**，超出返 429（客户端要做节流与退避）。
- 握手：`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"x","version":"1.0"}}}` 返回 200。
- 响应可能是 SSE 格式（每行 `data: {...}`），解析时**先剥掉 `data: ` 前缀再 json.loads**。
- `tools/call` 返回结构：`result.content[]`，取 `type=="text"` 的项拼接。
- **坑1**：`list-nutrition-foods`（无参数）的营养表**不是直接 JSON**——在外层 JSON 的 `data` 字段里
  嵌着一长串文本，每行 9 个逗号分隔字段：`名称,描述,kJ,kcal,蛋白质,脂肪,碳水,钠,钙`。
  需要二次解析（参考 tools/mcp-probe.py 与 data/nutrition.json 的生成方式）。
- **坑2**：`query-meals` 必填 `beType`(1=到店) `orderType`(1=到店) `storeCode`。
  返回 `categories`（14 个，含「500 大卡套餐」）+ `meals`（dict，key 为商品码），
  每项含 `name / image / currentPrice / originalPrice / discountType`。
  图片域 `menu-img.mcd.cn`，公网可直链（部分为 .gif）。
- `query-nearby-stores`：入参 `beType=1, searchType=2, city, keyword` → 拿 storeCode
  （例：上海人民广场 → `1450713` 华旭国际大厦店，可用作 demo 默认门店）。
- `query-my-coupons(page, pageSize)`：用户卡包的券。`available-coupons()`：可领取的券（无参）。
  `calculate-price(storeCode, beType, orderType, items[], ...)`：算真实价格。
  **这三个工具的返回字段未逐一验证，实现时先打印一次真实响应再写解析**（探针脚本可直接用）。

---

## 4. mcp_client.py 规范

```
class McpClient:
    __init__(token, base="https://mcp.mcd.cn")
    call(tool: str, args: dict) -> dict        # tools/call，返回解析后的 JSON
    list_tools() -> list
    fetch_nutrition_table() -> dict[name->dict] # 调 list-nutrition-foods，做二次解析
    fetch_menu(store_code, be_type=1, order_type=1) -> dict
    fetch_my_coupons() -> list                 # 失败返回 []，不抛异常
    fetch_available_coupons() -> list          # 同上
    calculate_price(store_code, items) -> dict # 失败返回 None
```

要求：urllib 实现；每次请求带超时(20s)；429 时 sleep 后重试最多 2 次；
菜单/营养结果写内存缓存（同一会话不重复拉）；Token 只从环境变量 `MCD_MCP_TOKEN`
或命令行参数读，**无 Token 时降级用 data/*.json 离线快照**（UI 显示「离线数据」角标）。

---

## 5. nutrition.py 规范（核心引擎，纯函数）

### 5.1 指标定义
```
METRICS = [kcal 能量(cap), protein 蛋白质(min), fat 脂肪(cap),
           carb 碳水(min), sodium 钠(cap), calcium 钙(min)]
cap = 越少越好（限制型）；min = 越多越好（达标型）
```

### 5.2 三档默认单餐目标（通用成人，未定制时用）
```
减脂: kcal500 protein28 fat17 carb40 sodium600 calcium267
维持: kcal700 protein25 fat23 carb50 sodium667 calcium267
增肌: kcal850 protein38 fat27 carb65 sodium700 calcium300
```

### 5.3 个性化目标（Mifflin-St Jeor，输入性别/年龄/身高cm/体重kg/活动系数）
```
BMR  = 10w + 6.25h − 5a + 5(男) / −161(女)
TDEE = BMR × 活动系数(久坐1.2 / 轻度1.375 / 中度1.55)
每日热量: 减脂 = max(1200, TDEE−400)；维持 = TDEE；增肌 = TDEE+300
蛋白质: 体重 × {减脂1.8, 维持1.2, 增肌1.8} g/日
脂肪:   每日热量 × {减脂0.22, 维持0.27, 增肌0.25} ÷ 9 g/日
碳水:   (每日热量 − 蛋白×4 − 脂肪×9) ÷ 4 g/日
钠/钙:  与体重无关，固定 2000mg / 800mg 每日
单餐目标 = 每日 ÷ 3
```

### 5.4 判灯与打分（必须与 mockup 完全一致，验收见 §9）
```
ratio = value / target
cap 型: ratio > 1.5 → 红; ratio > 1 → 黄; 否则绿
min 型: ratio >= 1 → 绿; ratio >= 0.7 → 黄; 否则红
score = max(0, 100 − 15×红数 − 6×黄数)
light = score>=80 绿 / >=55 黄 / 否则红
warn  = cap 型中 ratio>1 且最大者："钠超了 69%"
```

### 5.5 具象类比与运动换算（报告卡用，体重取 profile，默认 60kg）
```
米饭碗数 = kcal ÷ 256        (一碗米饭≈256kcal)
食盐克数 = sodium × 2.54 ÷ 1000
运动分钟 = kcal ÷ (METs × 体重kg ÷ 60)，快走 METs=4，慢跑 METs=8
```

### 5.6 替换算法（换品，multi-objective）
输入：当前餐盘 items[]、模式目标、菜单（含分类+营养+价格）、用户券列表。
对每个餐品，在**同分类**候选中枚举替换：
```
增益 = score(替换后餐盘) − score(替换前餐盘)
代价 = 替换后价格 − 替换前价格 + 券损失
券损失 = 若原商品被某张券覆盖而替换品不被覆盖 → 该券面值（粗略：券面额或按 discountType 估算）
约束: 代价 ≤ +5 元（可配置）；增益 > 0
输出: 按「增益 − 0.5×代价」排序的 Top 2 替换，每条带
      {from, to, fromImg, toImg, gains:["钠 −163mg"], priceDelta}
```

### 5.7 补短板（gap-fill，别漏做）
对 min 型指标中 ratio<1 的（蛋白/碳水/钙），从「饮品/小食/500大卡套餐」分类里找**单件加餐**：
```
排序键 = 补缺口量 ÷ 新增kcal（每卡路的修补效率），要求新增 kcal ≤ 200
输出 Top 1~2：{add, img, fills:"钙 +240mg（缺口 68mg→达标）", price}
```

### 5.8 跨餐累计（daylog.py）
`data/day_log.json`：`{"2026-10-09": {"meals": [{"ts","slot","items":[...],"totals":{...}}]}}`
- slot ∈ 早餐/正餐/下午茶（按本地时间自动归：5–10 早餐，10–16 正餐，其余下午茶）
- 提供 `today_remaining(targets_daily) -> dict`：每日目标 − 已吃合计 → 本餐余量
- 评接口返回里带 `day: {eaten, remaining, mealCount}`，UI 英雄卡下加一行
  「今日已吃 N 餐 · 钠 1540/2000mg · 本餐还可吃 460mg」

---

## 6. server.py 规范（stdlib http.server，端口 8000）

| 路由 | 方法 | 说明 |
|---|---|---|
| `/` | GET | web/index.html |
| `/api/bootstrap` | GET | `{store, menu(分类+餐品+图+价), nutrition, coupons:{mine, available}, offline:bool}` |
| `/api/evaluate` | POST | 入 `{items:[{name,qty}], mode, profile?, slot?}` → 出 `{totals, metrics:[{key,label,value,target,ratio,level}], score, light, warn, swaps[], gaps[], price:{before,after}, day}` |
| `/api/adopt` | POST | 入 `{items, swaps}` → 返回采纳后的新餐盘（前端仅切换显示用，可纯前端实现则省略） |
| `/api/day` | GET/POST | GET 今日累计；POST 记录一单 `{slot, items}` |
| `/api/coupons` | GET | 实时拉券（缓存 5 分钟） |

要求：JSON 响应带 `Access-Control-Allow-Origin: *`；启动时打印本机地址；
无 Token 自动离线模式（读 data/*.json），所有 API 仍可用（券返回空）。

---

## 7. web/index.html 实现规范

**照 `design/mockup-v4.html` 做**，它是定稿。差异只有：

1. 所有硬编码 MEALS/SWAP 数据 → 改为 `/api/bootstrap` + `/api/evaluate` 实时数据
2. 餐盘可增删（点「＋ 添加餐品」出分类选择器 → 餐品列表 → 加入餐盘，qty 可调）
3. 英雄卡下方加「今日已吃」条（§5.8）
4. 替换建议卡渲染 `/api/evaluate` 返回的 swaps + gaps（动态，不再是写死两条）
5. 券提示：若有券被替换浪费，在建议卡上用 amber 标注「会浪费 XX 券」
6. 保留：变色顶栏、三档切换、定制弹层、记分环动画、进度条目标刻度、报告卡
7. 门店可切换（搜索城市+关键词 → query-nearby-stores → 换 storeCode 重拉菜单）
8. 「采纳」后调 `/api/day` POST 记录，再刷新「今日已吃」

设计 token 不许改：红黄 `#DA291C/#FFC72C`、绿灯 `#2E8B1F`、琥珀 `#E08600`、
背景 `#F1EEE8`、字体栈、圆角、8px 网格。

---

## 8. 文档撰写要求

### README.md（评审第一眼，决定 star）
结构：① 一句话 + 效果截图/GIF（用 §10 的截图放 assets/）② 痛点 ③ 四大能力
（红绿灯判灯/个性化目标/智能替换+补短板/跨餐累计+券不浪费）④ 30 秒上手
（拿 Token → 配环境变量 → `python src/server.py` → 开浏览器）⑤ 判灯标准与公式
（抄 §5，注明依据《中国居民膳食指南2022》与 Mifflin-St Jeor）⑥ 目标用户 ⑦ 合规声明引用。
**禁令**：不得与其他品牌对比；不得承诺医疗建议；截图不得含真实 Token。

### MCP_INTEGRATION.md
表格列出实际用到的 Tool（至少：query-nearby-stores/query-meals/list-nutrition-foods/
query-my-coupons/available-coupons/calculate-price），每个写：用途/调用时机/关键参数；
画文字版调用时序；写清 600 次/分钟限流的应对；附一次真实调用的 traceId 截图或文本
（榜首项目靠「数据可验证」加分）。

### mcp-config.example.json
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

### workbuddy.md
说明本项目用 WorkBuddy 开发：mcd-mcp 连接器接入、Skill 形态、设计稿迭代过程（可放
design/ 的演进图）。真情实感写，别像广告。

### SKILL.md
YAML frontmatter（name/description）+ 使用指令：何时触发（点麦当劳/看营养/减脂点单）、
如何调 engine、输出格式约定。

---

## 9. 验收测试（✅ 已实装于 `tests/selftest.py`，32 项全部通过）

```bash
python tests/selftest.py          # 离线断言（读 data/ 快照）
python tests/selftest.py --live   # 追加真实 MCP 取数校验
```

实测值（来源：麦当劳官方营养表，非估算）：

```
T1 餐盘=巨无霸+薯条+可乐，维持档默认目标:
   totals = {kcal:949, protein:31, fat:38, carb:116, sodium:1126, calcium:199}
   levels = kcal黄 protein绿 fat红 carb绿 sodium红 calcium黄
   score = 58, light = 黄, warn = "钠超了 69%"

T2 智能替换（算法在真实菜单里穷举所得，非预设）:
   薯条 → 苹果片   收益 [钠 −165 mg, 热量 −257 kcal]   价差 −¥6.5
   采纳后 totals = {kcal:692, protein:27, fat:26, carb:85, sodium:961, calcium:199}
   score = 82, light = 绿

T3 个性化 profile={女,26,160cm,48kg,久坐}:
   BMR=1189, TDEE=1427；维持档单餐 {kcal:476, fat:14}
   同一餐盘 → fat 38g 判红, score = 49

T4 补短板: T2 采纳后钙 199mg（目标 267mg）→ 推荐「燕麦奶（钙 +438mg，达标）」

T5 跨餐累计: 记录两餐后 eaten.sodium = 1126+422 = 1548，剩余 452mg

T6 等价换算: 949 kcal ≈ 3.7 碗米饭 / 1126mg 钠 ≈ 2.9g 盐 / 快走 219 分钟

T7 实时取数: list-nutrition-foods 稳定 158 款；query-meals 返回实时菜单
   （菜单按时段切换，条数非固定，故用区间校验而非精确值）
```

**与原计划的偏差（以真实数据为准）**：
原 T2 预期「薯条→玉米杯 + 可乐→美式」是原型阶段的近似值。
真实营养表中**小杯玉米杯为 53 kcal / 钠 1mg**（原型写的是 87），且钙含量低于薯条，
会让钙指标跌破 70% 判红；算法因此选择了**苹果片**——同样降钠降热量，但不损失钙。
这正是"替换是算出来的、不是写死的"的证明。原规划值作废，以本节实测值为准。


---

## 10. 发布与报名（执行完代码后）

1. `git init` 在 mcd-traffic-light/，确认 `.gitignore` 生效（`git status` 无 .env/token）
2. 推到 GitHub 公开仓库，建议名 `mcd-traffic-light`
3. README 顶部放 2~3 张真实截图：用 Edge 无头模式截
   `"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --headless=new
   --screenshot=out.png --window-size=420,1140 --virtual-time-budget=9000 <url>`
   （必须带 `--virtual-time-budget`，否则远程商品图加载不出来）
4. 到 https://github.com/M-China/mcd-developer-innovation-challenge 按 Issue 模板提交报名
5. **越早发越好**：排名按 Star 累计，早一天多一天爬坡；后续功能用 commit 迭代补上

---

## 11. 执行顺序建议

```
1) src/mcp_client.py        → 用探针验证 5 个接口真实返回
2) src/nutrition.py + daylog.py → 跑通 §9 的 T1-T5（纯函数，命令行断言）
3) src/server.py            → T6 离线模式
4) web/index.html           → 对照 mockup 逐块实现
5) 五份文档 + 截图
6) git 发布 + Issue 报名
预计工作量：1 个整天。先做 1-3 保证能跑，UI 和文档可以后补。
```
