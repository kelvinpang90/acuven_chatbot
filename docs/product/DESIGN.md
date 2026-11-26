# 顾客侧 AI Chatbot：设计

> 版本：0.1｜2026-11-26｜状态：**草案，待 Kelvin 批准**。批准前不登记任务、不改代码。
>
> 需求见 [REQUIREMENTS.md](REQUIREMENTS.md)（R-x.y、N-x），接入契约见 [SUPPORT_CONTRACT_V1.md](SUPPORT_CONTRACT_V1.md)，待决定问题见 [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md)（Q-nn）。文中「现状」都指 2026-11-26 `origin/master`（`af6289f`）与 `acuven_aichat` 当前代码，给出文件与行号的地方按当时核对。
>
> 写法约定：「建议」= 本设计的提议，批准后生效；「待定」= 列在 Q-nn，不替 Kelvin 选。

---

## 1. 总览

```
                    ┌──────────────────────────── 同一镜像 ────────────────────────────┐
  顾客 ── WhatsApp ─┤                                                                   │
  顾客 ── 网页窗口 ─┤  入站 → 渠道解析(租户) → 白名单/管理指令 → 接手状态 → 机器人回复   │
                    │                              │                │                  │
                    │                    人工接手通道(三选)     能力调用(接入契约)       │
                    │                   ├ 手机 App 回音         ├ 本地工具(仅 Demo)      │
                    │                   ├ CRM 收件箱            └ 业务系统(网店/CRM/…)   │
                    │                   └ 自带工作台                                     │
                    │                                    用量 outbox → ai_billing_hub    │
                    └───────────────────────────────────────────────────────────────────┘
        Demo 实例：CHATBOT_ENV=demo（chatbot.acuventech.com，行业演示 + 网店样板）
        生产实例：CHATBOT_ENV=production（aicustomer.acuventech.com，Acuven 租户起步）
```

核心变化只有三件：

1. **从「一个号、一套凭据、一组 JSON 机器人」变成「租户 → 渠道 → 机器人」**，入站先按渠道找到租户再往下走（§5）。
2. **能力从「本仓库里写死的 ERP/CRM 客户端」变成「业务系统按契约声明、租户打开」**（§7、契约）。现有写死的客户端只留在 Demo 实例，阶段 3 逐步换掉。
3. **人工接手从「导演台 + CRM」两处各写一套，变成一个状态机 + 可替换的接手通道**（§8），aichat 的回音接管是其中一个通道。

## 2. 两份代码的能力对照

| 能力 | `ai_chatbot_demo`（现状） | `acuven_aichat`（现状） | 合并后 |
| --- | --- | --- | --- |
| Webhook 验签 | `X-Hub-Signature-256`，单个 app secret | 同左，secret 为空一律拒绝 | 按渠道所属 Meta App 的 secret 验（§5.2） |
| 去重 | 进程内存，要求单进程（`session_store.py`） | Redis `SET NX`，600 秒 | 用 aichat 的 Redis 做法，键加实例前缀 |
| 每号每日限额 | 进程内存 | 无 | 移到 Redis，按渠道配置 |
| 识别发送方 | 手机号，无号时 BSUID | 手机号 | 沿用 demo（含 BSUID） |
| 多号码 | 无：入站不读 `metadata.phone_number_id`，发送读 `settings.whatsapp_*` | 无：单号，按 `pnid` 过滤 | 按 `phone_number_id` 路由到渠道 |
| 机器人 | 5 个行业 JSON，菜单选择，可指定模型、工具 | 1 个，提示词硬编码 + `company.json` | 租户的机器人配置；Demo 仍可菜单选 |
| 模型调用 | 异步，tool runner 最多 8 轮，prompt cache | 同步，单轮，600 tokens | 用 demo 的 `services/llm.py` |
| 工具 | ERP、CRM、行业本地后端、转人工，写操作的「先问顾客」只写在说明里，无幂等键 | 无 | 本地工具只在 Demo；新能力走契约（§7） |
| 媒体 | 图片、语音转写、PDF、文件 | 只文本，其它回「请打字」 | 沿用 demo |
| 会话存储 | Redis `chat:user:{号}`，7 天，20 轮上下文 | Redis `aichat:history:{pnid}:{号}`，40 条，24 小时 | 键加渠道维度（§9） |
| 对话记录 | MySQL `chat_messages`/`tool_calls`/`model_usage`（无租户字段） | 无（只有原始同步数据落盘） | MySQL 对话表加租户、渠道（§9） |
| Coexistence | 无 | 订阅 `smb_message_echoes`、`history`、`smb_app_state_sync`，同步字段在号码过滤与白名单之前处理 | 搬入（§3） |
| 人工接手 | 关键词、转人工工具、导演台、CRM 回复；状态在用户档案里，空闲 24 小时 | 回音 → `auto`（24 小时滑动）、`#hold`（30 天）、`#bot` 交还 | 合成一个状态机（§8） |
| 管理指令 | 无（导演台是网页） | `ADMIN_PHONES` 发 `#list`/`#hold`/`#bot` | 搬入，按渠道配管理号码 |
| 白名单 | 无（demo 对所有人开放） | `AI_REPLY_ALLOWLIST`：空=不回，`*`=所有人 | 搬入，按渠道配；Demo 渠道配 `*` |
| 原始数据落盘 | 无 | `{field}-{日期}.jsonl`，无保留期 | 搬入，加保留期（§11） |
| CRM 对接 | J-04：签名事件推送、CRM 回复、模板（`crm_events.py`、`internal_crm.py`） | 无 | 保留，作为「CRM 收件箱」接手通道（§8） |
| 导演台、演示数据重置、行业后台 | 有 | 无 | 只在 Demo 实例（§4） |
| 网页聊天 | 只给运营者：要导演台令牌、按手机号冒充顾客（`routers/chat.py`） | 无 | 新增可嵌入聊天窗口（契约 §8）；运营者网页聊天只在 Demo |
| 计费 | 本地按硬编码美元单价估算，不上报 | 无 | 上报 `ai_billing_hub`（§10） |
| 部署 | GHCR + SSH，主机锁；健康检查不报 SHA；无 CI 测试；无 `.platform/` | GHCR 私有镜像；健康检查报 `GIT_SHA` | 按模板补齐（§12 阶段 0） |

