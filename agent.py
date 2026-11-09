#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CTFY Agent Starter —— 按「常见约定」写的可提交起点。

它只依赖 Python 标准库，容器里不需要 pip install，构建快、失败点少。

对外契约（占位约定，拿到 CTFY-0001 后按文档改这一层即可）：

    GET  /healthz   -> 200 {"status": "ok"}          # 平台健康检查
    GET  /meta      -> 200 {"name","version","protocol","model"}
    POST /solve     -> 200 {"status","flag","steps","trace","error"}
    POST /v1/solve  -> 同 /solve（别名，兼容不同调用方）

POST /solve 请求体（字段名可按文档调整，多余字段会被忽略）：

    {
      "task_id":     "ctfy-0001",
      "category":    "web",
      "description": "题目描述……",
      "target":      "http://10.0.0.5:8080",
      "flag_format": "flag{...}",
      "files": [ {"name": "a.zip", "url": "https://..."}        # 或
                 {"name": "a.zip", "content_b64": "UEsDBA..."} ]
    }

响应体：

    {"status": "solved" | "failed", "flag": "flag{...}" 或 "",
     "steps": 12, "trace": ["...", "..."], "error": ""}

环境变量（也接受 DEEPSEEK_* / OPENAI_* 同名变量）：

    LLM_BASE_URL   默认 https://api.deepseek.com/v1 （OpenAI 兼容接口）
    LLM_API_KEY    必填，模型密钥（也可用 DEEPSEEK_API_KEY / OPENAI_API_KEY）
    LLM_MODEL      默认 deepseek-chat
    PORT           默认 8080
    WORKDIR        默认 /workspace
    MAX_STEPS      默认 40
    FLAG_REGEX     默认 [A-Za-z0-9_]{2,32}\\{[^}\\n]{1,200}\\}

任务里如果带 model/llm 字段（平台在第二步下发模型配置时常见），会覆盖上面这些。

注意：本文件是「骨架」，真正的解题质量取决于模型与工具，协议层请以
CTFY-0001 为准（见 README 的「拿到协议后改哪里」）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1.0"
PROTOCOL = "ctfy-agent/0-draft"

BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "") \
    or os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("LLM_MODEL", "") or os.environ.get("DEEPSEEK_MODEL", "") \
    or os.environ.get("OPENAI_MODEL", "") or "deepseek-chat"
PORT = int(os.environ.get("PORT", "8080"))
WORKDIR = Path(os.environ.get("WORKDIR", "/workspace"))
MAX_STEPS = int(os.environ.get("MAX_STEPS", "40"))
FLAG_RE = re.compile(
    os.environ.get("FLAG_REGEX", r"[A-Za-z0-9_]{2,32}\{[^}\n]{1,200}\}")
)

MAX_OUTPUT = 8000          # 单次工具输出截断
BASH_TIMEOUT = 120         # 单条命令超时（秒）

log_lock = threading.Lock()


def log(*args):
    with log_lock:
        print("[agent]", *args, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "在工作目录执行 shell 命令，返回 stdout+stderr。用于分析附件、跑脚本、连目标。",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作目录下的文本文件（可带行号偏移）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "description": "起始行，默认 0"},
                    "limit": {"type": "integer", "description": "最多读取行数，默认 300"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入工作目录下的文件（覆盖）。",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": "发一个 HTTP 请求，返回状态码与响应体（前 8000 字符）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "HEAD"]},
                    "headers": {"type": "object", "description": "请求头键值对"},
                    "body": {"type": "string", "description": "请求体（字符串）"},
                },
                "required": ["url"],
            },
        },
    },
]


def _safe_path(name: str) -> Path:
    p = (WORKDIR / name).resolve()
    if WORKDIR.resolve() not in p.parents and p != WORKDIR.resolve():
        raise ValueError(f"路径越界: {name}")
    return p


