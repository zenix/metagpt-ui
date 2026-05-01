import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI()

CONTAINER_NAME = "metagpt"
WORKSPACE_HOST = Path.home() / "sw" / "metagpt-workspace"
WORKSPACE_CTR = "/app/metagpt/workspace"
CONFIG_HOST = Path.home() / ".metagpt" / "config2.yaml"
METAGPT_DIR_HOST = Path.home() / ".metagpt"
METAGPT_DIR_CTR = "/root/.metagpt"
IMAGE = "metagpt/metagpt:latest"

MODEL_IDS = {
    "heavy": "openai/google/gemma-4-26b-a4b",
    "fast": "openai/google/gemma-4-e4b-it",
    "gemma3": "google/gemma-3-27b-it-qat",
}

_config_lock = asyncio.Lock()
_runs: dict[str, asyncio.Queue] = {}


# ── Static files ─────────────────────────────────────────────────────────────

@app.get("/")
async def index():
    return FileResponse(Path(__file__).parent / "index.html")


# ── Projects ──────────────────────────────────────────────────────────────────

@app.get("/api/projects")
async def list_projects():
    if not WORKSPACE_HOST.exists():
        return []
    projects = []
    for d in sorted(WORKSPACE_HOST.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        src_count = sum(
            1 for _ in d.rglob("*")
            if _.suffix in {".py", ".js", ".ts", ".html"} and _.is_file()
        )
        req_file = d / "docs" / "requirement.txt"
        snippet = ""
        if req_file.exists():
            try:
                snippet = req_file.read_text().splitlines()[0][:80]
            except Exception:
                pass
        incremental = (d / ".dependencies.json").exists()
        projects.append({
            "name": d.name,
            "modified": d.stat().st_mtime,
            "file_count": src_count,
            "snippet": snippet,
            "incremental": incremental,
        })
    return projects


# ── Container ─────────────────────────────────────────────────────────────────

def _container_status() -> dict:
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "-f",
             "{{.State.Status}}|{{.State.StartedAt}}",
             CONTAINER_NAME],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        status, started_at = out.split("|", 1)
        return {"exists": True, "running": status == "running", "status": status, "started_at": started_at}
    except subprocess.CalledProcessError:
        return {"exists": False, "running": False, "status": "not found", "started_at": ""}


@app.get("/api/container")
async def container_status():
    return _container_status()


@app.post("/api/container/start")
async def container_start():
    info = _container_status()
    if info["running"]:
        return {"ok": True, "message": "already running"}
    if info["exists"]:
        subprocess.run(["docker", "start", CONTAINER_NAME], check=True)
    else:
        WORKSPACE_HOST.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "docker", "run", "--name", CONTAINER_NAME, "-d",
            "--net=host", "--privileged",
            "-v", f"{METAGPT_DIR_HOST}:{METAGPT_DIR_CTR}",
            "-v", f"{WORKSPACE_HOST}:{WORKSPACE_CTR}",
            IMAGE,
        ], check=True)
    return {"ok": True, "message": "started"}


@app.post("/api/container/stop")
async def container_stop():
    try:
        subprocess.run(["docker", "stop", CONTAINER_NAME], check=True)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/container/restart")
async def container_restart():
    try:
        subprocess.run(["docker", "restart", CONTAINER_NAME], check=True)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/container/remove")
async def container_remove():
    try:
        info = _container_status()
        if info["running"]:
            subprocess.run(["docker", "stop", CONTAINER_NAME], check=True)
        if info["exists"]:
            subprocess.run(["docker", "rm", CONTAINER_NAME], check=True)
        return {"ok": True}
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Run ───────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    idea: str
    project: str = ""
    model: str = "heavy"
    rounds: int = 5
    code_review: bool = True
    run_tests: bool = False


def _write_config(model_id: str):
    CONFIG_HOST.write_text(f"""llm:
  api_type: "openai"
  base_url: "http://localhost:1234/v1"
  api_key: "lm-studio"
  model: "{model_id}"
  use_system_prompt: true

code_execution:
  backend: "docker"
  image: "python:3.11-slim"
  timeout: 600

mermaid:
  engine: "nodejs"
  path: "mmdc"
  puppeteer_config: "/root/.metagpt/puppeteer-config.json"
""")


def _derive_project_name(idea: str) -> str:
    import re
    words = re.findall(r'\w+', idea.lower())[:4]
    return "_".join(words)


@app.post("/api/run")
async def start_run(req: RunRequest):
    if req.model not in MODEL_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown model alias: {req.model}")

    project = req.project or _derive_project_name(req.idea)
    model_id = MODEL_IDS[req.model]
    run_id = str(uuid.uuid4())
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    _runs[run_id] = queue

    asyncio.create_task(_execute_run(req, project, model_id, queue))
    return {"run_id": run_id, "project": project}


async def _execute_run(req: RunRequest, project: str, model_id: str, queue: asyncio.Queue):
    project_host = WORKSPACE_HOST / project
    project_ctr = f"{WORKSPACE_CTR}/{project}"

    cmd_parts = ["metagpt", "--project-name", project, "--n-round", str(req.rounds)]
    if req.code_review:
        cmd_parts.append("--code-review")
    else:
        cmd_parts.append("--no-code-review")
    if req.run_tests:
        cmd_parts.append("--run-tests")
    if project_host.exists() and (project_host / ".dependencies.json").exists():
        cmd_parts += ["--project-path", project_ctr]
        await queue.put("[metagpt-ui] Existing project — incremental mode\n")
    else:
        await queue.put("[metagpt-ui] New project — fresh mode\n")

    cmd_parts.append(req.idea)
    bash_cmd = " ".join(f'"{p}"' if " " in p else p for p in cmd_parts)

    original_config = CONFIG_HOST.read_text()
    try:
        async with _config_lock:
            _write_config(model_id)
            await queue.put(f"[metagpt-ui] Model set to: {req.model} ({model_id})\n")

        # Ensure container running
        info = _container_status()
        if not info["running"]:
            await queue.put("[metagpt-ui] Starting container...\n")
            if info["exists"]:
                subprocess.run(["docker", "start", CONTAINER_NAME], check=True)
            else:
                WORKSPACE_HOST.mkdir(parents=True, exist_ok=True)
                subprocess.run([
                    "docker", "run", "--name", CONTAINER_NAME, "-d",
                    "--net=host", "--privileged",
                    "-v", f"{METAGPT_DIR_HOST}:{METAGPT_DIR_CTR}",
                    "-v", f"{WORKSPACE_HOST}:{WORKSPACE_CTR}",
                    IMAGE,
                ], check=True)
            await asyncio.sleep(2)

        await queue.put(f"[metagpt-ui] Running: {bash_cmd}\n\n")

        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", CONTAINER_NAME, "bash", "-c", bash_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        assert proc.stdout is not None
        async for line in proc.stdout:
            await queue.put(line.decode("utf-8", errors="replace"))

        await proc.wait()
        await queue.put(f"\n[metagpt-ui] Process exited with code {proc.returncode}\n")
    except Exception as e:
        await queue.put(f"\n[metagpt-ui] ERROR: {e}\n")
    finally:
        CONFIG_HOST.write_text(original_config)
        await queue.put(None)  # sentinel — stream done


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str):
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="Run not found")
    queue = _runs[run_id]

    async def event_generator() -> AsyncIterator[str]:
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    yield "data: [DONE]\n\n"
                    break
                for line in chunk.splitlines(keepends=True):
                    safe = line.replace("\n", " ").replace("\r", "")
                    yield f"data: {safe}\n\n"
        finally:
            _runs.pop(run_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
