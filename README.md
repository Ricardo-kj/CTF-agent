# ctf-agent

CTFY「测评我的 Agent」用的 Agent 镜像。**推到 GitHub 就自动构建**，模型走 DeepSeek。

- 服务：纯标准库，`/healthz`、`/meta`、`/solve`
- 镜像：非 root、`linux/amd64`、8080、自带 HEALTHCHECK
- 构建：GitHub Actions → GHCR，不需要本机装 Docker

> 协议是按常见 HTTP 约定写的**占位实现**。拿到 CTFY-0001 后只改「协议层」，
> 或者直接跑 `ctfy agent check <镜像>` 按报错对齐。

---

## 一、10 分钟走完

### 1. 建仓库并上传

1. 打开 <https://github.com/new>，Repository name 填 `ctf-agent`，**Public**，不要勾 README。
2. 打开本目录，`Ctrl+A` 全选**里面的内容**（不是拖这一层文件夹），拖进
   仓库页的 **Add file → Upload files**：
   `agent.py`、`Dockerfile`、`README.md`、`.github/`、`.dockerignore`、`.gitignore`、`.env.example`、测试脚本。
3. Commit。

> 两个坑：
> ① 如果拖的是 `agent-starter` 这一层文件夹，文件会变成 `agent-starter/agent.py`，
> GitHub 只认仓库根目录下的 `.github/workflows/`，Actions 就不会触发。
> 传完确认根目录直接能看到 `agent.py` 和 `.github/workflows/build.yml`。
> ② `.github` 是点开头的目录，某些上传方式会漏掉它；漏了可以手动
> **Add file → Create new file**，文件名填 `.github/workflows/build.yml` 再粘内容。

### 2. 等 Actions 跑完

仓库页 → **Actions** → `build-and-push` → 绿勾。约 1–2 分钟。

构建产物就是：

```
ghcr.io/<你的用户名>/ctf-agent:1
```

用户名和仓库名都会自动转小写。

### 3. 把包设为 Public（必做）

GitHub Packages 默认是私有的，平台拉不动。第一次构建完成后：

1. 打开 `https://github.com/users/<你的用户名>/packages/container/ctf-agent/settings`
2. 拉到底 **Danger Zone → Change visibility → Public**

### 4. 把地址贴进测评页

```
ghcr.io/<你的用户名>/ctf-agent:1
```

→ **注册并检查**。

---

## 二、DeepSeek

### 建 Key

1. 打开 <https://platform.deepseek.com/api_keys>，登录。
2. **创建 API key** → 起个名（如 `ctfy`）→ 创建。
3. **立刻复制** `sk-...`，弹窗关掉就看不到了。
4. 确认账户有余额（余额不足会返回 402）。

### 填哪里

- **优先**：测评页第二步「模型」里填。填完后平台会把配置注入容器，`agent.py` 会自动读
  （环境变量 `LLM_*` / `DEEPSEEK_*` / `OPENAI_*`，或任务 JSON 里的 `model` 字段）。
- **不要**把 key 写进 Dockerfile、README 或任何会被提交的文件——镜像会被平台拉走，等于公开。

模型选 **`deepseek-chat`**（支持 function calling）。`deepseek-reasoner` 目前不支持工具调用，
这个 agent 靠工具循环解题，用 reasoner 会拿不到工具返回。

### 本地试

```powershell
$env:LLM_API_KEY = "sk-..."
$env:LLM_MODEL   = "deepseek-chat"
python agent.py
```

```powershell
curl.exe -s -X POST http://127.0.0.1:8080/solve -H "Content-Type: application/json" `
  -d '{\"task_id\":\"t1\",\"category\":\"misc\",\"description\":\"...\"}'
```

---

## 三、不花钱先验证

不需要 Docker、不需要 Key：

```powershell
python selftest.py    # 三个端点
python test_e2e.py    # 假模型跑通「工具循环 -> 提取 flag」
```

---

## 四、文件

| 文件 | 作用 |
| --- | --- |
| `agent.py` | 服务本体：HTTP 层 + 模型循环 + 工具（bash / 读写文件 / HTTP） |
| `Dockerfile` | 非 root、8080、HEALTHCHECK、默认 DeepSeek |
| `.github/workflows/build.yml` | push 即构建，推到 `ghcr.io/<owner>/<repo>:1` |
| `selftest.py` / `test_e2e.py` / `mock_llm.py` | 无需 Key 的本地验证 |

## 五、拿到 CTFY-0001 后改哪里

| 要改的东西 | 位置 |
| --- | --- |
| 路径 / 端口 / 启动命令 | `Handler`、`main()`、`Dockerfile` 的 `EXPOSE` |
| 请求字段名 | `solve()` 开头的 `task.get(...)` |
| 返回字段 | `solve()` 的返回值 |
| 鉴权 / 回调 | `Handler._read_json` 前后 |
| CTF 工具 | `Dockerfile` 里放开 pip / apt 注释 |

## 六、常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| Actions 失败在 login | 仓库 Settings → Actions → Workflow permissions 太严 | 改成 Read and write，或保留 workflow 里的 `permissions:` 块 |
| 平台报 `unauthorized` | 包还是 private | 做第 3 步 |
| 平台报 `manifest unknown` | Actions 没跑成功 / tag 不对 | 打开 Actions 看日志 |
| `no matching manifest` | 架构 | workflow 里已固定 `linux/amd64`，别删 |
| 模型 402 | DeepSeek 余额不足 | 充值 |
| 模型 401 | key 错 / 没注入 | 看 `/meta` 的 `model_configured` |
