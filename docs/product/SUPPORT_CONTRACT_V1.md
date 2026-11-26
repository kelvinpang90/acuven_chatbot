# 客服接入契约 v1

> 版本：0.1（契约 `1.0` 的草案）｜2026-11-26｜状态：**草案，待 Kelvin 批准**。
>
> 这是业务系统（网店、CRM、ERP、Stock Core 及以后的项目）把能力开放给顾客侧 AI Chatbot 的约定。E-21 建议「客服侧不为单个系统写专门代码」，本契约就是那条唯一的接法。需求见 [REQUIREMENTS.md](REQUIREMENTS.md) R-3、R-5，设计见 [DESIGN.md](DESIGN.md) §7，待定项见 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)。
>
> v1 只承诺阶段 1b 网店样板用到的部分；CRM、ERP 接入（阶段 3）需要的新东西以 `1.x` 增量加入（§9）。

---

## 1. 角色

| 角色 | 是谁 | 在本契约里做什么 |
| --- | --- | --- |
| Chatbot | 合并后的顾客侧 AI Chatbot 的一个实例（Demo 或生产） | 拉能力清单、按规则调用能力、接收回调、渲染网页聊天窗口 |
| 业务系统 | 接入的系统的一个部署（如网店 Demo） | 提供能力清单与调用接口、签发网页身份令牌、提供渠道核验、在自己后台让有权人员确认高风险写入、回调 Chatbot |
| 租户 | 在 Chatbot 里用这个业务系统服务自己顾客的一方 | 打开或关闭每项能力、收紧风险级别（R-3.2） |

一次「接入」= Chatbot 实例里的一条 integration 记录（DESIGN §5.1）：一个租户 × 一个业务系统部署，带业务系统地址、两个方向的签名密钥、业务系统给这个租户的标识 `tenant_ref`。

## 2. 服务间签名

两个方向（Chatbot → 业务系统；业务系统 → Chatbot 的回调）都签名，**每个方向一把独立密钥**，每把支持新旧两把并存以便轮换（J-04 已是这个做法）。

建议直接采用 `ai_billing_hub` 的集成请求签名格式（见该仓库 `docs/api.md`「集成请求签名」），方案选择列为 Q-08：

- 请求头：`X-Acuven-Api-Key`（密钥标识，不是密钥本身）、`X-Acuven-Key-Version`、`X-Acuven-Timestamp`（Unix 秒）、`X-Acuven-Request-Id`（每次 HTTP 尝试都换新）、`X-Acuven-Signature`。
- 签名：HMAC-SHA256，小写十六进制；被签内容是 `ai_billing_hub` 定义的五行规范串（方法、规范化的路径与查询串、时间戳、Request-Id、原始请求体的 SHA-256）。具体规范化规则以该文档为准，本契约不另写一份。
- 时间偏差超过 300 秒拒绝；同一密钥下 Request-Id 在窗口内重复则拒绝（防重放）。
- 鉴权失败一律 `401`，不说明是哪一项失败。

选它而不是本仓库 J-04 现用的「时间戳 + 正文」两头格式的理由：本契约有多个路径，J-04 的签名不覆盖方法与路径，抓到的一个请求体在 5 分钟内可以被投到另一个路径；`ai_billing_hub` 的格式覆盖了这两样，还有 Python 接入包可以复用。J-04 现有的 CRM 接口在阶段 3 迁移前保持原样（§10）。

## 3. 能力清单

`GET {base_url}/acuven-support/v1/manifest`（签名，Chatbot → 业务系统）。Chatbot 在接入时、每次运营命令刷新时、以及每小时拉一次；清单校验不通过时保留上一份能用的并告警。

