# 部署教程（公开发布副本）

本文件是经脱敏的 GitHub 公开部署副本：移除了内部截图、内部资料入口和 BNPM 路线，不包含维护者身份或凭证。维护者本机的飞书同步原版保持不变；公开副本不反向写回飞书。新用户按自己的账号和权限配置，不能复制他人参数。


本教程用于有 Glata 工单访问权限、会收到本人通知的使用者。维护者部署教程以其飞书版为唯一源，公开副本由同步内容脱敏生成；不反向覆盖飞书。由其他 AI 部署时必须先检查真实平台、源码版本、命令帮助与账号授权；本人扫码和系统权限由本人完成。日常一键启动后自动处理新工单，不逐单手动触发。不要复制他人的工作目录、凭证、二维码、配置或浏览器 profile。

**支持边界：**当前官方 doubao CLI npm 制品只支持 macOS ARM64；Apple Silicon 上 Node 必须原生运行。Intel Mac 或 Rosetta 下 darwin-x64 不属于本教程已支持路径，停止全流程部署并核实官方制品支持，不用第三方同名工具替代。本教程核对不等于所有新 Mac 已实机验收。

## 1. 获取干净源码

先安装 Apple Command Line Tools（用于 Git 和 swiftc）；若 xcode-select -p 已成功则跳过安装。安装弹窗由本人完成，完成后再继续。仓库目前已公开，HTTPS clone 无需上传 SSH key；若以后改为私有，须取得仓库访问权限。

```bash
xcode-select -p
# 仅上条提示未安装时执行：
# xcode-select --install
git --version
# 仅在目标父目录没有 glata_watch 时 clone；已有目录先检查，不覆盖
git clone https://github.com/chargyzuo/glata_watch.git
cd glata_watch
# macOS 默认 zsh；本教程 read 命令使用系统 Bash，单独执行后再继续
/bin/bash
pwd
git log -1 --oneline
```

已有同名工作目录不要在其上解压覆盖；先检查已有工作。无仓库访问权限时，由维护者按组织允许的范围交接 `bash make_dist.sh` 生成的干净分发包；不传整目录。

**源码版本门槛（必须先过）：**0.1.2 包含后台用户授权预检和 doubao 的 Native -C 前置修复。不要使用旧缓存分发包或旧工作目录替代本版；新机器拉取后执行下列必要兼容特征检查与离线回归。特征检查不代替真实授权；失败时取得维护者的修复版，不让部署 AI 猜改业务源码或删除状态。

Python 尚未安装时只记录版本，先完成第2节软件准备；其后执行必要源码检查，不在缺 Python 的新 Mac 提前运行 Python 命令。

## 2. 软件与模块

**先准备基础软件，再执行项目命令：**本项目代码兼容 Python 3.9 及以上，新 Mac 推荐使用 Python 3.12（本机部署和既有 CI 验证版本）；不要把 macOS 是否自带 python3 当作前提。安装 ARM64 Node.js 18 及以上并保留 npm/npx（安装、启动 CLI 都需要）；Chrome 和 Apple Command Line Tools 必需。唯一第三方 Python 模块为 websocket-client。

