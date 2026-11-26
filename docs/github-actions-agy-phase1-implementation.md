# GitHub Actions + Agy CLI 首期开发实施与进度

- 状态：首期已上线，修复首次线上启动问题并等待二次验证
- 更新日期：2026-11-26
- 对应设计：[整体设计文档](github-actions-agy-discussion-design.md)
- Workflow：`Agy Discussion`

## 1. 首期范围

首期实现自动参与以下两类事件：

| 事件 | 行为 | 发布位置 |
| --- | --- | --- |
| `issues.opened` | 分析新 Issue、指出缺失信息、给出初步建议 | Issue Conversation |
| `issue_comment.created` | 回答 Issue 或 PR 普通评论，延续已有讨论 | Issue/PR Conversation |

`issue_comment.created` 同时覆盖 Issue 评论和 PR Conversation 评论。

首期明确不处理：

- Issue 编辑、关闭和重新打开
- PR Review 正文
- PR 代码行评论
- Fork PR Artifact 接力
- 仓库代码修改、命令执行、标签、指派、合并和关闭操作
- 长期记忆或数据库

## 2. 已完成功能

### 2.1 事件与上下文

- 只接受 `issues.opened` 和 `issue_comment.created`。
- 过滤 Bot 发送者和 Agy 自己发布的评论，避免回复循环。
- 读取 Issue/PR 标题、正文、状态、标签和作者关联。
- 读取最近评论，默认最多 20 条。
- 保留之前的 Agy 回复作为讨论历史，但不把 Agy 回复当作新触发。
- 单条评论、Issue 正文和历史数量都有上限，并标记是否截断。
- 事件 ID 由 GitHub 对象 ID 生成，用于发布去重。

### 2.2 Agy 调用

- 使用固定版本 Agy CLI `1.5.18`。
- 使用 `gemini` Provider 和 `GEMINI_API_KEY`。
- 支持可选的 `GOOGLE_GEMINI_BASE_URL`。
- 使用 `agy -p` Headless 模式。
- 使用 `--output-format json` 获取标准执行结果。
- 使用 `--json-schema` 约束 `reply` 和 `skip`。
- `settings.json` 固定 Default 模式，不启用 OS Sandbox，以兼容 GitHub Hosted Runner。
- 模型超时默认 8 分钟，Workflow 总超时 15 分钟。

### 2.3 权限隔离

整个 Workflow 只有一个 Job，但不同步骤的凭据严格分开：

| 步骤 | 可获得的凭据 | 不获得的凭据 |
| --- | --- | --- |
| Context Builder | `GITHUB_TOKEN` | 模型 API Key |
| Agy | `GEMINI_API_KEY`、可选 Endpoint | `GITHUB_TOKEN` |
| Publisher | `GITHUB_TOKEN` | 模型 API Key |

其他安全控制：

- Checkout 固定默认分支，不执行 PR Head。
- Checkout 使用 `persist-credentials: false`，不会把 Git Token持久化到工作区。
- Agy 配置禁止 `read_file(*)`、`write_file(*)`、命令、URL 和 MCP。
- OS Sandbox 在部分 Hosted Runner 上可能无法初始化，因此不作为首期启动前提；所有工具能力仍由 Deny 规则封禁。
- Agy 工作目录放在 Runner 临时目录，只通过 Prompt 接收上下文。
- Publisher 只调用创建普通 Issue/PR 评论的 API。
- 外部评论只作为 Prompt 中的不可信 JSON 数据处理。
- 所有 GitHub Action 依赖固定到完整 Commit SHA。

### 2.4 回复与去重

- 回复统一发布到 Issue/PR Conversation。
- 评论尾部写入不可见事件标记：

```html
<!-- agy:event:comment-created-123456 -->
```

- 发布前分页检查已有评论。
- 同一事件重复运行 Workflow 时不会再次发布。
- Agy 返回 `skip: true` 时只记录日志，不发布评论。
- 非法 JSON、历史状态错误、空回复或超长回复都会失败关闭。

## 3. 文件清单