```json
{
  "contract_version": "1.0",
  "system": { "kind": "shop", "name": "Acuven Shop Demo", "version": "<提交 SHA 或版本号>" },
  "identity": {
    "webchat_token": true,
    "verify": ["shop_verify_by_order"]
  },
  "capabilities": [
    {
      "name": "shop_search_products",
      "title": { "en": "Search products", "zh": "搜索商品" },
      "description": "Search the catalogue by keyword. Returns up to 5 products with price and availability.",
      "risk": "read",
      "requires_customer": false,
      "input_schema": {
        "type": "object",
        "properties": { "query": { "type": "string", "maxLength": 100 } },
        "required": ["query"],
        "additionalProperties": false
      }
    },
    {
      "name": "shop_order_status",
      "description": "Status, items and shipping progress of one of the customer's own orders.",
      "risk": "read",
      "requires_customer": true,
      "input_schema": {
        "type": "object",
        "properties": { "order_no": { "type": "string", "maxLength": 32 } },
        "required": ["order_no"],
        "additionalProperties": false
      }
    },
    {
      "name": "shop_request_refund",
      "description": "Submit a refund request for items of the customer's own paid order. Staff review it in the shop admin.",
      "risk": "write_confirm",
      "requires_customer": true,
      "input_schema": { "...": "..." }
    }
  ]
}
```

字段：

| 字段 | 规则 |
| --- | --- |
| `contract_version` | 本契约版本，`1.x`；Chatbot 不认识主版本时拒绝整份清单 |
| `capabilities[].name` | `^[a-z][a-z0-9_]{2,47}$`，以系统前缀开头（`shop_`、`crm_`…），一个系统内唯一；名字一旦用过不改含义（改含义就换名） |
| `title` | 可选，多语言，给工作台与配置界面显示 |
| `description` | 给模型看的说明，英文，写清楚何时用、返回什么；不写「先问顾客」之类的流程要求——流程由 `risk` 决定（§5） |
| `risk` | `read`（只读）/ `write_low`（低风险写入）/ `write_confirm`（需人确认）。系统按 E-05 自己定级：退款、改价、调库存、取消已付款订单一类必须是 `write_confirm` |
| `requires_customer` | `true` 时只有对话带有效顾客身份才会被调用（§6） |
| `confirmable` | 可选，仅 `write_low` 有意义：`true` 表示这项能力也支持 §5.3 的「提出—确认」模式，租户才能把它收紧成 `write_confirm` |
| `input_schema` | JSON Schema 子集：顶层 `object`、`additionalProperties: false`；属性类型限 `string`（须有 `maxLength`）、`integer`、`number`、`boolean`、`enum`、元素为上述标量的 `array`（须有 `maxItems`）；不允许 `$ref`、不允许出现 §4.3 的保留字段名 |
| `identity.verify` | 该系统提供的渠道核验能力名（§6.2），这些能力的 `risk` 必须是 `read`，且 `requires_customer: false` |

## 4. 调用

### 4.1 请求

`POST {base_url}/acuven-support/v1/invoke/{capability_name}`（签名，Chatbot → 业务系统），超时默认 8 秒。

```json
{
  "contract_version": "1.0",
  "invocation_id": "0193b1f2-....",
  "tenant_ref": "acuven-shop-demo",
  "conversation": { "id": "c_...", "channel": "webchat", "locale": "zh" },
  "customer": { "subject": "member:1024", "verified_via": "webchat_token", "verified_at": "2026-11-26T08:00:00Z" },
  "input": { "order_no": "AS-20261126-0007" }
}
```

- `invocation_id`：UUIDv7，Chatbot 生成，是幂等键。
- `customer`：没有有效身份时为 `null`。`subject` 是业务系统自己的顾客标识（由令牌或核验得来，§6），Chatbot 原样回传，不解释。
- `input`：模型给出的参数，已按 `input_schema` 校验。

### 4.2 响应

业务结果一律 HTTP `200`，靠 `status` 区分：