def tool_bash(args: dict) -> str:
    cmd = args.get("command", "")
    if not cmd:
        return "错误：command 为空"
    try:
        r = subprocess.run(
            cmd, shell=True, cwd=str(WORKDIR), capture_output=True,
            text=True, timeout=BASH_TIMEOUT, errors="replace",
        )
        out = (r.stdout or "") + (r.stderr or "")
        return out[:MAX_OUTPUT] or f"（无输出，退出码 {r.returncode}）"
    except subprocess.TimeoutExpired:
        return f"超时（>{BASH_TIMEOUT}s）"


def tool_read_file(args: dict) -> str:
    p = _safe_path(args.get("path", ""))
    offset = int(args.get("offset", 0) or 0)
    limit = int(args.get("limit", 300) or 300)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[offset: offset + limit])[:MAX_OUTPUT]
    except Exception as e:  # noqa: BLE001
        return f"读取失败：{e}"


def tool_write_file(args: dict) -> str:
    p = _safe_path(args.get("path", ""))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(args.get("content", ""), encoding="utf-8")
    return f"已写入 {p}（{len(args.get('content', ''))} 字符）"


def tool_http_request(args: dict) -> str:
    url = args.get("url", "")
    method = (args.get("method") or "GET").upper()
    data = args.get("body")
    data = data.encode() if isinstance(data, str) and data else None
    headers = args.get("headers") or {}
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={**headers, "User-Agent": "ctfy-agent/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(MAX_OUTPUT)
        return f"HTTP {r.status}\n" + body.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}\n" + e.read(MAX_OUTPUT).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return f"请求失败：{e}"


TOOL_IMPL = {
    "bash": tool_bash,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "http_request": tool_http_request,
}


# --------------------------------------------------------------------------
# 模型调用（OpenAI 兼容 /chat/completions，默认 DeepSeek）
# --------------------------------------------------------------------------

def model_conf(task: dict) -> dict:
    """模型配置优先级：任务里带的 > 环境变量默认值。

    平台如果按任务下发模型（task["model"] 或 task["llm"]），这里直接用，
    这样第二步「模型」怎么注入都能接上。
    """
    m = task.get("model") or task.get("llm") or {}
    if isinstance(m, dict) and m:
        return {
            "base_url": str(m.get("base_url") or m.get("baseUrl") or BASE_URL).rstrip("/"),
            "api_key": m.get("api_key") or m.get("apiKey") or m.get("key") or API_KEY,
            "model": m.get("model") or m.get("name") or MODEL,
        }
    return {"base_url": BASE_URL, "api_key": API_KEY, "model": MODEL}


def llm_chat(messages: list, tools: list | None = None, conf: dict | None = None) -> dict:
    conf = conf or {"base_url": BASE_URL, "api_key": API_KEY, "model": MODEL}
    if not conf.get("api_key"):
        raise RuntimeError("未配置模型密钥（LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY）")
    payload = {"model": conf["model"], "messages": messages, "temperature": 0.2}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{conf['base_url']}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {conf['api_key']}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        data = json.load(r)
    return data["choices"][0]["message"]


# --------------------------------------------------------------------------
# 解题主流程
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个 CTF 解题 Agent，运行在隔离的 Linux 容器里，工作目录是当前目录。