## 3. 从 aichat 搬什么、不搬什么

| 搬 | aichat 现状（`acuven_aichat/app/`） | 合并后落点 | 按什么打开 |
| --- | --- | --- | --- |
| Coexistence 同步字段处理 | `coexistence.py`、`routers/whatsapp_webhook.py:71-82` | WhatsApp 入站的一个分支：同步类变更先于号码过滤与白名单处理 | 渠道 `coexistence=true` |
| 回音接管 | `handover.py`、`session_store.py:146-183`；自回音防护 `aichat:sent:{wamid}` | 接手通道「手机 App 回音」（§8.3）；状态并入统一状态机 | 渠道 `coexistence=true` 时总是生效（R-4.3） |
| 管理指令通道 | `admin.py`：`#list`、`#hold <编号或号码>`、`#bot <编号或号码>`；最近会话 ZSET | 渠道的管理指令处理，判断在白名单之前 | 渠道 `admin_phones` 非空 |
| 白名单 | `config.py:55-66,86-93`；白名单外不写历史、不改状态 | 渠道 `reply_allowlist` | 每个 WhatsApp 渠道都有（Demo 渠道配 `*`） |
| 历史落盘 | `coexistence.py:38-49`：`/data/coexistence/{field}-{YYYYMMDD}.jsonl`，写失败回 500 让 Meta 重投 | 同样落盘，目录按实例与渠道分；加保留期清理（Q-16） | 渠道 `coexistence=true` |
| 接入脚本 | `scripts/onboard.py`：`exchange`（换 token、订阅 WABA）、`sync`（触发同步）、`check` | 运营命令：写入渠道记录与加密凭据（§5.4） | 运营者手动执行 |
| 公司知识 | `data/company.json`、`services/llm.py:26-54` 的提示词 | Acuven 租户的机器人配置，**存库不进仓库**（R-8.3） | — |

**不搬**：aichat 的 `services/llm.py`（同步单轮，demo 的更完整）、`services/whatsapp.py`（demo 已有发送、媒体、Markdown 处理）、`session_store.py` 里与 demo 重复的历史与去重实现（只取它的 Redis 去重做法）。aichat 的需求文档里列了但没实现的（出站节流、写 CRM 线索、多人分配、媒体理解、客服后台）不在搬运范围；其中媒体理解合并后自动具备，客服后台由阶段 2 的工作台承担。

**aichat 归档时点**：建议在阶段 1a 上线、Acuven 客服在生产实例跑稳后归档（README 指向新仓库）；归档前 `aicustomer.acuventech.com` 的 VPS 容器停用、域名改指生产实例。aichat 的 Meta App（`Acuven Customer Service`，Embedded Signup 配置已建）沿用为生产实例的 App（Q-22）。

## 4. 实例与 Demo 开关

照 Stock Core 的写法（`acuven_stock_core/docs/CONTRACT_V1.md` §6.1）：

- **`CHATBOT_ENV=demo|production`**，启动时读，运行中不能改；库里 `chatbot_instance` 表记一份，两者不一致就拒绝启动、拒绝迁移。没有默认值：不设拒绝启动（防止生产实例忘了设而以 Demo 方式运行）。
- 下表「只在 Demo」的功能，在 `production` 下**不挂路由、不注册工具、不跑任务**；对应配置项在 `production` 下被设置时拒绝启动（R-7.3）。

