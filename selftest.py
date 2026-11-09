#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地自检：不依赖 Docker，验证 agent.py 的三个端点是否正常工作。

用法：
    python selftest.py

它会用端口 18080 临时拉起 agent.py，依次检查：
    /healthz  -> 200 {"status":"ok"}
    /meta     -> 200 且含 name/version/protocol
    /solve    -> 200 且返回结构化结果（未配 key 时应为 failed + 明确 error）

全部通过退出码 0，否则非 0。
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 18080
BASE = f"http://127.0.0.1:{PORT}"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.loads(r.read())


def main():
    env = dict(os.environ)
    env.pop("LLM_API_KEY", None)
    env["PORT"] = str(PORT)
    # 注意：不要用系统 temp，某些受限环境会在那里被拒绝写；放项目下最稳
    workdir = os.path.join(HERE, ".selftest-work")
    os.makedirs(workdir, exist_ok=True)
    env["WORKDIR"] = workdir
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "agent.py")],
                            env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    failures = []
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                code, body = get("/healthz")
                if code == 200:
                    break
            except Exception:
                time.sleep(0.3)
        else:
            print("FAIL: 服务在 15s 内没有就绪")
            return 1

        code, body = get("/healthz")
        ok = code == 200 and body.get("status") == "ok"
        print(("PASS" if ok else "FAIL"), "GET /healthz ->", code, body)
        ok or failures.append("healthz")

        code, body = get("/meta")
        ok = code == 200 and body.get("name") and body.get("protocol")
        print(("PASS" if ok else "FAIL"), "GET /meta ->", code, body)
        ok or failures.append("meta")

        code, body = post("/solve", {"task_id": "selftest", "category": "misc",
                                     "description": "自检用，未配置模型时应失败但不崩"})
        ok = (code == 200 and body.get("status") in ("failed", "solved")
              and "LLM_API_KEY" in (body.get("error") or ""))
        print(("PASS" if ok else "FAIL"), "POST /solve ->", code,
              {k: body.get(k) for k in ("status", "error")})
        ok or failures.append("solve")
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
        if err:
            print("\n--- 服务端日志（尾部）---")
            print(err.decode("utf-8", "replace")[-2000:])

    if failures:
        print("\n失败项：", ", ".join(failures))
        return 1
    print("\n全部通过。接下来：docker build / push，然后到页面点「注册并检查」。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