| `status` | 含义 | 其它字段 |
| --- | --- | --- |
| `ok` | 完成 | `data`（给模型看的结果，JSON，≤ 16 KB）、可选 `display`（给顾客看的简短文字） |
| `pending_confirmation` | 已提出，等有权人员确认（只对 `write_confirm`） | `pending_id`、`summary`（具体变更的人话描述，会原样给顾客看）|
| `verified` | 核验成功（只对核验能力） | `subject`、可选 `display_name` |
| `error` | 业务上不成 | `error.code`、`error.message`（可给顾客看）、`error.retryable` |

`error.code` 取值：`INVALID_INPUT`、`NOT_FOUND`、`NOT_ALLOWED`（例如订单不属于这个顾客）、`CUSTOMER_REQUIRED`、`VERIFY_FAILED`、`CONFLICT`、`UNAVAILABLE`。

HTTP 层：`401` 签名失败；`404` 能力不存在；`5xx` 或超时 = 系统不可用。Chatbot 对 `5xx`/超时只在 `risk=read` 时重试一次；写入不自动重试，需要重试时用**同一个** `invocation_id`。

### 4.3 幂等与保留字段

- 业务系统对同一 `invocation_id` 至少 24 小时内返回同一结果，不重复执行；写入能力必须做到，只读能力建议做到。
- 保留字段名（不能出现在 `input_schema` 里，Chatbot 发现就拒绝整份清单）：`tenant`、`tenant_id`、`tenant_ref`、`customer`、`customer_id`、`subject`、`user_id`、`member_id`、`conversation_id`、`invocation_id`、`idempotency_key`。身份与租户只经 §4.1 的服务端字段传递，模型填不了（E-04、R-3.6、R-5.5）。
- 核验能力例外：它的输入就是顾客提供的凭据（如订单号与下单电话），由业务系统判断真伪；但它的输入里同样不能有上述保留字段。

## 5. 风险级别与执行规则

### 5.1 谁决定

业务系统在清单里定级；租户只能收紧（`read` 不能变成写，`write_low` 在 `confirmable: true` 时可收紧成 `write_confirm`，任何能力都可以关掉），不能放宽（R-3.2）。收紧与关闭立即生效并记录（R-3.5）。

### 5.2 执行规则

| 级别 | Chatbot 做什么 |
| --- | --- |
| `read` | 模型提出后直接调用 |
| `write_low` | 调用前要顾客在对话里明确同意本次具体操作。由代码强制（发出确认选项，顾客确认后才调用）还是只靠提示词，见 Q-19 |
| `write_confirm` | 只能「提出」：调用后业务系统返回 `pending_confirmation`，Chatbot 把 `summary` 告诉顾客「已提交，等工作人员确认」；结果经 §7.1 回调回来再告知顾客 |

### 5.3 需人确认：谁确认、在哪确认

建议（Q-07）：**在业务系统自己的后台确认，由业务系统按自己的权限判断谁是「有权人员」**。理由：

- 有权人员、审批权限、审计都在业务系统里，Chatbot 不需要复制一套角色权限；
- 网店现成就有「退款申请 → 管理员审核批准或拒绝」（`acuven-shop/docs/REQUIREMENTS.md`「访客与会员流程」第 6 条），样板可以直接接上。

流程：

1. 模型提出 `write_confirm` 能力 → Chatbot 调用（带 `invocation_id`）。
2. 业务系统建一条待确认单（展示具体变更：哪张单、哪些商品、多少钱），返回 `pending_confirmation` 与 `pending_id`。
3. 有权人员在业务系统后台批准或拒绝；业务系统执行或不执行，写自己的审计。
4. 业务系统回调 Chatbot（§7.1），Chatbot 通知顾客。WhatsApp 24 小时窗口已过时，按模板白名单发通知；没有可用模板就记在对话里，等顾客下次来时告知，并在工作台显示。
5. Chatbot 的工作台只显示待确认单的状态，不提供确认按钮（DESIGN §8.4）。

## 6. 顾客身份