| 功能 | 现状位置 | 实例 |
| --- | --- | --- |
| 五个行业机器人与菜单选择 | `bots/data/*.json`、`whatsapp_webhook.py` `_send_bot_list` | 仅 Demo |
| 行业本地后端与 `/admin` | `verticals/`、`tools/local.py`、`tools/food.py`、`tools/realestate.py` | 仅 Demo |
| 直连 ERP/CRM 的工具（邮箱密码换 JWT，全局账号） | `tools/erp.py`、`tools/crm.py`、`services/erp_client.py`、`services/crm_client.py` | 仅 Demo，阶段 3 换成契约后删除 |
| 导演台：SSE、工具总开关、故障演练、自动演示、演示摘要、历史 | `routers/console.py`、`console/` | 仅 Demo |
| 运营者网页聊天（按手机号冒充顾客） | `routers/chat.py` 的 `/api/chat/*` | 仅 Demo |
| 现有前端：导演台、行业后台 `/admin`、运营者聊天页 | `frontend/src/pages/` | 仅 Demo；生产实例不部署现有前端容器，网页聊天窗口在 1b 另做 |
| 演示数据种子与每晚重置 | `tasks/seed_*.py`、`tasks/cleanup.py` | 仅 Demo（H-01） |
| 零售机器人「手机号即身份」查 ERP | `tools/erp.py:363-412` | 仅 Demo，与 B-05 的冲突见 Q-09 |
| CRM 收件箱（J-04） | `crm_events.py`、`internal_crm.py`、`crm_send.py`、`crm_templates.py` | 两个实例都有：它是接手通道之一（§8），Demo 里接 `acuven_crm_demo` |
| 企业微信渠道、已停用网关入口 `/internal/whatsapp/inbound` | `routers/wecom_webhook.py`、`routers/internal_whatsapp.py` | 待定（Q-18） |
| Coexistence、回音接管、管理指令、白名单、落盘 | 从 aichat 搬入 | 两个实例都有，按渠道配置 |
| 网页聊天窗口、接入契约、工作台、计费上报 | 新增 | 两个实例都有 |

**两个实例的部署**（建议，细节见 Q-13、Q-14）：

| | Demo 实例 | 生产实例 |
| --- | --- | --- |
| 域名 | `chatbot.acuventech.com`（不变） | `aicustomer.acuventech.com`（aichat 的 Meta App 回调已指向它） |
| Meta App | 现 demo 号所在 App（`Acuven Connect`） | `Acuven Customer Service`（aichat 的 App） |
| Redis | `infra_redis` db16（不变） | 沿用 aichat 的 db17 |
| MySQL | `ai_chatbot`、`ai_chatbot_verticals`（不变） | 新库（名称随 Q-01） |
| 计费项目 | 内部租户下的 Demo 项目（F-04） | 每个租户按「Chatbot × 模块」（§10） |
| 部署 | 合并到主分支即按 SHA 部署 | 同一工作流、同一 SHA，节奏见 Q-12 |

## 5. 租户模型

### 5.1 实体

```
tenant ─┬─ meta_app_link ── meta_app（Meta App：app_id、app_secret、verify_token；按实例共用）
        ├─ channel ──────── whatsapp：phone_number_id、waba_id、显示号码、业务 token、coexistence、
        │     │                       reply_allowlist、admin_phones、每日限额、接手方式
        │     │             webchat ：site_key、允许的来源域名、身份令牌密钥（新旧两把）、接手方式
        │     └─ bot（一个渠道接一个；Demo 的 demo 号例外：bot_selection=menu）
        ├─ bot ──────────── 人设、知识（文档/结构化 JSON）、模型、语言、sequential_tools、
        │                   可用能力（引用 integration 的能力名）、本地工具（仅 Demo）
        ├─ integration ──── 业务系统：kind、base_url、两个方向的签名密钥（新旧各一把）、
        │                   缓存的能力清单（含版本）、租户对每项能力的开关与收紧
        ├─ handover_config  默认接手方式（粒度见 Q-03）、工作台账号（阶段 2）
        ├─ billing_link ─── billing 租户 id、每个模块的项目 id 与项目凭据（§10）
        └─ audit_log ────── 配置变更记录（谁、何时、改了什么；R-3.5）
```

字段说明：

- **tenant**：`id`、`name`、`kind`（`internal` / `customer` / `demo`，只影响计费模式与 Demo 校验，R-2.2）、`status`（`active` / `suspended` / `closed`）。`demo` 租户只能存在于 Demo 实例。
- **meta_app**：一个 Meta App 只有一个 webhook 回调地址，所以一个 App 只能服务一个实例；实例里所有经该 App 接入的号码共用它的 `app_secret` 验签。用 Embedded Signup 接入的号码，业务 token 属于这个 App（Acuven 以 Tech Provider 身份持有）。
- **channel**：WhatsApp 渠道的 `phone_number_id` 全实例唯一（入站路由键）；网页渠道的 `site_key` 是公开标识，可以写在网页上。
- **bot**：现有 `BotConfig`（`bots/registry.py`）的字段原样保留，加 `tenant_id` 与「可用能力」。
- **integration**：每个租户接一个业务系统一条；同一个网店系统被两个租户用，就是两条，各有密钥。

### 5.2 运行时解析

