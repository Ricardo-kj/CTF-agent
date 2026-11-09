#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端自检：假模型 + 真 agent，验证「工具循环 -> flag 提取 -> HTTP 返回」。

用法：
    python test_e2e.py

期望输出末行：E2E PASS  flag=flag{local-mock-ok}
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LLM_PORT = 18099
AGENT_PORT = 18098


def post(url, payload, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read())


def main():
    workdir = os.path.join(HERE, ".e2e-work")
    os.makedirs(workdir, exist_ok=True)

    llm = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_llm.py"), str(LLM_PORT)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    env = dict(os.environ)
    env.update({
        "PORT": str(AGENT_PORT),
        "WORKDIR": workdir,
        "LLM_BASE_URL": f"http://127.0.0.1:{LLM_PORT}/v1",
        "LLM_API_KEY": "test-key",
        "LLM_MODEL": "mock",
        "MAX_STEPS": "6",
    })
    agent = subprocess.Popen([sys.executable, os.path.join(HERE, "agent.py")],
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        base = f"http://127.0.0.1:{AGENT_PORT}"
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/healthz", timeout=2)
                break
            except Exception:
                time.sleep(0.3)
        code, body = post(base + "/solve", {
            "task_id": "e2e", "category": "misc",
            "description": "随便说说这题要干什么",
            "flag_format": "flag{...}",
        })
        ok = code == 200 and body.get("status") == "solved" and body.get("flag") == "flag{local-mock-ok}"
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print("E2E PASS  flag=" + body.get("flag", "") if ok else "E2E FAIL")
        return 0 if ok else 1
    finally:
        for p in (agent, llm):
            p.terminate()
        try:
            _, err = agent.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            agent.kill()
            _, err = agent.communicate()
        if err:
            print("--- agent stderr ---")
            print(err.decode("utf-8", "replace")[-1500:])


if __name__ == "__main__":
    sys.exit(main())