### 6.1 网页聊天窗口：宿主系统签发的短时效令牌

- 宿主系统**后端**签发 JWT（建议 HS256，密钥 = 该网页渠道的身份令牌密钥，按渠道分发、支持新旧两把并存）。前端不持有密钥。
- 必需声明：`iss` = 网页渠道的 `site_key`；`aud` = `acuven-chatbot`；`sub` = 宿主系统里的顾客标识（即 §4.1 的 `customer.subject`）；`iat`；`exp` ≤ `iat` + 600 秒；`jti`。可选：`name`、`locale`。
- Chatbot 校验签名、`iss`/`aud`、过期、`jti` 不重复使用，然后把顾客身份绑到这次聊天会话；令牌快过期前聊天窗口向宿主系统再要一个。
- 顾客在宿主系统登出：宿主前端调用聊天窗口的 `logout()`，聊天会话的身份立即清除（之后匿名）。
- 没有令牌 = 匿名访客：只能用 `requires_customer: false` 的能力（R-5.2）。

### 6.2 渠道（WhatsApp）：业务系统提供的核验能力

业务系统在 `identity.verify` 里列出核验能力，Chatbot 在顾客要用 `requires_customer` 能力而对话未核验时引导核验。v1 定义两种形态，业务系统选一种或两种都提供，v1 先做哪种见 Q-05：

- **凭据核验（challenge）**：顾客在对话里提供该系统现成的查询凭据，业务系统判断真伪并返回 `verified` 与 `subject`。网店的例子：订单号 + 下单电话（网店现有的游客查单方式）。手机号单独不能作为凭据（B-05）；WhatsApp 发送方号码也不能被当作凭据之一自动填入。
- **登录链接（login_link）**：Chatbot 调用核验能力得到一个一次性链接（业务系统生成，绑定 `invocation_id`，10 分钟有效），发给顾客；顾客在业务系统网页上登录（用该系统已有的登录与二次验证）；业务系统经 §7.2 回调把 `subject` 交给 Chatbot。

失败次数限制：同一对话每小时最多 5 次核验失败，超过后本对话不再接受核验、建议转人工；业务系统自己也应限流。

### 6.3 有效期与范围

- 核验结果只绑定**本次对话**，不绑定手机号、不跨对话、不跨渠道生效（B-05、R-5.4）。
- 过期规则见 Q-06（建议：距最后一条消息空闲 30 分钟或核验后 4 小时，先到为准）。过期后再用需身份的能力要重新核验。
- 网页令牌身份随令牌刷新延续，宿主系统停止续签即失效。
- 「同一顾客的跨渠道对话显示在一起」（R-1.4）只用于人工接手界面的显示，不授予任何能力调用。

## 7. 回调（业务系统 → Chatbot）

地址前缀：`https://{chatbot 实例域名}/acuven-support/v1/callbacks/`，用业务系统 → Chatbot 方向的密钥签名（§2）。Chatbot 按 `callback_id` 去重，重复回调返回同一结果；成功 `200`，签名失败 `401`，Chatbot 不可用时业务系统按退避重试至少 24 小时。

### 7.1 确认结果

`POST .../callbacks/confirmation`

```json
{ "callback_id": "…", "invocation_id": "…", "pending_id": "…",
  "outcome": "approved", "message": "Refund of RM 59.00 approved." }
```

`outcome`：`approved` / `rejected` / `expired`；`message` 可给顾客看。

### 7.2 核验完成（登录链接形态）

`POST .../callbacks/identity`

```json
{ "callback_id": "…", "invocation_id": "…", "subject": "member:1024", "display_name": "Tan" }
```

Chatbot 检查 `invocation_id` 属于一次未过期、未用过的登录链接核验，然后把身份绑到那次对话。

## 8. 网页聊天窗口嵌入