| 文件 | 作用 |
| --- | --- |
| `.github/workflows/agy-discussion.yml` | 首期 Workflow |
| `.agy/settings.json` | Agy 模型与权限限制 |
| `.agy/response.schema.json` | Agent 输出 JSON Schema |
| `.agy/prompts/system.md` | 公共系统提示词 |
| `.agy/prompts/issue-opened.md` | 新 Issue 提示词 |
| `.agy/prompts/issue-comment.md` | 普通评论提示词 |
| `scripts/agy/install-agy.sh` | 安装固定版本并校验 SHA-512 |
| `scripts/agy/context.mjs` | 读取并标准化 GitHub 上下文 |
| `scripts/agy/run-agent.sh` | 执行 Agy 并校验输出 |
| `scripts/agy/validate-response.mjs` | 响应结构与安全校验 |
| `scripts/agy/publish.mjs` | 去重并发布评论 |
| `scripts/agy/tests/` | 无外部依赖的 Node 单元测试 |

## 4. 运行流程

```text
GitHub Issue/Comment Event
  -> Workflow 条件过滤
  -> 固定默认分支 Checkout
  -> 本地单元测试
  -> Context Builder 读取 GitHub API
  -> Agy 生成结构化 JSON
  -> Validator 校验结果
  -> Publisher 检查去重
  -> 创建 Issue/PR 评论
```

## 5. GitHub 所需配置

### 5.1 Secrets

进入：

`Repository -> Settings -> Secrets and variables -> Actions -> Secrets`

| Secret | 必填 | 说明 |
| --- | --- | --- |
| `GEMINI_API_KEY` | 是 | Gemini API Key |
| `GOOGLE_GEMINI_BASE_URL` | 否 | Gemini 兼容中转服务根地址 |

如果使用官方 Gemini Endpoint，只配置 `GEMINI_API_KEY`。

如果使用中转服务：

- `GEMINI_API_KEY` 填中转服务提供的 Key。
- `GOOGLE_GEMINI_BASE_URL` 填服务根地址。
- 不要自行追加 Chat Completions 路径。

### 5.2 Variables

进入同一页面的 `Variables`：

| Variable | 推荐值 | 说明 |
| --- | --- | --- |
| `AGY_DISCUSSION_ENABLED` | `true` | 全局开关，设置为 `false` 可立即停用 |
| `AGY_MAX_COMMENTS` | `20` | 读取最近评论数 |
| `AGY_MAX_COMMENT_CHARS` | `4000` | 单条评论上下文上限 |
| `AGY_MAX_REPLY_CHARS` | `12000` | Agent 回复上限 |
| `AGY_MODEL_TIMEOUT_MINUTES` | `8` | 模型调用超时 |
| `AGY_MODEL` | 留空或固定模型 | 可选，留空使用 CLI 默认模型 |

这些 Variable 都有代码内默认值。不是每一项都必须配置，但建议明确设置 `AGY_DISCUSSION_ENABLED`。

### 5.3 Actions 权限

进入：

`Repository -> Settings -> Actions -> General`

确认：

1. GitHub Actions 已启用。
2. Workflow permissions 允许当前 Workflow 使用所需的 Issue/PR 写权限。
3. 如果仓库限制第三方 Action，允许 `actions/checkout` 和 `actions/setup-node`。

Workflow 自身已经声明：

```yaml
permissions:
  contents: read
  issues: write
  pull-requests: write
```

如果组织或仓库策略强制只读，Workflow 即使写了上述权限也可能无法发布评论。

## 6. 进度

### 已完成

- [x] 按设计文档确定首期边界
- [x] 实现单 Workflow
- [x] 实现 Issue/PR 普通评论上下文读取
- [x] 实现 Agy 权限限制和固定版本安装
- [x] 实现结构化输出校验
- [x] 实现评论发布和事件去重
- [x] 实现 Bot 与自回复过滤
- [x] 加入全局开关、上下文限制和超时
- [x] 编写本地单元测试
- [x] 完成本地语法和格式检查

### 等待执行

- [x] 提交并推送到 GitHub 默认分支
- [x] 在 GitHub 创建测试 Issue
- [ ] 验证新 Issue 自动分析
- [ ] 验证 Issue 评论自动回复
- [ ] 验证 PR Conversation 评论自动回复
- [ ] 验证 Workflow 重跑不重复评论
- [ ] 验证外部贡献者评论
- [ ] 根据第一次线上运行固定或调整模型与预算
- [ ] 二次运行确认 Agy 可正常生成响应

## 7. 本地验证结果

本轮实际执行：

- `node --test scripts/agy/tests/*.test.mjs`：12 项通过。
- `.agy/settings.json` 和 `.agy/response.schema.json`：JSON 解析通过。
- `.agy/settings.json`：配置键和关键枚举与官方 Settings Schema 核对通过。
- `context.mjs`、`validate-response.mjs`、`publish.mjs`：`node --check` 通过。
- `install-agy.sh` 和 `run-agent.sh`：Git Bash `bash -n` 通过。
- Workflow：PyYAML 结构解析和 `actionlint 1.7.12` 检查通过。
- `git diff --check`：通过。