你可以调用工具：bash、read_file、write_file、http_request。
规则：
1. 先看题目描述、附件、目标地址，再决定攻击面；不要空谈。
2. 每一步都要有明确目的，失败就换思路，不要重复同样的命令。
3. 找到 flag 后立刻停止，并在回答里单独一行输出：FLAG: <完整 flag>
4. 找不到就说明已排除的方向，不要编造 flag。
"""


def materialize_files(task: dict, task_dir: Path) -> list[str]:
    """把任务附件落到磁盘，返回落盘清单。"""
    saved = []
    for f in task.get("files") or []:
        name = os.path.basename(f.get("name") or "attachment.bin")
        dst = task_dir / name
        try:
            if f.get("content_b64"):
                dst.write_bytes(base64.b64decode(f["content_b64"]))
            elif f.get("url"):
                urllib.request.urlretrieve(f["url"], dst)
            else:
                continue
            saved.append(name)
        except Exception as e:  # noqa: BLE001
            log("附件落盘失败", name, e)
    return saved


def solve(task: dict) -> dict:
    global WORKDIR
    task_id = str(task.get("task_id") or f"task-{int(time.time())}")
    task_dir = WORKDIR / task_id

    prev, WORKDIR = WORKDIR, task_dir
    try:
        task_dir.mkdir(parents=True, exist_ok=True)
        files = materialize_files(task, task_dir)
        brief = {
            "task_id": task_id,
            "category": task.get("category") or "unknown",
            "description": task.get("description") or "",
            "target": task.get("target") or "",
            "flag_format": task.get("flag_format") or "",
            "files": files,
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "任务信息（JSON）：\n" + json.dumps(brief, ensure_ascii=False, indent=2)},
        ]
        trace: list[str] = []
        flag = ""
        conf = model_conf(task)

        for step in range(1, MAX_STEPS + 1):
            msg = llm_chat(messages, TOOLS, conf)
            messages.append(msg)

            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""

            if content:
                m = FLAG_RE.search(content)
                if m:
                    flag = m.group(0)
                    trace.append(f"step {step}: 模型直接给出 flag")
                    break

            if not tool_calls:
                trace.append(f"step {step}: 模型未调用工具，结束")
                break

            for call in tool_calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                impl = TOOL_IMPL.get(name)
                result = impl(args) if impl else f"未知工具 {name}"
                trace.append(f"step {step}: {name}({str(args)[:120]})")
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": result,
                })
                m = FLAG_RE.search(result)
                if m:
                    flag = m.group(0)

            if flag:
                trace.append(f"step {step}: 工具输出里命中 flag")
                break

        if not flag:
            # 兜底：把整个对话再扫一遍
            blob = json.dumps(messages, ensure_ascii=False)
            m = FLAG_RE.search(blob)
            if m:
                flag = m.group(0)

        return {
            "status": "solved" if flag else "failed",
            "flag": flag,
            "steps": len(trace),
            "trace": trace[-60:],
            "error": "",
        }
    except Exception as e:  # noqa: BLE001
        log("solve 异常", traceback.format_exc())
        return {"status": "failed", "flag": "", "steps": 0, "trace": [],
                "error": f"{type(e).__name__}: {e}"}
    finally:
        WORKDIR = prev


# --------------------------------------------------------------------------
# HTTP 层
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ctfy-agent/" + VERSION

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            self._json(200, {"status": "ok"})
        elif path == "/meta":
            self._json(200, {
                "name": os.environ.get("AGENT_NAME", "ctfy-agent-starter"),
                "version": VERSION,
                "protocol": PROTOCOL,
                "model": MODEL,
                "model_base_url": BASE_URL,
                "model_configured": bool(API_KEY),
            })
        elif path == "/":
            self._json(200, {"service": "ctfy-agent-starter", "endpoints":
                             ["/healthz", "/meta", "/solve"]})
        else:
            self._json(404, {"error": "not found", "path": path})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path not in ("/solve", "/v1/solve", "/run"):
            self._json(404, {"error": "not found", "path": path})
            return
        try:
            task = self._read_json()
        except Exception as e:  # noqa: BLE001
            self._json(400, {"error": f"bad json: {e}"})
            return
        log("收到任务", task.get("task_id"), task.get("category"))
        try:
            result = solve(task)
        except Exception as e:  # noqa: BLE001  永远不要把连接直接挂断
            log("solve 未捕获异常", traceback.format_exc())
            result = {"status": "failed", "flag": "", "steps": 0, "trace": [],
                      "error": f"{type(e).__name__}: {e}"}
        self._json(200, result)

    def log_message(self, fmt, *args):  # 静默默认访问日志
        log("http", fmt % args)


def main():
    # 容器 / Windows 控制台编码不一致时，别让一条日志把请求线程带崩
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    WORKDIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on 0.0.0.0:{PORT}, workdir={WORKDIR}, model={MODEL}, "
        f"key={'set' if API_KEY else 'MISSING'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
