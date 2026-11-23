# Glata 工单自动分析

在自己的 Mac 上一键启动，自动监听 Glata 私聊中的新工单：解析详情链接、读取工单页权威会话信息、拉取本次问题的聊天记录、本地 OCR、调用 doubao CLI 检索资料并生成 Markdown 分析。电话加急是独立可选分支，不替代 AI 分析。

## 快速入口

首次部署见 [DEPLOY.md](DEPLOY.md)。GitHub 中为去内部图片与资料链接的公开部署副本；维护者本机可保留飞书单向同步原版，两者不反向覆盖飞书。当前官方 doubao CLI npm 制品的 macOS 路径限 ARM64，Intel 上的离线源码回归不等于 CLI 运行支持。

每个新终端先进入本人项目并激活虚拟环境；采用 DEPLOY 用户目录安装时设置 CLI 路径：

```bash
cd ~/glata_watch
source .venv/bin/activate
export PATH="$HOME/.local/glata-tools/bin:$PATH"
export DOUBAO_CLI_BIN="$HOME/.local/glata-tools/bin/doubao"
```

部署完成后以下三种启动方式任选一条，不连续执行：

```bash
cd ~/glata_watch
bash start.sh          # 人工工单自动分析，不发增强提醒
bash start.sh -at      # 人工工单：自动分析 + 机器人 @本人并电话加急
bash start.sh -at all  # 所有 @本人的工单含告警都提醒；只有人工工单做 AI 分析
```

提醒正文固定为 **“你有新工单！！！！”**。`all` 不是 @所有人。监控运行期间换参数不会热切换，需要先停止，再以新参数启动。

```bash
bash stop.sh          # 停监控，保留专用浏览器
bash stop.sh --all    # 同时正常关闭专用浏览器，保留登录数据
GLATA_BG=1 bash start.sh -at  # 不显示日志控制台
python3 console.py   # 再连接只读控制台；Ctrl+C 只退出控制台，不停监控
```

## 流程与边界

```text
watch_and_run.py（默认每 2 秒一轮；单实例）
  └─ lark-cli 拉取 Glata 私聊最新页（50 条，无 reactions）
      ├─ 没有新的 @本人 工单：继续扫描
      ├─ 提醒分支：独立状态 + 2 个轻量线程 → 机器人消息 → 电话加急
      └─ 人工分析：详情 URL → 工单号
          → 专用 Chrome 读取 chat_id / 转人工时间 / Form Type / 关闭状态
          → 已关闭或 Alert：跳过分析
          → 独立 AI 子进程（默认最多 3 单并发）
              → 按 chat_id + 转人工时间前 90 秒拉聊天记录
              → 欢迎卡辅助裁剪 → 本地截图 OCR
              → doubao CLI → Markdown 分析报告
```

- 群名、报告人不是定位依据；工单页提供会话映射。取得 ID 不代表已有该群访问权限。
- 底层消息访问仍通过 lark-cli 用户授权调用飞书接口，**不是飞书事件长连接**。CDP WebSocket 仅用于读取专用浏览器页面。
- 主监控同时按消息 ID 与“工单号＋转人工时间”的任务键去重；同一轮问题的不同通知卡片合并，不同工单仍可并发。独立增量工具按 `message_position` 本地比较，两者都不是“未读消息”。
- 默认首次部署第一轮仅建立分析基线；已有状态时继续处理未处理卡片和重试。电话提醒每次启动都重新建立最新位置基线，不补拨历史或停机期间的提醒。
- 超过 AI 并发上限的卡片留到后续扫描；定位仍发生在主循环，页面加载慢会延长那一轮。2 秒是目标周期，不是每次请求的耗时保证。
- 目前只扫描最新 50 条；如果停机或突发消息超过一页，可能漏卡片，尚未实现完整历史追赶队列。
- 工单元数据缓存 TTL 为 7 天；含关闭状态的旧缓存也可能滞后。接口返回不可读时按当前实现跳过，而不能单凭错误码认定工单已解决。

## 配置：只改本机文件，不改源码

```bash
cp config.example.json config.local.json  # 仅首次；已有文件不要覆盖
chmod 600 config.local.json
# 用编辑器填写配置后
python3 project_config.py --check
```

`config.local.json` 不入 Git、不进分发包。`GLATA_CONFIG_FILE` 可指定其他 JSON 路径。非密钥配置优先级为 **环境变量 > 本机 JSON > 空默认**，修改后重启生效。机器人凭证中的 `app_id` 仍优先于环境/本机后备 App ID。

