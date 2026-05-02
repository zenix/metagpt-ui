import asyncio
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query, Request
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
LOGS_DIR = WORKSPACE_HOST / ".logs"
UI_MODELS_PATH = METAGPT_DIR_HOST / "ui_models.json"

MODEL_IDS = {
    "qwen3-14b": "openai/qwen/qwen3-14b",
    "heavy": "openai/google/gemma-4-26b-a4b",
    "fast": "openai/google/gemma-4-e4b-it",
    "gemma3": "google/gemma-3-27b-it-qat",
}

SOURCE_EXTS = {".py", ".js", ".ts", ".html", ".css", ".json", ".md",
               ".txt", ".mmd", ".yaml", ".toml"}

_config_lock = asyncio.Lock()
_run_buffers: dict[str, list[str]] = {}   # run_id → all log chunks
_run_done: dict[str, bool] = {}           # run_id → finished flag
_run_meta: dict[str, str] = {}            # run_id → project name
_run_procs: dict[str, asyncio.subprocess.Process] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _guard_project(name: str) -> Path:
    if not name or "/" in name or ".." in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="Invalid project name")
    path = (WORKSPACE_HOST / name).resolve()
    workspace = WORKSPACE_HOST.resolve()
    if not (path == workspace or workspace in path.parents):
        raise HTTPException(status_code=400, detail="Path traversal detected")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Project not found")
    return path


def _guard_file_path(project_path: Path, rel: str) -> Path:
    if not rel or ".." in rel:
        raise HTTPException(status_code=400, detail="Invalid file path")
    p = (project_path / rel).resolve()
    if project_path.resolve() not in p.parents and p != project_path.resolve():
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return p


def _load_models() -> dict[str, str]:
    try:
        if UI_MODELS_PATH.exists():
            return json.loads(UI_MODELS_PATH.read_text())
    except Exception:
        pass
    return dict(MODEL_IDS)


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
        if not d.is_dir() or d.name.startswith("."):
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


@app.delete("/api/projects/{project_name}")
async def delete_project(project_name: str):
    path = _guard_project(project_name)
    info = _container_status()
    if info["running"]:
        ctr_path = f"{WORKSPACE_CTR}/{project_name}"
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "rm", "-rf", ctr_path],
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500,
                detail=f"Container delete failed: {result.stderr.decode()}")
    else:
        try:
            shutil.rmtree(path)
        except PermissionError as e:
            raise HTTPException(status_code=500,
                detail=f"Permission denied (files are root-owned — start container first): {e}")
    return {"ok": True}


@app.get("/api/projects/{project_name}/summary")
async def project_summary(project_name: str):
    path = _guard_project(project_name)

    def _newest_json(subdir: str):
        d = path / "docs" / subdir
        if not d.exists():
            return None
        files = sorted(d.glob("*.json"))
        if not files:
            return None
        try:
            return json.loads(files[-1].read_text())
        except Exception:
            return None

    prd  = _newest_json("prd")
    sd   = _newest_json("system_design")
    task = _newest_json("task")
    cp   = _newest_json("code_plan_and_change")

    return {
        "prd": {
            "Project Name": prd.get("Project Name"),
            "Refined Requirements": prd.get("Refined Requirements"),
            "Refined User Stories": prd.get("Refined User Stories"),
        } if prd else None,
        "system_design": {
            "Refined Data structures and interfaces": sd.get("Refined Data structures and interfaces"),
            "Refined File list": sd.get("Refined File list"),
        } if sd else None,
        "task": {
            "Required packages": task.get("Required packages"),
            "Refined Task list": task.get("Refined Task list"),
        } if task else None,
        "code_plan": {
            "Development Plan": cp.get("Development Plan"),
            "Incremental Change": cp.get("Incremental Change"),
        } if cp else None,
    }


@app.get("/api/projects/{project_name}/files")
async def list_project_files(project_name: str):
    path = _guard_project(project_name)
    result = []
    for f in sorted(path.rglob("*")):
        if not f.is_file() or ".git" in f.parts:
            continue
        try:
            result.append({
                "path": str(f.relative_to(path)),
                "size": f.stat().st_size,
                "ext": f.suffix.lower(),
            })
        except Exception:
            continue
    return result


@app.get("/api/projects/{project_name}/file")
async def get_project_file(project_name: str, rel_path: str = Query(..., alias="path")):
    proj_path = _guard_project(project_name)
    fp = _guard_file_path(proj_path, rel_path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    size = fp.stat().st_size
    if size > 1_000_000:
        return {"path": rel_path, "binary": True, "size": size,
                "message": f"File too large ({size:,} bytes)"}
    try:
        content = fp.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return {"path": rel_path, "binary": True, "size": size,
                "message": f"Binary file ({size:,} bytes)"}
    return {"path": rel_path, "content": content, "size": size}


@app.get("/api/projects/{project_name}/download")
async def download_project(project_name: str, include: str = "source"):
    path = _guard_project(project_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(path.rglob("*")):
            if not f.is_file() or ".git" in f.parts:
                continue
            if include == "source" and f.suffix.lower() not in SOURCE_EXTS:
                continue
            try:
                zf.write(f, str(f.relative_to(path.parent)))
            except Exception:
                continue
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_name}.zip"'},
    )


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


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.get("/api/logs")
async def list_logs():
    if not LOGS_DIR.exists():
        return []
    logs = []
    for f in sorted(LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True):
        parts = f.stem.rsplit("_", 2)
        logs.append({
            "filename": f.name,
            "project": parts[0] if len(parts) >= 3 else f.stem,
            "timestamp": parts[2] if len(parts) >= 3 else "",
            "size": f.stat().st_size,
        })
    return logs