1. **WhatsApp 入站**：按 Meta 推送里 `entry[].changes[].value.metadata.phone_number_id` 找渠道 → 用该渠道所属 Meta App 的 secret 验签。实现上先读出原始 body 里的 `phone_number_id` 再验签，验签通过前不做任何写入；同一请求里出现不属于同一 App 的号码时整批拒绝。找不到渠道的号码：回 200（避免 Meta 重投）、不处理、记日志（N-5）。
2. **网页入站**：按 `site_key` 找渠道 → 校验 `Origin` 在允许列表 → 校验身份令牌（契约 §6.1）。
3. 渠道 → 租户 → 机器人 → 「可用能力 = 系统声明 ∩ 租户打开 ∩ 机器人配置」（R-3.3）。
4. **发送**：用渠道自己的 `phone_number_id` 与业务 token，不再读全局 `settings.whatsapp_*`（现状 `services/whatsapp.py:79,85`、`services/whatsapp_media.py:41,45`）。
5. 所有 Redis 键、对话表、用量记录都带渠道（从而带租户）；同一手机号在两个渠道是两个顾客记录（R-2.3）。

### 5.3 存储与密钥

- 配置存在实例自己的 MySQL。建议新表用 **Alembic** 管理（与模板、网店、Stock Core 一致）；现有 `audit.py`、`verticals/` 的 `CREATE TABLE IF NOT EXISTS` 不动（Q-15）。
- 密钥（业务 token、app secret、签名密钥、计费项目凭据、令牌密钥）在库里加密存放，主密钥来自环境变量 `CHATBOT_SECRETS_KEY`；支持换主密钥（新旧两把并存期间重新加密）。日志与导出不出现明文。
- 配置读取按请求进行，允许最多几秒的进程内缓存，保证「收紧立即生效」（R-3.5）。

### 5.4 配置方式（首期由 Acuven 人员操作，R-8.1）

- 运营命令（容器里执行的 CLI）：建租户、加 Meta App、接入号码（合并 aichat 的 `onboard.py exchange/sync/check`，结果直接写进渠道记录）、加网页渠道、导入机器人配置（从仓库外的文件导入，R-8.3）、加业务系统并拉取能力清单、开关能力、设接手方式。每条命令写 `audit_log`。
- 阶段 4 的客户自助后台调用同一套服务层，不另写一套逻辑。

### 5.5 Demo 租户

Demo 实例迁移时生成：一个 `demo` 租户「行业演示」（demo 号渠道，`bot_selection=menu`，五个行业机器人从 `bots/data/*.json` 导入，白名单 `*`，接手方式 CRM 收件箱 + 导演台）；阶段 1b 再加一个 `demo` 租户「网店样板」。Demo 机器人的 JSON 继续留在仓库（本来就公开、是虚构业务）。

## 6. 三种交付形态

| 形态 | 怎么部署 | 与本设计的关系 |
| --- | --- | --- |
| 多租户 SaaS | 生产实例，多个租户；Acuven 是其中一个 | 默认形态，阶段 1a 起 |
| Acuven 托管专属 | 同一镜像另起一个实例（独立库、Redis、域名），里面一个租户 | 只是多一份部署配置；Meta App 怎么接见 Q-10 |
| 客户自托管 | 客户在自己机器上跑同一镜像与同一版本 | 同上；计费见 Q-11；C-14：客户机器不接成 SaaS 租户 |

约束：一个 Meta App 只有一个回调地址（§5.1），所以经 Acuven 的 App 接入的号码只能落在 Acuven 的多租户生产实例上。专属与自托管实例要么用客户自己的 Meta App，要么由多租户实例转发（与 J-01 停用网关的方向相反）——这会影响阶段 3 的工作量与客户开户流程，列为 Q-10，不在本设计里替 Kelvin 选。

## 7. 能力、工具与执行

- **两种工具来源**：①本地工具（现有 `tools/*`），只在 Demo；②契约能力，由业务系统的能力清单生成模型工具定义（名称、说明、输入 JSON Schema）。模型看到的工具名带系统前缀（如 `shop__order_status`）。
- **执行**全部在应用层（E-04）：模型提出调用 → 应用按能力的风险级别与身份要求放行或拦截 → 签名调用业务系统 → 结果回给模型。顾客身份、租户、对话 id、幂等键由应用加进请求，不在模型可填的输入里（契约 §4.3）。
- **风险级别的执行规则**（契约 §5）：
  - 只读：直接执行。
  - 低风险写入：执行前要顾客在对话里明确同意。是由代码强制（发确认按钮、顾客点「确认」才调用），还是像现在只写在提示词里，见 Q-19。
  - 需人确认：只能「提出」，业务系统返回待确认单号，有权人员在业务系统里确认后回调 Chatbot，再通知顾客（Q-07）。
- **身份要求**：能力 `requires_customer=true` 时，对话没有有效的顾客身份（网页令牌或渠道核验）就不调用，改为引导登录或核验（R-5.2、R-5.3）。
- **失败**：超时（默认 8 秒）、5xx、签名错误都如实告诉模型「系统暂时不可用」，由机器人告知顾客并可转人工；不重试写入，除非同一幂等键（N-7）。
- **现有 Demo 工具的过渡**：零售机器人继续用直连 ERP/CRM 的工具，直到阶段 3 ERP、CRM 按契约提供能力后切换，切换后删除 `services/erp_client.py`、`services/crm_client.py`（契约 §10）。

## 8. 人工接手

### 8.1 一个状态机