软件入口：[Python 官方 macOS 安装器](https://www.python.org/getit/)、[Node.js 官方下载](https://nodejs.org/en/download)；Chrome 安装到 /Applications/Google Chrome.app。安装时选择与实际系统相容的正式版和 ARM64 架构。已有 Homebrew 时可执行 brew install python@3.12 node；没有 Homebrew 不要求为本项目额外安装，可使用官方安装器。Homebrew 命令依据：[python@3.12](https://formulae.brew.sh/formula/python@3.12)。

```bash
uname -m
node --version
npm --version
npx --version
node -p "process.platform + '-' + process.arch"
python3.12 --version
swiftc -version
test -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

应确认 uname 为 arm64、Node 输出 darwin-arm64、Node 版本不少于 18；若 Apple Silicon 的 Node 输出 darwin-x64，安装原生 ARM64 Node 后重开终端核对。Intel 不继续本教程。上述命令任何必需项失败都先修复，不接着创建后台。Python 若不是命名为 python3.12，先检查实际版本和绝对路径，再以已确认解释器替换下一步首条命令。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python3 -V
python3 -m pip install -r requirements.txt
python3 -c "import sys,websocket; print(sys.executable); print(websocket.__version__)"
bash build_vision_ocr.sh
file bin/vision_ocr
```

每条命令成功后再执行下一条。已有 .venv 先检查解释器和依赖，不覆盖重建。Vision 在每台新 Mac 本地重新编译，不拷贝另一架构二进制；file 应显示本机架构，编译成功不能代替后面的真实截图识别验收。

之后启动和自检均在已激活此虚拟环境的终端进行。每个新终端都重新 cd 到自己的项目目录、source .venv/bin/activate；AI 单独运行每个 Bash 调用时也要明确继承或重建 PATH，不依赖上一调用的 shell 变量。

软件/venv 已就绪，现在执行必要源码兼容检查；失败就停止本次部署并取得维护者发布的修复版，不自动提交或推送仓库。

```bash
python3 - <<'PY'
from pathlib import Path
import ast
root=Path.cwd()
assert (root/"test_lark_auth.py").is_file(), "源码缺少后台授权修复回归，请先取得维护者发布的修复版"
start=(root/"start.sh").read_text()
assert "deploy_check.py --preflight" in start, "源码缺少启动前授权预检"
tree=ast.parse((root/"oncall_ai_summary.py").read_text())
assert any(isinstance(n,ast.List) and len(n.elts)>=3 and isinstance(n.elts[1],ast.Constant) and n.elts[1].value=="-C" for n in ast.walk(tree)), "源码缺少 Native -C 前置修复"
print("必要源码兼容特征通过；仍须完成后面的授权、回归和真实链路验收")
PY
```

### 2.1 官方 doubao CLI 安装和登录

依据 [官方 AI 安装指南](https://www.doubao.com/work/docs/zh-cn/articles/247733152152-install-cli-agent.md)，默认使用公网 npm latest 渠道；本项目内部资料能力还要求本人有企业账号与相关资源权限。npm 下载认证、doubao 企业登录、lark-cli OAuth 和 Glata 网页登录是四条独立链路，成功一种不能替代其他。

```bash
npm install -g '@bytedance/doubao-cli' --prefix "$HOME/.local/glata-tools" --registry=https://registry.npmjs.org/ --ignore-scripts=false
export PATH="$HOME/.local/glata-tools/bin:$PATH"
export DOUBAO_CLI_BIN="$HOME/.local/glata-tools/bin/doubao"
"$DOUBAO_CLI_BIN" --version
"$DOUBAO_CLI_BIN" --help
"$DOUBAO_CLI_BIN" auth status --verify
```

这里只安装到本人可写的用户目录，不使用 sudo。安装前检查是否已有 CLI 正在工作，已有可用官方 CLI 就复用其确认过的绝对路径，不在生产任务期间覆盖安装。若安装目录已有同名命令，先确认所属包与版本，不自动卸载其它程序。原生包的 SHA-256 校验和安装脚本不能关闭；registry/scope 配置导致实际源不同、npm EACCES、证书或网络失败时按错误修复，不禁用 TLS、不递归 chmod 系统目录。

只有账号未登录或失效时，单独发起以下登录（已验证有效则跳过）：

```bash
"$DOUBAO_CLI_BIN" login --qr=png
```

部署 AI 应保持登录进程可观察，立即展示 QR_IMAGE 返回的实际图片，由本人按提示用飞书/豆包移动端确认企业身份，待命令成功结束后执行下一条。二维码过期就结束该次登录，生成新二维码；不调用 logout 来验证，它会停止其它任务。

```bash
"$DOUBAO_CLI_BIN" auth status --verify
"$DOUBAO_CLI_BIN" model list --json
```

核验账号为本人企业账号、模型列表有且唯一匹配 DeepSeek V4.1 Flash；没有时不换默认模型，不宣布可分析。登录有效仍不证明可搜索内部资料，须在第 7 节用本人的当前问题核验实际企业资料查询与来源。脚本已按当前 CLI 帮助把 -C 前置于 -p 和 --resume。现有脚本采用 always_allow 权限模式，具有在账号授权范围内执行工具的能力；部署者必须了解这一现有行为，AI 报告仍需审阅，不能为安装额外关闭平台保护。

公网 npm 或制品下载不可达时先处理实际网络与包发布问题。企业专用安装渠道按组织授权的独立教程处理，不从公开仓库取得内部源、SSO 命令或他人凭证；不混装第三方同名包。

- Chrome 默认路径为 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，其他路径通过 `GLATA_CHROME_BIN` 指定。
- 新 Mac 必须固定支持 auth 的独立 lark-cli 原生路径；不要把豆包会话临时提供的裁剪版或其注入 token 当作后台部署。见 3.1。
- doubao CLI 使用官方带 scope 的 npm 包；安装和本人登录见本章 2.1。桌面客户端不是 CLI 必需依赖；不要安装裸包 doubao-cli 或把第三方客户端控制工具当作替代品。
- 本地 OCR 首选 macOS Vision：未安装编译器时运行 `xcode-select --install`，完成后 `swiftc -version`，再 `bash build_vision_ocr.sh`。
- Tesseract 是可选兜底，已有 Homebrew 时可 `brew install tesseract tesseract-lang`。缺省不影响 Vision，但 Vision 也不可用时将缺失截图文字。

## 3. 身份与三个会话分别配置

**取 ID 的口径：**Glata 私聊对应监听源 chat_id，不是提醒输出 notify_chat_id。公开副本没有内部截图，禁止从别人的截图抄值。本人在飞书中打开各自会话右上角设置，读取“会话 ID”；必须是该 CLI 应用可读的本人会话。以后台 preflight 实读为准，不能只凭格式匹配认定正确。

提醒/告警是可选：

- `chat_id`：**监听源 Glata p2p**，原来在哪里监听不改变。

（公开副本不附内部截图；请在本人飞书会话设置中读取实际会话 ID，并用本人 CLI 实读核对。）
- `notify_chat_id`：可选，新建一个群只拉你自己和机器人用于**输出提醒**，机器人 @本人＋电话加急。

（公开副本不附内部截图；请在本人飞书会话设置中读取实际会话 ID，并用本人 CLI 实读核对。）
- `alert_reference_chat_id`：**资料参考告警群**，只进入 AI 提示词，无额外扫描；需自己有权查询，不能借用前两项。

（公开副本不附内部截图；请在本人飞书会话设置中读取实际会话 ID，并用本人 CLI 实读核对。）
- `bot_app_id`：旧凭证未含 App ID 时的后备；新凭证应直接写同应用 `app_id`。

环境变量覆盖同名本机配置，详见 README。不要在源码修改个人常量；同事独立填自己的本机配置。`GLATA_CONFIG_FILE` 可指向自定义位置；更改后正常重启才生效。

### 3.1 独立 CLI 用户授权与 target_user_id 获取

后台读取需要独立官方 lark-cli 的持久本人 OAuth；电话应用不作为消息读取身份。官方入口：[larksuite/cli](https://github.com/larksuite/cli)。本次安装路径算法按 1.0.100 已安装制品源码核对；原生程序位于该包 bin/lark-cli，不把 Node 启动脚本复制成独立二进制。已有支持 auth 且持久授权有效的独立版本优先复用，不创建额外应用、不重置电话 Secret。

```bash
npm install -g '@larksuite/cli@1.0.100' --prefix "$HOME/.local/glata-tools" --registry=https://registry.npmjs.org/ --ignore-scripts=false
export LARK_CLI_BIN="$HOME/.local/glata-tools/lib/node_modules/@larksuite/cli/bin/lark-cli"
CLI="$LARK_CLI_BIN"
test -x "$CLI"
"$CLI" --version
"$CLI" auth login --help
"$CLI" config init --help
```

固定路径来自上述 --prefix 的实际包目录，不假设 npx 必然创建 \~/bin/lark-cli。二进制不存在或帮助缺 auth 时停止；确认安装日志/脚本校验成功后再继续。更新 CLI 要现读对应安装指南和 help，不盲跟 latest 导致参数漂移。

**首次 CLI 应用配置：**先判断当前独立 CLI 是否已有本人可用的应用/profile；已有配置不要再 init。确无应用时单独运行下条正常配置向导，由本人选择复用自己有管理权的应用或按组织流程新建。部署 AI 先检查 config init --help 的 Agent 限制；若要求 config bind，按实际已有 Agent 应用进行绑定，不用 force-init 绕过限制。不要把密钥写命令参数或报告。

```bash
"$CLI" config init
```

向导/网页配置完成并成功退出，核对为本人读取应用及正确飞书租户。应用管理者在用户身份侧开放下一节所列最小读取范围，按租户要求发布/生效并把使用范围覆盖本人；本人随后还须 OAuth 同意。机器人 tenant 权限不能替代用户范围。应用配置仅用于该读取 CLI，不覆盖 app_secret.json。

#### 3.1.1 生成授权链接、二维码并完成本人登录

本节是唯一的本人 OAuth 发起路线。继续前面已配置的同一独立 CLI、同一应用；已验证本人授权有效则跳过重新发起，直接做本人查询。以下 read 使用 /bin/bash；AI 非交互环境不能照抄 read 阻塞，应执行 --no-wait --json、展示当次链接/二维码并由本人确认后亲自完成同次 --device-code。不要重复发起另一登录，不复制终端提示符或说明文字。

**步骤 1：向飞书申请本次授权链接。**授权 URL 由飞书 OAuth device flow 返回，不是手工拼接；--no-wait 表示先返回链接，不阻塞等待扫码，--json 便于识别各字段。下面只申请本流程需要的读取与离线续期权限。

**步骤 1A：确认 CLI 命令可用（只查看帮助）。**执行后显示 Usage、Flags 属于正常帮助输出；这一步不会生成授权链接，也没有完成授权。

```bash
# 如是新 Bash 会话，重新设置独立 CLI 路径
CLI="$HOME/.local/glata-tools/lib/node_modules/@larksuite/cli/bin/lark-cli"
export LARK_CLI_BIN="$CLI"
"$CLI" auth login --help
```

帮助 Usage/Flags 只证明命令存在，未发起授权。执行步骤 1B；每次独立 shell 调用需要重新设置 CLI 或使用已确认绝对路径。不得用上一轮过期二维码。

**步骤 1B：真正发起本次本人授权。**下面这条命令才会返回授权链接和本次关联码。只复制代码块内命令，不带终端提示符或说明文字。

```bash
"$CLI" auth login --scope "offline_access im:chat:read im:message:readonly im:message.p2p_msg:get_as_user im:message.group_msg:get_as_user contact:user.base:readonly contact:user:search" --no-wait --json
```

成功应看到 JSON 中的 verification_url、device_code 和 expires_in。没有这三个字段时，不继续生成二维码：若只看到 Usage/Flags，说明仍是查看帮助；若返回 error，先处理该错误。成功后继续步骤 2；不要把真实 device_code 发到聊天或写进共享文档。

输出 JSON 中：verification_url 是供本人打开的授权链接；device_code 是后续完成同一次登录的短期关联码，不是 target_user_id；expires_in 是本次有效期。复制当前返回值，不用以前的值。真实链接和关联码只用于这一次本人登录，不写进共享教程、日志、截图或仓库。

**步骤 2：把返回的授权链接转换成二维码。**先执行 read，再粘贴刚返回的 verification_url 并回车；保留 ?、& 和全部参数，不编码、解码或改写 URL。auth qrcode 在本机把该链接编码为 PNG，不会创建新的授权流程。

```bash
read -r -p "粘贴本次 verification_url：" VERIFY_URL
```

```bash
mkdir -p logs
"$CLI" auth qrcode "$VERIFY_URL" --output ./logs/lark-user-auth.png --size 512
open ./logs/lark-user-auth.png
```

**步骤 3：本人确认授权。**用飞书扫描弹出的二维码，或直接打开 verification_url；核对为本人账号及所需读取权限，然后确认。二维码只放本人本机，不发给同事或公共群；生成图片文件不代表已经授权。

**步骤 4：完成同一次 CLI 登录。**本人确认后，在同一终端执行 read，粘贴步骤 1 返回的同一次 device_code，再执行登录完成命令。read -s 不回显输入；不要开 shell 调试追踪。扫码确认本身不等于钥匙串中已保存用户令牌，这一步不能省略。

```bash
read -r -s -p "粘贴同一次请求的 device_code：" DEVICE_CODE
```

```bash
printf '\n'
"$CLI" auth login --device-code "$DEVICE_CODE"
unset DEVICE_CODE VERIFY_URL
```

如果是由豆包代你发起授权：只需本人扫码确认后回来回复“已授权”，由豆包完成 --device-code 步骤。豆包展示链接和二维码的当轮不会立即阻塞轮询，以免你看不到入口；不要另外发起第二个登录流程。

**步骤 5：验证用户身份，再获取参数 ID。**必须确认 identities.user.status 不为 missing、identities.user.available 为 true，并通过本人查询。bot ready 或单独顶层 verified=true 可能只验证了机器人，不能当作本人授权成功。

```bash
"$CLI" auth status --json --verify
"$CLI" contact +search-user --user-ids me --as user --format json
```

本人查询成功后，按下文说明取 open_id 和 localized_name，进入 3.2 填写 config.local.json。配置完成后运行 python3 deploy_check.py --preflight，通过才启动监控；无需重复跑 AI 或拨电话来验证授权。

**常见卡点：**链接过期或拒绝后，重新执行相同最小 scope 的 --no-wait --json，生成新的链接及二维码，不能继续使用旧 device_code；提示 unknown command auth 时，CLI 路径选中了内置裁剪版，改为独立版本；DNS/网络超时先恢复正常网络，反复扫码不能解决域名解析；授权后 user 仍 missing 时核对步骤 4 是否成功及是否为同一 CLI、同一应用。禁止借用他人 token、降低钥匙串安全级别、修改 DPoP 安全策略或删除去重状态来绕过错误。

本节只保留命令和字段说明，不嵌入任何真实授权链接、device_code 或二维码。logs/lark-user-auth.png 含本次临时登录入口，登录结束或过期后不再复用，不纳入 Git 或分发包。

**target_user_id 获取方法：**在独立 CLI 登录后运行本人查询，不按姓名搜索其他人：

```bash
"$CLI" contact +search-user --user-ids me --as user --format json
# 可选自动探测 Glata p2p（只读联网）；未找到时从本人会话设置读取
python3 deploy_check.py
```

从 data.users 中唯一本人记录取 **open_id** 填 **target_user_id**，**localized_name** 填 **target_user_name**。

不同应用下同一个人 open_id 不同；更换 CLI 应用后要重新取值，target_user_ids 只补其他应用下已确认属于本人的 ID。查询输出有个人信息，只留本机，不复制到共享文档、截图或代码仓库。

**Glata 会话获取：**可以先用 deploy_check.py 的配置建议；未找到 Glata 时不按机器人名字猜，或把 report 人的值班号替代。本人已收到 Glata 通知时，从 p2p 设置取会话 ID，再用同一独立 CLI 按用户身份只读验证。自动探测目前只看有限页且最近卡片可能为空；“没找到卡片”不等于授权失败。输入 ID 后再执行第二个代码块，不把下一命令粘进 read。

```bash
read -r -p "粘贴本人 Glata 私聊会话 ID：" GLATA_P2P
```

```bash
"$CLI" im +chat-messages-list --chat-id "$GLATA_P2P" --page-size 1 --no-reactions --as user --format json
```

### 3.2 获取参数 ID 后填写本机配置

```bash
umask 077
if [ ! -e config.local.json ]; then
  cp config.example.json config.local.json
fi
chmod 600 config.local.json
nano config.local.json
```

在编辑器填写严格 JSON（双引号、无注释、无尾逗号）：

```JSON
{
  "lark_cli_bin": "",
  "chat_id": "",
  "target_user_name": "",
  "target_user_id": "",
  "target_user_ids": [],
  "bot_app_id": "",
  "notify_chat_id": "",
  "alert_reference_chat_id": ""
}
```

模板空值不是可运行配置：把同一读取 CLI 查询到的本人 open_id、localized_name 和可读 Glata p2p 分别填 target_user_id、target_user_name、chat_id；把前文确认的 LARK_CLI_BIN 绝对路径填 lark_cli_bin。路径中不能填 \$HOME 字面量。可用 python3 -c "import os; print(os.environ['LARK_CLI_BIN'])" 在本机获取实际路径。前三项必须真实填写；optional 无需使用保持空字符串，尤其不要填写“通知群ID（可选）”这类说明词。target_user_ids 初始空数组，只有另一个应用下已确认本人的 ID 才补入。

```Bash
python3 project_config.py --check  # 不联网、不输出真实值
```

在 config.local.json 新增 lark_cli_bin 为独立 CLI 的绝对路径，例如 /Users/你的用户名/bin/lark-cli；不要原样复制用户名占位。LARK_CLI_BIN 环境变量优先，固定路径无效或授权失败时不换候选。原有 chat_id 仍为 Glata 私聊；不要因授权失败改群 ID。

```bash
python3 project_config.py --check
python3 deploy_check.py --preflight
# 此处仅预检；完成第4节浏览器登录后，才执行第5节启动
```

一键入口在浏览器与监控启动前验证真实后台授权、本人主 ID/姓名一致、Glata 可读性。失败明确停止，不回落机器人、不清去重状态。电话仍是原可选分支，人工 AI 并发照常。OAuth 需本人完成；只有真实预检通过后才恢复监控，离线测试不代表授权已恢复。

## 4. 专用浏览器首次登录

飞书 CLI 的用户授权与 Glata 网页登录独立。默认 headless 无窗口不会替代扫码登录。

```Bash
bash stop.sh --all
GLATA_BROWSER_MODE=headed python3 cdp_client.py --ensure-browser
```

在专用窗口访问 `https://glata.bytedance.com`，由本人完成登录/验证，确认能打开自己有权限的工单。随后关闭专用实例，复用其 profile 进入后台：

```Bash
python3 cdp_client.py --stop-browser
python3 cdp_client.py --ensure-browser
```

同一 profile 不能同时 headed/headless。默认 profile 是 `~/.glata_cdp_profile`。停止不删除登录数据；不复制他人的 cookie、profile 或 token。CDP 在线只证明浏览器连接，真实工单读取需要单独验收。

## 5. 日常一键启动与停止

三个模式任选其一，**不要连续启动三遍**：

```bash
# 每个新终端：先进入自己的项目并激活 .venv
source .venv/bin/activate
export PATH="$HOME/.local/glata-tools/bin:$PATH"
export DOUBAO_CLI_BIN="$HOME/.local/glata-tools/bin/doubao"
# 以下三个入口任选一条，不连续执行
bash start.sh          # 人工工单自动 AI 分析
# 第6节电话部署完成后，才可选择下面其中一条
# bash start.sh -at      # 人工工单分析和电话提醒
# bash start.sh -at all  # 所有 @本人的工单电话提醒，告警仍不进 AI
```

默认进入关键日志控制台，Ctrl+C 只离开控制台；真正停止用 stop.sh。由其他 AI 部署时使用 GLATA_BG=1 bash start.sh（不带 -at），避免前台控制台让命令超时；启动之后用进程命令与 logs/watch-\* 新日志核验实际存活/扫描，而不是只看 PID 文件。再连接控制台：python3 console.py。已部署电话且用户明确选择时才启用 -at 或 -at all。

```Bash
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
|-|-|
| `im:message:send_as_bot` | 机器人发消息 |
| `im:chat.members:read` | 读取提醒群成员、确认本人在此应用下的 ID |
| `im:message.urgent:phone` | 对自己发送的消息电话加急 |

后台支持批量导入时，核对新增项、身份类型后导入：

```JSON
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
"$LARK_CLI_BIN" im +chat-search --query "你的提醒群名称" --as user --format json
```

CLI 不在 PATH 时使用已探测路径；按本机 `--help` 核对版本。把确认的 ID 写 `config.local.json` 的 `notify_chat_id`。监听 `chat_id` 仍是 Glata p2p。

### 6.4 安全保存应用凭证

从同一应用的凭证与基础信息取 App ID / App Secret，只在本机编辑器填写：

```Bash
umask 077
nano app_secret.json
```

```JSON
{
  "app_id": "cli_替换为自己的应用ID",
  "app_secret": "替换为同一个应用的真实密钥"
}
```

```Bash
chmod 600 app_secret.json
```

不要把密钥写进终端命令行、环境变量、聊天、截图或仓库。已有文件先核对归属，不盲目覆盖。默认读根目录此文件；其他位置通过 `GLATA_BOT_CREDENTIALS` 指定。新部署用标准 JSON；历史单元素写法仅用于兼容，未含 app_id 时必须配置本机后备 App ID。

### 6.5 只读预检与实际验收

```Bash
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

check_release.py 是发行隐私检查，不是账号授权检查。公开源码运行此检查应通过；维护者若本机 DEPLOY.md 保留了飞书内部素材，则不能直接打包或暂存该原版，应使用已审阅的脱敏发布副本。保留原图和隐私检查保护，不用清理用户资料或关闭检查来放行。部署 AI 不自动上传业务文件。

```bash
python3 -m unittest discover -p 'test_*.py' -v
python3 project_config.py --check
python3 deploy_check.py --offline
```

### 真实链路验收（自己有权限的当前工单）

日常自动执行；以下仅首次检查。示例的角括号值需替换，不直接执行原样占位符。

```bash
# 以下为首次验收的参数模板，角括号不可原样执行
# python3 cdp_client.py <当前未关闭人工工单号>
# python3 oncall_ai_summary.py --message-id <该张@本人通知的消息ID> --dry-run
# 已知权威会话/时间时也可：
# python3 oncall_ai_summary.py --chat-id <本单会话ID> --since <转人工前90秒ISO8601时间> --dry-run
```

确认元数据、起点、记录与图中文字不混入其他问题；然后通过新工单自动触发，检查报告可打开、结论证据、内部/告警引用归属和浓缩字数。不要把 `--chat-id` 单独取到的最新一页当成“当前问题精准时间切片”的完整验收。`--dry-run` 仍读取聊天/OCR，只是不调用模型。

| 现象 | 排查与影响 |
|-|-|
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

### 交接给其他 AI 的执行与验收约定

按序执行：检查平台和源码门槛 → 基础软件/原生 Node/Python/venv/OCR → 官方 doubao 登录和模型 → 独立 lark-cli 配置应用及本人 OAuth → 获取本人/会话 ID → 填本机 config → 真实后台预检 → 专用 Glata 浏览器登录 → 无电话模式一键启动 → 新通知自动完成报告。不是先启动，再让 AI 猜修所有失败。

受阻时只请求必要的本人操作：软件安装/系统钥匙串许可、扫码登录、应用权限管理员处理。允许访问的目录和网络必须满足当前执行环境授权；沙箱内失败且确认系统/钥匙串/GUI/网络限制时通过当前平台正常授权后重试，不因 Permission denied 改 chmod 或绕过保护。登录页面/二维码由用户亲自确认，不提取 cookie、token、密码或复用别人身份。

部署验收记录必须说明平台/架构、Python/CLI版本、源码commit与工作区变更；分别记录依赖、me唯一查询、user本地授权、Glata可读、权威转人工时间、记录范围、真实图中文字、监控存活、新人工单自动入队/AI报告、并发期间扫描继续。首次新安装第一轮只建立分析存量基线；等下一张真实新通知，不删除状态、伪造生产消息或自动补跑所有历史。无新工单则写“自动触发/模型完成待验收”，不宣布全流程完成。

内部资料检索要看到实际检索证据和可访问来源，不能只凭模型说“已检索”；告警仅作直接相关辅助来源。输出浓缩问题/补充信息各不超过100字需独立计算核对；现脚本尚未自动强制，模型超长或引用不足属于报告质量未通过，不改模型/提示词字数来掩盖。超时须保留会话及重试证据；电话另行一次真实验收、本人核对来电，不反复拨号。Mac关机/休眠/断网期间本地轮询不能保证及时检测，恢复时最新50条可能不足以追赶全部历史。

**验证范围与限制：**0.1.2 的源码与 CLI 参数兼容回归采用合成数据，本机授权、浏览器、聊天/OCR 和自动分析链路曾分别验证；这不等于已在另一台全新 Mac 上完成实机部署。独立 CLI 的隔离下载试验曾超时，不能标为安装验证通过；当前官方 npm 制品不支持 macOS Intel。输出字数和引用质量仍需人工核对，新部署须逐项完成真实验收。