| JSON 字段 | 环境变量 | 用途 |
|---|---|---|
| `lark_cli_bin` | `LARK_CLI_BIN` | 独立 CLI 的绝对路径；配置后禁止候选回落 |
| `chat_id` | `GLATA_CHAT_ID` | 监听的 Glata 私聊 |
| `target_user_name` | `GLATA_TARGET_USER_NAME` | 本人姓名，跨应用 ID 的匹配兜底 |
| `target_user_id` | `GLATA_TARGET_USER_ID` | 本人主要 open_id |
| `target_user_ids` | `GLATA_TARGET_USER_IDS` | 多应用本人 ID 数组；环境变量用逗号分隔 |
| `bot_app_id` | `GLATA_BOT_APP_ID` | 凭证未含 app_id 时的后备应用 ID |
| `notify_chat_id` | `GLATA_NOTIFY_CHAT_ID` | 机器人外发提醒群，不是 Glata 私聊 |
| `alert_reference_chat_id` | `GLATA_ALERT_REFERENCE_CHAT_ID` | 可选告警检索参考群，不是提醒群 |

程序没有维护者个人身份默认值；配置缺失应明确失败，不允许静默扫描他人的会话。跨应用 open_id 不同，`target_user_ids` 必须只包含自己已确认的 ID。姓名兜底可能有同名风险，应按实际卡片核对。

### 后台用户授权与本人 ID

后台读取使用独立官方 CLI 的持久用户 OAuth 授权，所有读取显式 `--as user`，不依赖豆包临时注入 token；电话仍走原机器人应用，不重置其 Secret。官方安装/登录入口见 `https://github.com/larksuite/cli`。应使用已核验支持 `auth` 的独立可执行文件，不要把客户端内置裁剪版当作可独立登录的版本。

独立 CLI 登录后，在自己的终端运行（路径按本机实际安装位置）：

```bash
"$LARK_CLI_BIN" contact +search-user --user-ids me --as user --format json
```

只取 `data.users` 中唯一“本人”记录：`open_id` 填 `config.local.json` 的 `target_user_id`，`localized_name` 填 `target_user_name`。输出含个人信息，只留本机，不复制到公开文档/仓库。不同应用的 open_id 不同；不是机器人的 ID、报告人的 ID、工号或手机号。`target_user_ids` 仅补其他应用下已经确认属于自己的 ID，不能用它绕过登录用户校验。

```bash
python3 deploy_check.py              # 只读探测并打印配置建议
python3 deploy_check.py --preflight  # 不显示真实值；验证固定 CLI 的本人及 Glata 可读性
```

`start.sh` 在启动浏览器与监控之前执行 `--preflight`。用户授权缺失、本人配置不一致、Glata 读取失败都停止启动；授权过期后需正常重新 OAuth，不改监听 ID、不复制临时 token、不清去重状态。更换 CLI 应用后重新取本人 ID，本机主 ID 要与该 CLI 查询结果一致。

### 其他运行配置

- `LARK_CLI_BIN`：固定独立飞书 CLI 路径，优先于 `lark_cli_bin`；未显式配置时优先 `~/bin/lark-cli`，最后保留旧客户端探测兼容。指定路径无效或授权失效时不会切换其他候选。
- `DOUBAO_CLI_BIN`：豆包 CLI 路径；默认 `doubao`。
- `GLATA_INTERVAL`：一键入口扫描周期秒数，默认 2。
- `GLATA_BROWSER_MODE`：默认 `headless`，显式登录用 `headed`。
- `GLATA_CDP_PORT` / `GLATA_CDP_PROFILE` / `GLATA_CHROME_BIN`：专用 Chrome 端口、profile、可执行文件。
- `GLATA_BOT_CREDENTIALS`：凭证路径，默认项目根 `app_secret.json`。
- `GLATA_NOTIFY_OPEN_ID`：提醒机器人应用下的本人 ID，用于同名消歧。
- `VISION_OCR_BIN` / `TESSERACT_BIN` / `OCR_LANG` / `ONCALL_IMG_DIR`：OCR 引擎、语言和图片暂存位置。

## 浏览器后台与登录

日常默认无窗口 Chrome，复用原专用 profile；已有在线浏览器保守复用，不强行重启。**飞书 CLI 授权不等于 Glata 网页登录**。首次或登录过期时：

```bash
bash stop.sh --all
GLATA_BROWSER_MODE=headed python3 cdp_client.py --ensure-browser
# 在专用窗口访问 Glata，由本人完成登录验证
python3 cdp_client.py --stop-browser
bash start.sh  # 或需要的 -at / -at all
```

后台/有窗口模式不能同时使用同一个 profile。停止不删除 profile；不导出 cookie、不复制他人的登录数据。CDP 只绑定本机地址，不能向公网开放调试端口。

## 提醒分支

机器人需已发布、已进入提醒群，并取得最小应用身份权限：`im:message:send_as_bot`、`im:chat.members:read`、`im:message.urgent:phone`。完整申请、发布和本机凭证教程见 DEPLOY。

```bash
python3 ticket_notify.py --check  # 仅鉴权、读成员、确认目标；不发消息、不拨电话
```