同一对话同一时刻只有一个接手状态（R-4.6），字段：`state`（`ai` / `requested` / `human`）、`via`（谁触发：顾客要求、转人工工具、手机 App 回音、CRM 回复、工作台、导演台、管理指令）、`since`、`last_human_at`、`expires_at`、`hold`。

- **进入**：顾客要人工（现有关键词与 `request_human_help` 工具）→ `requested`；同事任何一次回复（App 回音、CRM、工作台、导演台）→ `human`，`last_human_at` 更新。
- **计时**：`human` 状态下空闲（距最后一次同事动作）超过 24 小时自动回到 `ai`（现状 `HANDOVER_IDLE_SECONDS`，J-04 D2；aichat 也是 24 小时滑动）。`hold`（管理指令 `#hold`）把期限设为 30 天且不被新回音缩短（aichat `HANDOVER_HOLD_TTL_SECONDS`）。`requested` 的超时沿用现有 `services/handover.py` 的规则，阶段 0 不改行为。
- **退出**：超时；同事交还（CRM handback、工作台「交还」、对话里发 `#bot`、管理指令 `#bot`）；Demo 里访客发 `menu`。
- 状态从现在的用户档案字段（`handover_since`、`handover_active_at`）与 aichat 的 `aichat:handover:{pnid}:{号}` 合成一份，放在对话记录上；每次变化发一个内部事件，各通道按需订阅（CRM 通道转成现有的 `handover_changed` 推送）。

### 8.2 接手通道接口

每个通道实现同一组操作，换通道不改状态机：

| 操作 | 说明 |
| --- | --- |
| `on_requested(conv)` | 顾客要人工时通知该通道（CRM：推事件；工作台：列表里置顶；手机 App：可选给管理号码发提醒） |
| `on_customer_message(conv, msg)` | 接手期间顾客的消息送到该通道 |
| `on_state_changed(conv)` | 状态变化通知 |
| 入站：同事回复 | 通道把同事的回复交给统一的发送路径（带同事标识与幂等键），发送即进入 `human`（J-04 已是这个语义） |

### 8.3 三个实现

| 通道 | 来源 | 说明 |
| --- | --- | --- |
| 手机 App 回音 | aichat | 只对 coexistence 号存在。同事在手机 App 发的消息以回音（`smb_message_echoes`，顾客号码在 `to`）到达，记为同事回复、进入 `human`；自己发出的消息靠 `wamid` 记录排除。回音无条件生效（R-4.3），即使该渠道选的是别的接手方式 |
| CRM 收件箱 | 现有 J-04 | 现有签名事件推送与 `/internal/crm/*` 原样保留。阶段 3 起 CRM 按契约接入后，收件箱接口改按契约 §7 的回调规范（同一套签名），见契约 §10 |
| 自带工作台 | 新增（阶段 2） | 见 §8.4 |

导演台的接管与回复（`/console/handover`、`/console/reply`）在 Demo 实例里保留，实现上改为调用同一状态机与发送路径。

### 8.4 自带工作台的范围

**做**：按租户登录；对话列表（筛选：要人工、接手中、全部）；看完整对话（含各渠道、核验状态、能力调用记录摘要）；接手、回复（文本，WhatsApp 24 小时窗口外按模板白名单）、交还；看需人确认单的状态（只读，确认在业务系统里做，Q-07）。

**不做**（R-4.5，避开 A-15）：工单、指派规则、状态流转、优先级、SLA、报表、内部备注流转。若以后要做，归属先按 A-15 定。

工作台的账号与登录（租户同事账号、密码与会话、谁能看哪些渠道）在阶段 2 开工前补一份短设计，过 Kelvin 批准。

## 9. 对话记录

- **权威记录**：MySQL。现有 `chat_messages`、`tool_calls`、`model_usage`（`services/audit.py:58-117`）加 `tenant_id`、`channel_id`、`customer_ref` 列（迁移把历史行归到 Demo 租户），这就是「同一份对话记录」（R-1.3）；工作台、导出、删除都读这里。
- **模型上下文**：Redis，按渠道分键（`chat:user:{channel_id}:{号或 BSUID}` 一类，具体在阶段 0 的状态层任务里定），保留期沿用现在的 7 天、20 轮。阶段 0 迁移时现有 `chat:user:*` 一次性改名到 Demo 渠道下，避免进行中的 Demo 对话丢失选中的机器人。
- **跨渠道归并**（R-1.4）：`customer_link` 记「某渠道顾客在某次对话里核验为某业务系统顾客」及时间；显示时按租户把核验到同一系统顾客的对话放在一起。核验本身的有效期按对话算（契约 §6.3），归并记录不授予后续对话任何访问。
- **Coexistence 原始数据**：照 aichat 落 JSONL（按实例、渠道、字段、日期分文件），只供排查与将来导入；不进模型上下文。
- 保留期：见 Q-16 与 §11。

## 10. 计费

