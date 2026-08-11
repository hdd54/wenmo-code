# -*- coding: utf-8 -*-
"""分布式 Agent 集群 Worker：独立进程执行任务（P2/P3）

用法：
    python worker.py [--coordinator http://127.0.0.1:8000] [--name worker-1]
    （可部署到任意机器；需与 Coordinator 相同的 CLUSTER_TOKEN 环境变量）

流程：注册 → 心跳（后台）→ 循环 poll 任务 → run_task 执行 → 回传结果。
拉模式：空闲 worker 拉取，天然负载均衡；任务认领超时未回传 → Coordinator 自动重置（故障转移）。
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.request

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 确保能 import cluster

from cluster import run_task  # noqa: E402

TOKEN = os.environ.get("CLUSTER_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("[worker] 必须设置 CLUSTER_TOKEN；拒绝使用公开默认口令")


def _req(url, body=None, headers=None):
    h = {"X-Cluster-Token": TOKEN, "X-Worker-Id": WORKER_ID}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print("[worker] 鉴权失败：CLUSTER_TOKEN 与 Coordinator 不一致", flush=True)
            sys.exit(1)
        return None
    except Exception as e:
        return None


def poll():
    return _req(COORD + "/api/workers/poll")


def send_result(tid, result=None, error=None):
    return _req(COORD + "/api/workers/result", {"id": tid, "result": result, "error": error})


def main():
    global COORD, WORKER_ID
    ap = argparse.ArgumentParser(description="问墨·code 分布式 Agent Worker")
    ap.add_argument("--coordinator", default=os.environ.get("COORDINATOR_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--name", default=os.environ.get("WORKER_NAME", "worker"))
    args = ap.parse_args()
    COORD = args.coordinator.rstrip("/")
    WORKER_ID = args.name

    # 注册 + 心跳线程
    import threading
    _req(COORD + "/api/workers/register", {"name": WORKER_ID})

    def _hb():
        while True:
            _req(COORD + "/api/workers/heartbeat")
            time.sleep(15)
    threading.Thread(target=_hb, daemon=True).start()

    print(f"[worker] {WORKER_ID} 已连接 Coordinator {COORD}", flush=True)
    while True:
        r = poll()
        if not r:
            time.sleep(3)
            continue
        task = r.get("task")
        if not task:
            time.sleep(2)
            continue
        tid = task["id"]
        print(f"[worker] {WORKER_ID} 领取任务 {tid[:8]}", flush=True)
        try:
            result = asyncio.run(run_task(task))   # run_task 是 async，需事件循环
            if result.get("error"):
                send_result(tid, error=result["error"])
                print(f"[worker] {WORKER_ID} 任务 {tid[:8]} 失败: {result['error'][:60]}", flush=True)
            else:
                send_result(tid, result={
                    "output": result.get("output", ""),
                    "tool_calls": result.get("tool_calls", 0),
                    "usage": result.get("usage"),
                })
                print(f"[worker] {WORKER_ID} 任务 {tid[:8]} 完成", flush=True)
        except Exception as e:
            send_result(tid, error="worker 异常: %s" % str(e)[:200])
            print(f"[worker] {WORKER_ID} 任务 {tid[:8]} 异常", flush=True)


if __name__ == "__main__":
    main()