修复前的首次 GitHub Runner 运行已经执行但在 Agy 步骤失败。本轮修复后尚未进行二次线上验证，也没有在本地调用真实 Gemini API。

## 8. 首次线上运行记录

首次线上运行：

- Run ID：`45828721736`
- Job ID：`139939530144`
- 提交：`31e8b1d`

结果：

- Checkout、Node Setup、本地测试、Context Builder 和 Agy 安装成功。
- `Run Agy without GitHub token` 在约 1 秒后以退出码 `3` 结束，尚未进入发布步骤。
- 同次运行出现 `actions/checkout@v4` 和 `actions/setup-node@v4` 的 Node.js 20 弃用警告。

修复：

- 移除 Agy 的 `--sandbox` 启动参数，并关闭 `enableTerminalSandbox`，避免 Hosted Runner 的 Sandbox 初始化导致启动失败。
- Agy 仍禁止读取文件、写文件、执行命令、访问 URL 和调用 MCP。
- 将 Checkout 和 Setup Node 升级到 v5，并继续固定到完整 Commit SHA。
- 增加 `GEMINI_API_KEY` 前置检查，缺失时直接给出明确 Actions 错误。
- 增加 Gemini Endpoint 格式检查。
- Agy 非零退出时输出 stderr 和结构化错误 JSON，下一次失败可以直接看到真实原因。

## 9. 上线操作

### 9.1 推送

将本轮修改合并到仓库默认分支 `main`。

Workflow 只有在默认分支包含 `.github/workflows/agy-discussion.yml` 后，才会对后续 Issue 和评论事件生效。

### 9.2 创建测试 Issue

在仓库中创建一个普通测试 Issue，例如：

```text
标题：测试 Agy 自动讨论
正文：请说明这个项目的窗口遮挡监控如何处理多显示器。
```

预期：

1. 仓库 Actions 页面出现 `Agy Discussion` 运行。
2. `Prepare GitHub context` 成功。
3. `Run Agy without GitHub token` 成功。
4. Issue 下出现一条 Agy 分析评论。

### 9.3 测试普通评论

在测试 Issue 下继续回复：

```text
请进一步说明为什么监控只处理当前虚拟桌面。
```

预期 Agy 会基于新评论和之前的讨论继续回答。

### 9.4 测试 PR Conversation

在任意 PR 的 Conversation 中发表普通评论。不要使用代码行评论，代码行评论属于第二阶段。

预期 Agy 在 PR Conversation 中回复。

### 9.5 测试去重

进入已经成功的 Workflow 运行页面，选择 `Re-run jobs`。

预期 Publisher 检测到相同事件标记并跳过，不产生第二条评论。

## 10. 故障排查

| 现象 | 检查项 |
| --- | --- |
| Workflow 没有启动 | Workflow 是否在默认分支；Actions 是否启用；触发事件是否匹配 |
| 报 `GEMINI_API_KEY is not set` | Secret 名称是否为 `GEMINI_API_KEY` |
| 报 `Gemini API key missing` | Secret 是否在 Repository Secrets 中，值是否为空 |
| 模型请求失败 | Key 是否有效；中转地址是否正确；模型额度是否可用 |
| 报 `Invalid Gemini endpoint` | `GOOGLE_GEMINI_BASE_URL` 是否以 `http://` 或 `https://` 开头 |
| 发布评论返回 403 | Actions 或组织策略是否允许 Issue/PR 写权限 |
| 没有评论但 Workflow 成功 | Agent 是否返回 `skip: true`；Publisher 是否命中重复事件 |
| 每次运行都失败在安装 | 固定下载地址、Runner 网络和 SHA-512 是否正常 |
| 临时停用 | 将 `AGY_DISCUSSION_ENABLED` 设为 `false` |

## 11. 第二阶段入口

首期线上稳定后，再增加：

- `pull_request_review_comment.created`
- Review Thread 根评论解析
- Review Reply API
- Fork PR 的 Intake/Dispatch Workflow
- Diff 和单个文件片段上下文

在首期验证完成前，不建议同时引入 Review Thread 和 Fork Artifact 接力，避免无法区分问题来自模型、权限还是事件关联。