- 模型仍由 Chatbot 直接调用（E-17）；每次模型 API 调用产生一条用量事件，写进本地 outbox（建议 MySQL 表，与对话写入同一事务或紧随其后），后台按 `ai_billing_hub` 的接入规范签名上报；上报失败不影响回复（N-2）。
- **以 `ai_billing_hub` 文档为准，本设计不另定格式**：签名见其 `docs/api.md`「集成请求签名」，上报见「集成：用量事件摄取」（`POST /api/v1/integration/usage-events`，批量 `/usage-events/batch`），字段与重试语义见 `docs/design/AIH-TASK-029-usage-ingest.md`，接入方 Python 包见 `integration-client/README.md`。要点（核对于 2026-11-26，实施时以该仓库当时版本为准）：每个项目一套凭据，绑定一个租户、一个项目；`event_id` 是幂等键，重复上报安全；是否重试只看响应里的 `retryable`，不可重试的进死信并告警；请求里不能带对话内容或费用。
- **项目划分**（F-08，`ai_billing_hub` 规格 §141「one project corresponds to one module of one product line」）：产品线 = Chatbot，模块 = 该租户用 Chatbot 服务的那个业务场景。建议：公司客服（不嵌系统）、网店、CRM、ERP、Stock Core 各一个模块；一个租户用了几个就在 billing 里建几个项目、发几套凭据（模块怎么划见 Q-23）。
- **Acuven 自己**：内部租户，`INTERNAL_METERED_ONLY`（只计量不扣费，OWNER 文档）下的「Chatbot × 公司客服」项目（Q-20）。
- **Demo 实例**：F-04，内部租户下单独的 Demo 项目；网店样板在同一 Demo 实例上，是否再拆项目随 Q-23。
- 语音转写（OpenAI）的用量 `ai_billing_hub` 是否支持对应的 `provider`/`usage_type`，阶段 2 开工前去该仓库核对；不支持就先只记本地，不自造类型。
- 本地 `console/cost.py` 的硬编码单价只给导演台显示用，不作为计费依据。

## 11. 安全与隐私

- 本仓库公开：真实租户配置与密钥只在库里加密存放（§5.3），仓库只有 Demo 配置（R-8.3、N-1）。
- 服务间签名、令牌、身份规则见契约 §2、§6。
- 生产实例没有导演台，也没有「一把令牌看所有租户」的入口；运营命令需要进容器执行。
- 生产实例的对话保留期、停用租户的删除、顾客要求删除的处理，在 `docs/data-flow-pdpa.md` 的基础上为生产实例另写一份（阶段 1a 上线前），保留期取值见 Q-16。aichat 隐私政策已承诺「停用后 30 天内删除」，合并后必须照做（现在只能手工删）。
- 发给 Anthropic、OpenAI、Meta 的数据范围不变；业务系统能力返回的个人数据只进本次对话的模型上下文与对话记录。

## 12. 分阶段

| 阶段 | 内容 | 前提 | 出口 |
| --- | --- | --- | --- |
| **0 合并** | 改名；按模板补 `.platform/`、CI、按 SHA 部署、健康检查报 SHA；OpenClaw 登记；实例开关；租户数据模型；按渠道路由；搬 aichat 五项；生产实例空跑上线 | 本组文档批准；Q-01、Q-12～Q-15、Q-18、Q-21 有答复 | Demo 实例行为不变（R-7.4）；生产实例在线、白名单为空不回任何人（R-6.2）；健康检查报 SHA；`onboard check` 为 ready |
| **1a Acuven 客服上线** | Acuven 租户与机器人配置（aichat 知识 + 需求里缺的 6 类）；用 Embedded Signup 接入客服号；回音接管真机验收；白名单从测试号放到 `*`；生产实例隐私文档 | aichat 的 Meta 手续：App Review 结果、Access Verification、设置备用号（以 `acuven_aichat/tasks/todo.md` 为准）；Q-02 | 真机验收通过，客服号对所有人回复 |
| **1b 网店嵌入样板**（与 1a 并行） | Chatbot：网页渠道、可嵌入聊天窗口、身份令牌、契约客户端（清单校验、调用、签名、核验、需人确认回调）；Demo 实例上建「网店样板」租户。网店侧（在 `acuven-shop` 另登任务）：能力清单与调用接口、身份令牌签发、嵌入脚本、渠道核验能力 | Q-05～Q-08、Q-17、Q-19；网店侧任务批准 | 网店 Demo 上：匿名问商品、会员查自己的订单、申请退款经后台批准（R-3.4、R-5.1） |
| **2 工作台、接手统一、计费** | 自带工作台（含账号设计）；三种接手通道接到统一状态机；用量上报 `ai_billing_hub` | 工作台账号短设计批准；billing 侧建项目、发凭据 | 三种接手各自验收；billing 里能看到 Chatbot 各项目用量 |
| **3 业务系统接入、多租户开户、自托管** | CRM、ERP 按契约提供能力，Demo 零售机器人切过去并删旧客户端；CRM 收件箱改按契约回调；客户号码经 Embedded Signup 开户；托管专属与自托管打包。Stock Core 等它深度融合（C-21）后期再接，接法见 Q-24 | Q-09～Q-11、Q-24；各系统侧任务 | 至少一个外部客户租户上线 |
| **4 客户自助后台** | 客户自己改知识、开关能力、管理工作台账号 | 自助范围另行设计、批准（B-02、G-02） | — |

