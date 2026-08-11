"""Start an isolated Wenmo server + headless Edge, then run the CDP smoke test."""

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


ROOT = pathlib.Path(__file__).resolve().parents[1]
EDGE = pathlib.Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def clean_environment():
    # Windows treats environment names case-insensitively.  Some parent shells
    # expose both Path and PATH, which prevents child-process creation.
    result = {}
    for key, value in os.environ.items():
        result[key.upper()] = value
    return result


def wait_json(url, timeout=15):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=1) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError("timed out waiting for %s: %r" % (url, last_error))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    if not EDGE.is_file():
        raise RuntimeError("Microsoft Edge not found")
    qa_root = pathlib.Path(tempfile.mkdtemp(prefix="wenmo-qa-"))
    data_root = qa_root / "data"
    edge_profile = qa_root / "edge"
    data_root.mkdir()
    edge_profile.mkdir()
    env = clean_environment()
    env["WENMO_DATA_DIR"] = str(data_root)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    server_log = open(qa_root / "server.log", "w", encoding="utf-8")
    edge_log = open(qa_root / "edge.log", "w", encoding="utf-8")
    server = edge = None
    succeeded = False
    app_port = free_port()
    cdp_port = free_port()
    try:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "gui_server:app", "--host", "127.0.0.1",
             "--port", str(app_port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=server_log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        wait_json("http://127.0.0.1:%d/api/health" % app_port)
        edge = subprocess.Popen(
            [str(EDGE), "--headless", "--disable-gpu", "--in-process-gpu", "--no-sandbox",
             "--disable-features=UseSkiaRenderer,Vulkan", "--no-first-run",
             "--remote-allow-origins=*", "--remote-debugging-port=%d" % cdp_port,
             "--user-data-dir=" + str(edge_profile), "about:blank"],
            cwd=ROOT, env=env, stdout=edge_log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        wait_json("http://127.0.0.1:%d/json" % cdp_port)
        qa_env = dict(env)
        qa_env.update({
            "WENMO_QA_URL": "http://127.0.0.1:%d" % app_port,
            "WENMO_CDP_URL": "http://127.0.0.1:%d" % cdp_port,
            "WENMO_QA_ARTIFACT_DIR": str(ROOT / ".qa-artifacts"),
        })
        completed = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "browser_qa_cdp.py")],
            cwd=ROOT, env=qa_env, text=True, capture_output=True, timeout=60,
            creationflags=flags,
        )
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        if completed.returncode:
            raise RuntimeError("browser QA failed")
        succeeded = True
    finally:
        for process in (edge, server):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
        server_log.close()
        edge_log.close()
        if not succeeded:
            for log_path in (qa_root / "server.log", qa_root / "edge.log"):
                if log_path.is_file():
                    sys.stderr.write("\n--- %s ---\n%s\n" %
                                     (log_path.name, log_path.read_text(encoding="utf-8", errors="replace")))
        shutil.rmtree(qa_root, ignore_errors=True)


if __name__ == "__main__":
    main()
