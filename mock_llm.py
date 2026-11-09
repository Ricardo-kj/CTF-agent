#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一个假的 OpenAI 兼容模型服务，用来在没有 API Key 的情况下跑通全链路。

它会：
  第 1 次被调用 -> 返回一个 bash 工具调用
  第 2 次被调用 -> 直接给出 flag{local-mock-ok}

这样你可以确认「协议层 + 工具循环 + flag 提取」都正常，再去接真模型。

用法：
    python mock_llm.py 18099
    # 另开窗口
    $env:LLM_BASE_URL="http://127.0.0.1:18099/v1"; $env:LLM_API_KEY="test"; python agent.py
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        _ = self.rfile.read(length)
        STATE["n"] += 1
        if STATE["n"] == 1:
            message = {
                "role": "assistant",
                "content": "先确认一下执行环境是否正常。",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash",
                                 "arguments": json.dumps({"command": "echo hello-ctf && uname -s"})},
                }],
            }
        else:
            message = {"role": "assistant",
                       "content": "环境正常，模拟解题完成。\nFLAG: flag{local-mock-ok}"}
        body = json.dumps({
            "id": f"chatcmpl-{STATE['n']}",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18099
    print(f"mock LLM on http://127.0.0.1:{port}/v1")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