1a 与 1b 都依赖阶段 0，彼此不依赖（R-6.1）：1a 只用 WhatsApp 渠道与回音接管；1b 只用 Demo 实例。Acuven 官网的网页聊天窗口（匿名、只答知识）在 1b 做出聊天窗口后可以随时加，不算 1a 的出口。

## 13. 阶段 0 任务拆分草案

按模板「一任务一层、允许路径是精确文件清单、超过 12 个路径先拆」的粒度。**只是草案，不写进 `.platform/`**；编号前缀随 Q-01（暂写 `CHAT-0xx`），批准后在登记时定稿（届时路径以当时代码为准）。🔍 = 改状态机或真实数据，按本仓库 `tasks/REVIEW.md` 走冷审。新建文件的路径是建议。

| 编号 | 层 | 内容 | 允许改的路径 | 验收要点 | 依赖 |
| --- | --- | --- | --- | --- | --- |
| CHAT-001 | 部署 | 健康检查报出提交 SHA（构建时注入） | `backend/app/main.py`、`backend/Dockerfile`、`backend/tests/test_health.py` | `/health` 返回 `commit` 字段；测试覆盖未注入时的值 | — |
| CHAT-002 | CI | 新增 CI：`unit`（后端 pytest，Python 版本与镜像一致）、`frontend`（构建） | `.github/workflows/ci.yml` | PR 上两个 job 绿；job 名与 `required_status_checks` 一致 | — |
| CHAT-003 | 部署 | 部署工作流达到 D1–D5：部署 `DEPLOY_SHA`、上线后健康检查核对 SHA、`cancel-in-progress: false`、`timeout-minutes` ≤ 25；保留主机部署锁 | `.github/workflows/deploy.yml`、`deploy/healthcheck.sh`、`docker-compose.prod.yml` | 一次真实部署，健康检查报出该 SHA；故意失败时工作流红 | 001 |
| CHAT-004 | 平台契约 | `.platform/project.yaml`、`commands.yaml`、`tasks.yaml`（默认 deny）、planning 计划块；`CLAUDE.md`/`AGENTS.md` 补模板规则块（保留本仓库已有约定） | `.platform/project.yaml`、`.platform/commands.yaml`、`.platform/tasks.yaml`、`CLAUDE.md`、`AGENTS.md` | `contract_check` / `onboard check` 为 ready | 002、003；Q-01、Q-21 |
| CHAT-005 | 配置 | 实例开关 `CHATBOT_ENV`，`production` 下不挂 §4「仅 Demo」的路由与任务、相关配置被设置时拒启动 | `backend/app/config.py`、`backend/app/instance.py`、`backend/app/main.py`、`backend/tests/test_instance.py` | 两种取值下的路由表测试；未设置拒启动 | — |
| CHAT-006 | 数据 | 引入 Alembic；建 `chatbot_instance` 与租户各表（§5.1）；启动时核对实例记录 | `backend/requirements.txt`、`backend/alembic.ini`、`backend/migrations/env.py`、`backend/migrations/versions/0001_tenancy.py`、`backend/app/tenancy/models.py`、`backend/app/tenancy/db.py`、`backend/tests/test_tenancy_models.py` | 空库迁移成功；实例记录不一致拒启动 | 005；Q-15 |
| CHAT-007 | 数据 | 密钥加密存放与主密钥轮换 | `backend/app/tenancy/secrets.py`、`backend/app/config.py`、`backend/tests/test_tenancy_secrets.py` | 库里无明文；换主密钥后可读 | 006 |
| CHAT-008 | 业务规则 | 租户配置服务层 + 运营命令（建租户、加 App、加渠道、导入机器人、设白名单/管理号码/接手方式），每条写 `audit_log` | `backend/app/tenancy/service.py`、`backend/app/tenancy/cli.py`、`backend/tests/test_tenancy_service.py` | 命令全流程测试；审计记录完整 | 007 |
| CHAT-009 | 业务规则 | Demo 租户迁移：从 `bots/data/*.json` 与现有 `WHATSAPP_*` 环境变量生成 Demo 租户、App、渠道 | `backend/app/tenancy/seed_demo.py`、`backend/app/bots/registry.py`、`backend/tests/test_seed_demo.py` | 迁移幂等；生成的机器人与现有 JSON 逐字段一致 | 008 |
| CHAT-010 🔍 | 服务 | WhatsApp 按 `phone_number_id` 路由与验签、按渠道凭据发送与取媒体 | `backend/app/routers/whatsapp_webhook.py`、`backend/app/services/whatsapp.py`、`backend/app/services/whatsapp_media.py`、`backend/app/tenancy/resolve.py`、`backend/tests/test_whatsapp_routing.py` | 两个渠道各自验签、各自发送；未知号码不处理；现有 webhook 测试全绿 | 009 |
| CHAT-011 🔍 | 状态 | Redis 键加渠道维度（含现有 `chat:user:*` 一次性迁移）；去重与每日限额从进程内存移到 Redis | `backend/app/services/user_store.py`、`backend/app/session_store.py`、`backend/app/tasks/migrate_redis_keys.py`、`backend/tests/test_user_store.py`、`backend/tests/test_session_store.py` | 两渠道同号码互不可见（R-2.3）；迁移幂等；多进程去重测试 | 010 |
| CHAT-012 | 数据 | 对话记录加 `tenant_id`、`channel_id`、`customer_ref`（迁移历史行到 Demo 租户） | `backend/app/services/audit.py`、`backend/migrations/versions/0002_audit_tenant.py`、`backend/tests/test_audit.py` | 历史行归属正确；按租户查询测试 | 006 |
| CHAT-013 | 服务 | 搬 aichat：白名单与管理指令（`#list`/`#hold`/`#bot`），按渠道配置 | `backend/app/channels/allowlist.py`、`backend/app/channels/admin_commands.py`、`backend/app/routers/whatsapp_webhook.py`、`backend/tests/test_allowlist.py`、`backend/tests/test_admin_commands.py` | aichat 的对应用例迁过来全绿；白名单外不写历史 | 011 |
| CHAT-014 🔍 | 状态 | 统一接手状态机（§8.1），把 aichat 的回音接管（`auto`/`hold`、`#bot`、自回音防护）并入；导演台与 CRM 改用同一状态机 | `backend/app/services/handover.py`、`backend/app/channels/echo.py`、`backend/app/routers/whatsapp_webhook.py`、`backend/app/routers/internal_crm.py`、`backend/app/routers/console.py`、`backend/tests/test_handover.py`、`backend/tests/test_echo_handover.py` | 现有接手测试全绿；回音、hold、三种退出方式测试 | 013 |
| CHAT-015 | 服务 | 搬 aichat：coexistence 同步字段处理与原始数据落盘（含保留期清理） | `backend/app/channels/coexistence.py`、`backend/app/routers/whatsapp_webhook.py`、`backend/app/tasks/cleanup_coexistence.py`、`backend/tests/test_coexistence.py` | 同步字段先于白名单处理；写失败回 500；清理按保留期 | 013；Q-16 |
| CHAT-016 | 运营 | 搬 aichat 接入脚本：`exchange`/`sync`/`check` 改为运营命令，结果写渠道记录 | `backend/app/tenancy/whatsapp_onboard.py`、`backend/app/tenancy/cli.py`、`backend/tests/test_whatsapp_onboard.py` | 用模拟 Graph 响应跑通三步 | 008 |
| CHAT-017 | 部署 | 生产实例：compose 服务、环境模板、nginx vhost（`aicustomer.acuventech.com`）、部署工作流部署第二个实例 | `docker-compose.production.yml`、`backend/.env.production.example`、`deploy/nginx/aicustomer.conf`、`.github/workflows/deploy.yml`、`deploy/healthcheck.sh` | 生产实例健康检查报 SHA；无渠道时不回复 | 003、005、006；Q-12～Q-14 |
| CHAT-018 | 文档 | README、`docs/data-flow-pdpa.md` 按合并后更新；本组文档状态改为已实施的部分 | `README.md`、`docs/data-flow-pdpa.md`、`docs/product/DESIGN.md` | 只跑文档相关检查 | 017 |