@app.get("/api/logs/{filename}")
async def get_log(filename: str):
    if "/" in filename or ".." in filename or not filename.endswith(".log"):
        raise HTTPException(status_code=400, detail="Invalid log filename")
    p = LOGS_DIR / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    return StreamingResponse(open(p, "rb"), media_type="text/plain; charset=utf-8")


# ── Models ────────────────────────────────────────────────────────────────────

class ModelRequest(BaseModel):
    alias: str
    model_id: str


@app.get("/api/models")
async def list_models():
    return _load_models()


@app.post("/api/models")
async def add_model(req: ModelRequest):
    if not req.alias or not req.model_id:
        raise HTTPException(status_code=400, detail="alias and model_id required")
    models = _load_models()
    models[req.alias] = req.model_id
    UI_MODELS_PATH.write_text(json.dumps(models, indent=2))
    return {"ok": True, "models": models}


@app.delete("/api/models/{alias}")
async def remove_model(alias: str):
    if not UI_MODELS_PATH.exists():
        raise HTTPException(status_code=404, detail="Model not found")
    models = json.loads(UI_MODELS_PATH.read_text())
    if alias not in models:
        raise HTTPException(status_code=404, detail="Model not found")
    del models[alias]
    UI_MODELS_PATH.write_text(json.dumps(models, indent=2))
    return {"ok": True, "models": models}


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
  max_token: 8192
  temperature: 0.0

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
    words = re.findall(r'\w+', idea.lower())[:4]
    return "_".join(words)


@app.get("/api/runs")
async def list_active_runs():
    return [
        {"run_id": rid, "project": _run_meta.get(rid, "")}
        for rid, done in _run_done.items()
        if not done
    ]


@app.post("/api/run")
async def start_run(req: RunRequest):
    models = _load_models()
    if req.model not in models:
        raise HTTPException(status_code=400, detail=f"Unknown model alias: {req.model}")

    project = req.project or _derive_project_name(req.idea)
    model_id = models[req.model]
    run_id = str(uuid.uuid4())
    _run_buffers[run_id] = []
    _run_done[run_id] = False
    _run_meta[run_id] = project

    asyncio.create_task(_execute_run(req, project, model_id, run_id))
    return {"run_id": run_id, "project": project}


@app.post("/api/run/{run_id}/cancel")
async def cancel_run(run_id: str):
    if _run_done.get(run_id, True):
        raise HTTPException(status_code=404, detail="Run not found or already finished")
    proc = _run_procs.get(run_id)
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "pkill", "-f", "metagpt"],
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True}


async def _execute_run(req: RunRequest, project: str, model_id: str, run_id: str):
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

    cmd_parts.append(req.idea)
    bash_cmd = " ".join(shlex.quote(p) for p in cmd_parts)

    # Set up log file
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        safe_project = re.sub(r'[^\w-]', '_', project)
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        log_filename = f"{safe_project}_{run_id[:8]}_{timestamp}.log"
        lf = open(LOGS_DIR / log_filename, "w", encoding="utf-8")
    except OSError:
        lf = None

    def push(msg: str):
        for line in (msg.splitlines(keepends=True) or [msg]):
            _run_buffers[run_id].append(line)
        if lf is not None:
            lf.write(msg)
            lf.flush()

    original_config = CONFIG_HOST.read_text()
    try:
        if project_host.exists() and (project_host / ".dependencies.json").exists():
            push("[metagpt-ui] Existing project — incremental mode\n")
        else:
            push("[metagpt-ui] New project — fresh mode\n")

        async with _config_lock:
            _write_config(model_id)
            push(f"[metagpt-ui] Model set to: {req.model} ({model_id})\n")

        # Ensure container running
        info = _container_status()
        if not info["running"]:
            push("[metagpt-ui] Starting container...\n")
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

        push(f"[metagpt-ui] Running: {bash_cmd}\n\n")

        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", CONTAINER_NAME, "bash", "-c", bash_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        _run_procs[run_id] = proc
        assert proc.stdout is not None
        async for line in proc.stdout:
            push(line.decode("utf-8", errors="replace"))

        await proc.wait()
        push(f"\n[metagpt-ui] Process exited with code {proc.returncode}\n")
    except Exception as e:
        push(f"\n[metagpt-ui] ERROR: {e}\n")
    finally:
        CONFIG_HOST.write_text(original_config)
        _run_procs.pop(run_id, None)
        if lf is not None:
            lf.close()
        _run_done[run_id] = True


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.get("/api/stream/{run_id}")
async def stream_run(run_id: str, request: Request):
    if run_id not in _run_buffers:
        raise HTTPException(status_code=404, detail="Run not found")

    last_id = request.headers.get("last-event-id", "")
    try:
        start = int(last_id) + 1 if last_id else 0
    except ValueError:
        start = 0

    async def event_generator() -> AsyncIterator[str]:
        pos = start
        while True:
            buf = _run_buffers[run_id]
            while pos < len(buf):
                line = buf[pos]
                safe = line.replace("\n", " ").replace("\r", "")
                yield f"id: {pos}\ndata: {safe}\n\n"
                pos += 1
            if _run_done.get(run_id, False):
                yield "data: [DONE]\n\n"
                break
            await asyncio.sleep(0.1)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