```html
<script src="https://{chatbot 实例域名}/widget/v1/loader.js" async
        data-site-key="ws_xxx"></script>
<script>
  window.AcuvenChat = window.AcuvenChat || { q: [] };
  // 宿主系统后端签发 §6.1 的令牌；未登录时返回 null，聊天窗口以匿名方式运行
  AcuvenChat.q.push(["identityProvider", async () => {
    const r = await fetch("/api/support/chat-token", { credentials: "include" });
    return r.ok ? await r.text() : null;
  }]);
</script>
```

- 聊天窗口在 Chatbot 域名下的 iframe 里运行，宿主页面拿不到对话内容与 Chatbot 的会话凭据；宿主页面只经 `postMessage` 传令牌与 `logout`。
- Chatbot 只接受来自渠道「允许的来源域名」的嵌入与请求（`Origin` 校验、`frame-ancestors`）。
- 匿名访客按来源 IP 与浏览器会话限流；超过限额提示稍后再试。
- 宿主系统需要做的：签发令牌的后端接口；页面里加上面两段；在 CSP 里允许 Chatbot 域名的 `script-src` 与 `frame-src`。

## 9. 版本

- `contract_version` 为 `主.次`。次版本只加可选字段、新错误码、新能力形态；双方都必须忽略不认识的字段。主版本变化要两边同时升级。
- 每次契约修订在本文件头部记版本与批准情况；已登记的系统侧任务引用它当时依据的版本。

## 10. 现有 CRM、ERP 客户端怎么逐步改成按契约对接

现状（DESIGN §2、§4）：零售机器人直接用 ERP、CRM 的管理员邮箱密码换 JWT 调它们的业务接口（`services/api_client.py`、`erp_client.py`、`crm_client.py`），身份只认渠道号码；CRM 收件箱走 J-04 的专用签名接口。

迁移按「对方先提供、我方再切换、最后删除」三步走，每个系统单独进行（阶段 3）：

1. **对方提供契约端点**（在 `erp_os`、`acuven_crm_demo` 仓库登记任务）：把现在零售机器人用到的 11 个工具（9 个 `erp_*`、`crm_lookup_customer`、`crm_create_lead`）按本契约声明成能力，定好风险级别（例如开单、开电子发票、开红字发票按 E-05 判断是否 `write_confirm`），实现调用接口与核验能力；不再需要给 Chatbot 一个管理员账号。
2. **Chatbot 切换**：Demo 租户为零售机器人接上这两个系统的 integration，机器人配置从本地工具名换成契约能力名；跑完零售剧本的真机演示。Q-09（Demo 的「手机号即身份」）在这一步按决定处理。
3. **删除**：删掉 `tools/erp.py`、`tools/crm.py`、`services/erp_client.py`、`services/crm_client.py` 及其配置项与测试。

CRM 收件箱（J-04）：事件推送与回复接口在第 1 步之后改为按本契约 §2 的签名格式、挂在 `/acuven-support/v1/` 下的「接手」扩展（契约 `1.x` 增加），两边同时切换；切换前现有 `/internal/crm/*` 与 `/internal/chatbot/events` 原样保留。

Stock Core（含原 Inventory 的平台渠道能力）的接法等它深度融合（C-21）后期再定，范围见 Q-24。

## 11. 业务系统接入清单

- [ ] 实现 `GET /acuven-support/v1/manifest`，能力定级符合 E-05
- [ ] 实现 `POST /acuven-support/v1/invoke/{name}`，写入能力按 `invocation_id` 幂等
- [ ] 验 Chatbot 的签名、对 Chatbot 的回调签名，两把密钥都支持新旧并存
- [ ] `write_confirm` 能力：后台有待确认单的列表与批准/拒绝，确认后回调
- [ ] 网页聊天：签发令牌的后端接口、嵌入脚本、CSP
- [ ] 渠道核验：至少一种核验能力，带失败限流
- [ ] 在自己的仓库登记以上任务；Chatbot 侧只需运营命令接入，不改代码
