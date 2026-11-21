# 部署教程

本教程用于有 Glata 工单权限、会收到本人通知的使用者，在自己的 Mac 上部署。日常只用一键入口；单阶段命令仅供初次验收/排障，不是每次运行步骤。不要复制他人的工作目录、凭证或浏览器 profile。

## 1. 获取干净源码

仓库为私有，使用经授权的 GitHub 账号：

```bash
git clone git@github.com:chargyzuo/glata_watch.git
cd glata_watch
```

已有同名工作目录不要在其上解压覆盖；先检查已有工作。无仓库访问权限时，由维护者按组织允许的范围交接 `bash make_dist.sh` 生成的干净分发包；不传整目录。

## 2. 软件与模块

支持 macOS；当前 shell 入口兼容系统 Bash。需要 Python 3.9 及以上、Google Chrome、可用且已授权的 lark-cli、可用且已登录的 doubao CLI、本地 OCR 的 Swift 编译器。唯一第三方 Python 依赖为 websocket-client。

```bash
python3 -V
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -c "import websocket; print(websocket.__version__)"
```

之后启动和自检均在已激活此虚拟环境的终端进行，后台子进程继承这个 Python。不要混用系统 pip 与其他 Python。

- Chrome 默认路径为 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，其他路径通过 `GLATA_CHROME_BIN` 指定。
- lark-cli 可通过客户端安装目录动态探测，或设 `LARK_CLI_BIN` 为自己实际可执行路径。存在命令不等于已授权，需以下自检。
- doubao CLI 默认取 PATH 的 `doubao`；若不在 PATH，设 `DOUBAO_CLI_BIN`。安装/账号访问按所在环境正常流程处理，不能复用别人的凭证。本项目不打包或安装这两个 CLI。
- 本地 OCR 首选 macOS Vision：未安装编译器时运行 `xcode-select --install`，完成后 `swiftc -version`，再 `bash build_vision_ocr.sh`。
- Tesseract 是可选兜底，已有 Homebrew 时可 `brew install tesseract tesseract-lang`。缺省不影响 Vision，但 Vision 也不可用时将缺失截图文字。

## 3. 身份与三个会话分别配置

```bash
python3 deploy_check.py       # 默认只读访问：检查依赖，探测本人/Glata；输出含个人信息，只留本机
cp config.example.json config.local.json  # 仅首次；已有配置不要覆盖
chmod 600 config.local.json
nano config.local.json
```

在编辑器填写严格 JSON（双引号、无注释、无尾逗号）：

```json
{
  "chat_id": "oc_替换为你自己的Glata私聊ID",
  "target_user_name": "替换为本人姓名",
  "target_user_id": "ou_替换为本人主要ID",
  "target_user_ids": [],
  "bot_app_id": "",
  "notify_chat_id": "",
  "alert_reference_chat_id": ""
}
```

**必须替换所有说明占位符后再运行。**前三项是基础必填。程序自动把 primary ID 合并进集合；`target_user_ids` 可补自己在其他应用下已确认的 ID。不同应用下同一个人的 open_id 不同，不能只按一个 ID 判断，更不能混入同事 ID。姓名精确兜底仍需自己核对同名风险。

```bash
python3 project_config.py --check  # 不联网、不输出真实值
```

提醒/告警是可选：

- `chat_id`：**监听源 Glata p2p**，原来在哪里监听不改变。
- `notify_chat_id`：**输出提醒群**，用于机器人 @本人＋电话加急。
- `alert_reference_chat_id`：**资料参考告警群**，只进入 AI 提示词，无额外扫描；需自己有权查询，不能借用前两项。
- `bot_app_id`：旧凭证未含 App ID 时的后备；新凭证应直接写同应用 `app_id`。

环境变量覆盖同名本机配置，详见 README。不要在源码修改个人常量；同事独立填自己的本机配置。`GLATA_CONFIG_FILE` 可指向自定义位置；更改后正常重启才生效。

## 4. 专用浏览器首次登录

飞书 CLI 的用户授权与 Glata 网页登录独立。默认 headless 无窗口不会替代扫码登录。

```bash
bash stop.sh --all
GLATA_BROWSER_MODE=headed python3 cdp_client.py --ensure-browser
```

在专用窗口访问 `https://glata.bytedance.com`，由本人完成登录/验证，确认能打开自己有权限的工单。随后关闭专用实例，复用其 profile 进入后台：

```bash
python3 cdp_client.py --stop-browser
python3 cdp_client.py --ensure-browser
```

同一 profile 不能同时 headed/headless。默认 profile 是 `~/.glata_cdp_profile`。停止不删除登录数据；不复制他人的 cookie、profile 或 token。CDP 在线只证明浏览器连接，真实工单读取需要单独验收。

## 5. 日常一键启动与停止