这个自检不证明发送/电话权限、额度或实际来电正常。电话结果不确定或失败时不自动重拨；提醒状态独立于分析状态。不要通过删除状态强制重拨。提醒线程失败不阻断人工 AI 分析。

## AI、OCR 与告警参考

默认模型保持 `DeepSeek V4.1 Flash`。提示词要求浓缩问题与补充信息各不超过 100 字，先找内部资料，未找到再查互联网；字数由模型生成，尚无自动逐项字数验收。

聊天记录是输入主体；OpenAPI 不可见的 Copilot 摘要不补造。截图使用 macOS Vision OCR，失败时尝试 Tesseract；OCR 存在误识别和长度裁剪，图片占位符不能被当作已识别文字。

可选告警参考群仅加入 AI 提示词，要求按系统、现象和时间查询直接相关告警、独立注明来源；不新增告警轮询，也不保证检索命中。无权限/无结果必须说明，不能把其他工单或告警原文当成本单证据。模型输出仍需人工审阅，不自动执行网络配置或回复客户。

## 文件与数据

| 文件 | 作用 |
|---|---|
| `project_config.py` / `config.example.json` | 集中配置读取、空输入保护、模板 |
| `start.sh` / `stop.sh` / `console.py` | 一键启停与关键日志控制台 |
| `watch_and_run.py` | 扫描、定位、并发调度、重试 |
| `read_oncall_chat.py` | 卡片解析、精确拉消息、元数据与 OCR |
| `cdp_client.py` | 专用浏览器生命周期与中英文工单字段解析 |
| `oncall_ai_summary.py` / `ai_task_guard.py` | 同任务进程锁、完成凭据、会话安全合并、提示词与模型输出 |
| `ticket_notify.py` | 异步机器人提醒、电话加急与防重复状态 |
| `deploy_check.py` | 部署环境与本人配置探测，结果只留本机 |
| `fetch_glata_auto.py` / `deploy/*.template` | 可选旧增量归档工具；不是完整监控必需步骤 |
| `vision_ocr.swift` / `build_vision_ocr.sh` | 本地 OCR 源码和编译脚本 |
| `test_*.py` | 合成数据离线回归，不调用真实电话/AI |
| `check_release.py` / `release_files.txt` / `make_dist.sh` | 白名单和隐私检查、生成干净分发包 |

本机业务产物：`oncall_chats/`（消息 JSON、输入文本、报告、分析/提醒/OCR/AI 会话状态）、`logs/`、根目录 `.ticket_cache.json`、`.watch.pid`、独立工具的 `.last_position`。都不入 Git。不要上传整个工作目录。

监控新报告使用 `<任务键>_summary.md`，不同转人工轮次分开保存，不再覆盖同群旧问题。手动 `--chat-id` 旧入口保持原文件名。`.watch_state.json` 新增 `tasks` 索引且兼容旧结构，不清空旧 seen/pending；`.ai_task_*.lock`、`.ai_done_*.json` 保护同任务进程与成功报告。完成凭据校验不一致时明确报错、不自动重跑。会话元数据在成功、失败及可获取超时输出时加锁合并保存；若 CLI 未输出会话号，仍不能保证重试复用。

电话提醒仍保持原来按通知卡片独立处理的语义，本次合并仅针对 AI 分析，不改电话分支。

## 失败与排查

- 工单页定位失败：最多 6 次、通常间隔 300 秒；通用处理异常按现有逻辑 60 秒后重试。
- AI 失败：通常 120 秒后重试，累计上限 6 次。不是所有错误都等 5 分钟。
- 阶段三返回 `0` 写报告；`6/7` 表示 CLI/上游错误，不写假报告；`8` 表示群不可读，监控按现实现为终态。需要区分权限、成员变化和关闭，不扩大解释。
- `stop.sh` 只停监控，已启动的独立 AI 可能继续完成。新版 AI 同任务锁与完成凭据防止重启期间重复调用；返回 `9` 表示仍忙，等待 10 秒检查、不计失败，`10` 表示本机状态/产物校验失败。旧版已在运行的进程不持新锁，升级前应等旧分析结束再停启；历史会话和报告不会自动合并或删除。
- 模型推理/检索耗时受网络和服务影响，没有固定 RTT 承诺。本版未重新跑真实 AI 性能基准。

排查先看控制台，再看 `logs/watch-*.log` 与对应 `logs/ai-*.log`。CLI 兼容性和权限取决于实际部署版本，安装后先自检。

## 维护与更新

```bash
python3 -m unittest discover -p 'test_*.py' -v
python3 check_release.py
bash make_dist.sh
```

提交规范与安全边界见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。GitHub Actions 使用合成数据回归，不配置个人凭证；私有仓库也不是业务数据备份盘。更新采用有说明的普通 commit，不强推、不随意删除本机状态。
