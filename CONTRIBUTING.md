# 维护与提交规范

## 每次更新

1. 修改点保持单一，关键边界在代码旁写清楚“为什么”，不要为每一行重复注释。
2. 同步 README/DEPLOY/CHANGELOG 中受影响的入口、配置与失败处理。
3. 运行离线回归、语法检查和发布白名单检查；不要在 CI 中放个人密钥或调用真实电话。
4. `git diff` 审查改动，再仅暂存本次改动文件。不要在业务目录盲用 `git add .`。
5. 使用普通 commit 和明确说明。提交格式 `类型: 改动摘要`；正文列出原因、兼容性、测试覆盖与未验证项。

示例（占位路径须替换为本次文件，不是固定全量上传）：

```bash
python3 -m unittest discover -p 'test_*.py' -v
python3 check_release.py
git status --short
git diff
git add read_oncall_chat.py test_project_config.py
git commit -m "fix: 防止缺失身份配置时误匹配卡片" -m "原因：空姓名不应命中无姓名 mentions。验证：离线合成卡片回归；未调用真实 API。"
git push origin main
```

类型可用 `feat/fix/refactor/docs/test/chore`。不使用无说明的 `update`；不强推，不跳过现有 hooks。

## 更新到本机

```bash
bash stop.sh
git pull --ff-only
python3 -m pip install -r requirements.txt
python3 project_config.py --check
python3 -m unittest discover -p 'test_*.py' -v
bash start.sh  # 如需提醒仍加 -at / -at all
```

拉取失败先检查自己的未提交修改，不用 reset --hard 丢弃工作。`config.local.json`、凭证与运行状态不被 Git 覆盖。停止监控不会立即终止已启动的独立 AI 子进程，更新前应确认相关分析结束。

## 扩展文件

新源文件/测试/文档需加入 `release_files.txt`，并纳入离线回归。分发脚本、隐私扫描与 Git 跟踪范围使用同一份白名单。故障样本必须合成或完全脱敏，不能提交真实消息和工单截图。

本项目未添加开源许可证；私有仓库访问应仅授予经允许的协作者，分发不得超出组织的数据和代码政策。