三个模式任选其一，**不要连续启动三遍**：

```bash
bash start.sh          # 人工工单自动 AI 分析
bash start.sh -at      # 人工工单 AI 分析 + 电话提醒
bash start.sh -at all  # 所有 @本人的工单提醒；告警仍不进 AI
```

默认进入关键日志控制台，监控已在后台；Ctrl+C 只退出控制台。纯后台 `GLATA_BG=1 bash start.sh -at`；回看用 `python3 console.py`。

```bash
bash stop.sh          # 仅停监控，已启动独立 AI 可能继续完成
bash stop.sh --all    # 再停专用 Chrome，保留 profile
```

需要换提醒模式时先正常停启；已有进程下不同参数会报“参数未生效”。不需要用户名占位符、cron、LaunchAgent 或逐阶段启动。`deploy/*.template` 仅对应可选旧归档工具，不是本套全流程的部署依赖。

## 6. 电话提醒首次手动配置（可选）

不需要电话的使用者跳过本节。电话具有外部副作用和额度限制，先完成配置与只读自检，最终真实验收只触发一张新工单，不反复拨号。

### 6.1 复用/创建企业自建应用

使用自己的账号进入 [开放平台](https://open.feishu.cn/app) 或 [Lark 开放平台](https://open.larkoffice.com/)，复用自己有管理权限的应用，或创建企业自建应用。

1. 进入应用能力，添加**机器人**能力。不是仅 Webhook 的群自定义机器人。
2. 原权限、事件配置、服务用途保持不变；不要重置其他工具正在使用的 Secret。
3. 在权限管理的**应用身份（tenant_access_token）**下申请缺失权限，而非仅用户身份。

程序需要的三个 scope：

| Scope | 用途 |
|---|---|
| `im:message:send_as_bot` | 机器人发消息 |
| `im:chat.members:read` | 读取提醒群成员、确认本人在此应用下的 ID |
| `im:message.urgent:phone` | 对自己发送的消息电话加急 |

后台支持批量导入时，核对新增项、身份类型后导入：

```json
{
  "scopes": {
    "tenant": ["im:message:send_as_bot", "im:chat.members:read", "im:message.urgent:phone"],
    "user": []
  }
}
```

不为此分支申请全量私聊、全部通讯录、文档或短信权限。原 Glata 拉取继续使用 lark-cli 用户授权。权限不存在/灰度不可用时按正常管理员流程处理，不替换近似 scope。

### 6.2 发布与确认生效

若后台提示修改待发布：版本管理与发布 → 创建高于已有版本的新版本 → 默认能力机器人 → 范围只选实际使用者 → 保存/提交发布。更新说明可写：

> 新增工单增强提示：本机检测 @本人新工单后，在指定内部群机器人 @本人并电话加急，原 AI 分析保持；仅实际使用者，不对外共享。

需要理由时按真实业务补充三个权限的用途和使用范围。**提交审核不等于生效**，最终核对已发布版本、三个应用身份权限已开通。免审修改若已显示生效，不要为教程重复发布。是否需审批、可用范围和额度以实际租户后台政策为准。

### 6.3 提醒群与目标用户

在飞书建立专用内部提醒群，本人加入。群设置 → 群机器人 → 添加自己已发布的应用机器人。按群加急设置允许该机器人加急；如果仅管理员可加急，需按群权限正常配置机器人角色。

取这个群 ID 时核对群名/成员，不按搜索结果第一条盲选：

```bash
lark-cli im +chat-search --query "你的提醒群名称" --format json
```

CLI 不在 PATH 时使用已探测路径；按本机 `--help` 核对版本。把确认的 ID 写 `config.local.json` 的 `notify_chat_id`。监听 `chat_id` 仍是 Glata p2p。

### 6.4 安全保存应用凭证

从同一应用的凭证与基础信息取 App ID / App Secret，只在本机编辑器填写：

```bash
umask 077
nano app_secret.json
```

```json
{
  "app_id": "cli_替换为自己的应用ID",
  "app_secret": "替换为同一个应用的真实密钥"
}
```

```bash
chmod 600 app_secret.json
```

不要把密钥写进终端命令行、环境变量、聊天、截图或仓库。已有文件先核对归属，不盲目覆盖。默认读根目录此文件；其他位置通过 `GLATA_BOT_CREDENTIALS` 指定。新部署用标准 JSON；历史单元素写法仅用于兼容，未含 app_id 时必须配置本机后备 App ID。

### 6.5 只读预检与实际验收

```bash
python3 ticket_notify.py --check
```

此命令只鉴权/读群成员/唯一匹配本人，**不发消息不拨电话**；不证明电话权限、额度或真实来电已成功。同名时在启动终端设置 `GLATA_NOTIFY_OPEN_ID` 为此机器人应用下的本人 ID，仍核对姓名。不能借用另一应用下的 open_id。

用所需模式启动，等第一轮完成提示“已建立存量基线”，再等一张真实 @本人的新工单：

1. 群中发送者为自己的机器人，@本人正确，正文为 **“你有新工单！！！！”**。
2. 日志出现机器人消息、电话接口结果；自行核对实际来电，接口成功不等于已经接听。
3. 人工工单的 AI 分析仍运行/生成报告；提醒失败或等待不阻断分析。
4. `-at` 告警不提醒，`-at all` 告警只提醒不分析。

第一轮存量、停机期间消息不会补拨；AI 重试不再提醒。结果失败/不确定不自动重拨，不通过删除 `.notify_state.json` 强制再拨。

### 6.6 官方接口参考与限制

- [机器人消息发送](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/create)
- [群成员读取](https://open.feishu.cn/document/server-docs/group/chat-member/get)
- [电话加急及错误/额度限制](https://open.feishu.cn/document/server-docs/im-v1/buzz-messages/urgent_phone?lang=zh-CN)
- [应用令牌](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal?lang=zh-CN)
- [机器人使用](https://open.feishu.cn/document/client-docs/bot-v3/how-to-use-bot-in-feishu?lang=zh-CN)

引用为部署查阅入口，页面版本/租户策略可能变化，权限缺失、成员/范围、群加急设置、额度和折叠会话等问题按实际返回与后台核对，不承诺固定申请时长或实际响铃。原内部参考说明只留维护者本机，不分发其真实资源标识。

## 7. 验收与排障

### 离线自动检查（没有真实副作用）

```bash
python3 -m unittest discover -p 'test_*.py' -v
python3 check_release.py
python3 project_config.py --check
```

### 真实链路验收（自己有权限的当前工单）

日常自动执行；以下仅首次检查。示例的角括号值需替换，不直接执行原样占位符。

```bash
python3 cdp_client.py <当前工单号>
python3 read_oncall_chat.py --chat-id <上一步的会话ID>
python3 oncall_ai_summary.py --chat-id <会话ID> --since <转人工前90秒的ISO8601时间> --dry-run
```

确认元数据、起点、记录与图中文字不混入其他问题；然后通过新工单自动触发，检查报告可打开、结论证据、内部/告警引用归属和浓缩字数。不要把 `--chat-id` 单独取到的最新一页当成“当前问题精准时间切片”的完整验收。`--dry-run` 仍读取聊天/OCR，只是不调用模型。

| 现象 | 排查与影响 |
|---|---|
| 配置校验失败 | JSON/本人/监听 ID 未填或占位未替换；启动应停止，不改源码 |
| CLI 鉴权失败 | 正常检查自己的授权与实际 CLI 版本；不要全局清理豆包环境或借用 token |
| 工单页登录/字段未就绪 | 显式 headed 重新登录，核对权限、工单号和页面字段改版 |
| 有新卡片但无 AI | 检查 mentions、本机身份、关键词、Alert 类型、seen/pending 与 workers；不要删状态试探 |
| 230002 群不可读 | 权限、未入群、成员被移出或关闭都可能；当前 AI 主链路按终态跳过，不据此认定已解决 |
| OCR 空/错 | 编译 Vision，核对语言与截图；不把未识别文字当事实 |
| AI 非零/上游错误 | 看对应 ai 日志，原逻辑通常 120 秒后重试，不能写成功假报告 |
| 同单重复会话 | 新版按工单号＋转人工时间合并 AI 通知；查控制台“分析去重”、`.watch_state.json` 的 tasks 和 `.ai_sessions.json`，旧历史对话不会自动删除 |
| AI 退出码 9 / 10 | 9=同任务旧进程仍忙，等待而非再调模型；10=本机状态或完成报告校验失败，先核对文件，不清空去重状态 |
| 电话配置/权限失败 | 检查同应用凭证、应用身份权限、发布、入群与范围；只读预检不能覆盖电话权限 |
| 电话失败/不确定 | 核对群消息与真实来电，不自动重拨、不清提醒状态；人工分析继续 |
| 退出控制台仍有监控 | 这是设计；真正停止用 stop.sh |

本机数据不随 Git 更新。更新方法和提交说明见 CONTRIBUTING；不要对 `oncall_chats/*.json` 一刀切删除，它包含重要去重/提醒状态。清理业务数据需要另定保留策略，不是部署步骤。

0.1.1 更新后，监控报告以 `<任务键>_summary.md` 保存，同群新轮次分开；原历史报告不搬迁、不删除，手动旧入口文件名不变。升级前等旧 AI 完成再停启，新锁只能保护新版进程，不能给已在运行的旧进程补锁。日常使用原 start.sh 参数，无需手动配置任务键；电话分支不变。