阶段 0 最后一项照本仓库约定是真机验收（Kelvin）：Demo 实例抽演一段行业对话与一次 CRM 接手，确认行为不变。

## 14. 仓库外的事项（不在本仓库任务里）

| 事项 | 谁 | 时点 |
| --- | --- | --- |
| GitHub 仓库改名、GHCR 镜像名、分支保护与必需检查 | Kelvin | 阶段 0 开始时（Q-01、Q-21） |
| OpenClaw 控制面登记（`worker: none`、`status: pilot`，再分配 Worker） | 控制面会话 | CHAT-004 合并、`onboard check` ready 后 |
| 生产实例 MySQL 库与账号、`.env`、Cloudflare/nginx | Kelvin | CHAT-017 之前 |
| `acuven_aichat` 停容器、归档、README 指向新仓库 | Kelvin / aichat 会话 | 1a 跑稳后 |
| `acuven-shop` 侧任务（清单、调用接口、令牌、嵌入、核验） | 网店会话，登记在网店 `.platform/tasks.yaml` | 1b |
| `ai_billing_hub` 建项目、发凭据 | Kelvin（ADMIN） | 阶段 2 |
| `acuven_hub/DECISIONS.md` E-21 状态、`E:\projects\CLAUDE.md` 项目表 | 对应会话 | 本文档批准后、改名后 |

## 15. 风险

- **单一 Meta App 回调**：多租户生产实例是所有经 Acuven App 接入号码的唯一入口，它停机 = 所有租户的 WhatsApp 停。缓解：按 SHA 部署、健康检查门禁、出问题回滚；Meta 会重投失败的 webhook 一段时间。
- **Demo 行为回归**：阶段 0 动了入站路由、状态键、接手状态机三处核心。缓解：这三项标 🔍、现有 51 个测试文件全绿是硬门槛、最后真机抽演。
- **Meta 手续时间不可控**：1a 可能久等。缓解：1a 与 1b 并行（R-6.1）；生产实例先空跑。
- **契约过早定死**：v1 只有网店一个实现方。缓解：契约带版本号（契约 §9），v1 只承诺网店样板用到的部分，CRM、ERP 接入时可以升 v1.x。
