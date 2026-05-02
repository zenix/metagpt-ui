# MetaGPT UI — Planned Features

## Feature 0: Agent Progress & Artifacts Summary

**Why:** A 20-minute run goes through 4–5 roles with no visibility. After completion, the structured role outputs exist in `docs/` but are invisible in the UI.

### A) Live pipeline strip
Parse the SSE stream for role transitions. MetaGPT emits lines like:
```
│ Alice(Product Manager): to do WritePRD(WritePRD)
│ Bob(Architect): to do WriteDesign(WriteDesign)
│ Eve(Engineer): to do WriteCode(WriteCode)
```
Show a 5-step pipeline above the log: **Product Manager → Architect → Project Manager → Engineer → QA**
Each step lights up when its role name appears in the stream.

```js
const ROLE_PATTERNS = [
  [/Product Manager/i, 'pm'],
  [/Architect/i,       'arch'],
  [/Project Manager/i, 'projmgr'],
  [/Engineer/i,        'eng'],
  [/QA/i,              'qa'],
];
```

### B) Post-run artifacts summary panel
New backend endpoint:
```
GET /api/projects/{project_name}/summary
→ {prd: {...}, system_design: {...}, task: {...}, code_plan: {...}}
```
Reads the newest `.json` from each `docs/` subdirectory. Returns trimmed objects. Frontend shows per-role cards:

| Role | Source | Fields |
|------|--------|--------|
| Product Manager | `docs/prd/*.json` | Project Name, Refined Requirements, User Stories |
| Architect | `docs/system_design/*.json` | Data structures, API spec |
| Project Manager | `docs/task/*.json` | Task list, packages |
| Engineer | `docs/code_plan_and_change/*.json` | Files changed |

"Summary" button appears in sidebar when a project is selected.

**Complexity: Medium**

---

## Feature 1: Kill/Cancel a Running Job

**Why:** A bad prompt currently requires stopping the entire Docker container.

### Backend (`app.py`)
- Add `_run_procs: dict[str, asyncio.subprocess.Process] = {}`
- Store `proc` in `_run_procs[run_id]` after creation; pop in `finally`
- New endpoint: `POST /api/run/{run_id}/cancel`
  - `proc.kill()` + `docker exec metagpt pkill -f metagpt` (best-effort)

### Frontend (`index.html`)
- Hidden "Cancel" button in log header; shown while `running=true`
- `cancelRun()` POSTs to `/api/run/${currentRunId}/cancel`, disables itself immediately

**Complexity: Low**

---

## Feature 2: Project Deletion

**Why:** The sidebar fills with abandoned experiments.

### Backend (`app.py`)
- `DELETE /api/projects/{project_name}` — `shutil.rmtree` after path-traversal guard

### Frontend (`index.html`)
- Small `✕` button on each project row (visible on hover)
- Confirms before deleting, then refreshes list

**Complexity: Low**

---

## Feature 3: Project File Browser

**Why:** The generated code and docs exist only on disk — no way to view them without a terminal.

### Backend (`app.py`)
```
GET /api/projects/{project_name}/files       → [{path, size, ext}, ...]
GET /api/projects/{project_name}/file?path=  → {content, path}
```
- Skip `.git/` entries; reject paths resolving outside workspace; reject files > 1 MB

### Frontend (`index.html`)
- "Browse Files" button in form bar (when project selected)
- Replaces log area with two-pane layout: file tree (220px) | code viewer (flex:1)
- Binary files show `[binary — N bytes]`; `.mmd` files show "Render Diagram" button (Feature 4)

**Complexity: Medium**

---

## Feature 4: Mermaid Diagram Rendering

**Why:** MetaGPT generates `.mmd` diagrams in `resources/seq_flow/`, `resources/data_api_design/`, `resources/competitive_analysis/`.

### Backend
None — served by Feature 3's file endpoint.

### Frontend (`index.html`)
- Load from CDN: `https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js`
- `mermaid.initialize({ startOnLoad: false, theme: 'dark' })`
- `.mmd` files: Raw/Rendered toggle using `mermaid.run({ nodes: [...] })`
- `.md` files: scan for fenced ` ```mermaid ` blocks and render inline

**Complexity: Low** (depends on Feature 3)

---

## Feature 5: Persistent Run Log History

**Why:** Long runs (15–30 min) are unrecoverable if the browser tab is closed.

### Backend (`app.py`)
- `LOGS_DIR = WORKSPACE_HOST / ".logs"` — created on first run
- Filename: `{project}_{run_id[:8]}_{YYYYmmddTHHMMSS}.log`
- Write each streamed line to both queue and log file (with `flush()`)
- `GET /api/logs` → `[{filename, project, timestamp, size}, ...]`
- `GET /api/logs/{filename}` → raw log as `text/plain`

### Frontend (`index.html`)
- "Logs" toggle in sidebar header switches project list ↔ log list
- Each entry: project name, date, size, "View" button that loads into log area

**Complexity: Medium**

---

## Feature 6: Project Export / Download

**Why:** The file browser lets you read files; downloading lets you actually use the output.

### Backend (`app.py`)
```
GET /api/projects/{project_name}/download?include=source|all
```
- In-memory ZIP (`zipfile.ZipFile` + `io.BytesIO`)
- `source` filters to: `.py .js .ts .html .css .json .md .txt .mmd .yaml .toml`
- `StreamingResponse` with `Content-Disposition: attachment`

### Frontend (`index.html`)
- "Download ZIP" button in form bar and file browser header
- `window.location = /api/projects/${name}/download?include=source`

**Complexity: Low** (depends on Feature 3 guard helper)

---

## Feature 7: Custom Model Management

**Why:** The model list is hardcoded in `app.py`. Adding a new LM Studio model requires editing Python source.

### Backend (`app.py`)
- Persist to `~/.metagpt/ui_models.json`; fall back to current `MODEL_IDS` if missing
- `GET /api/models` → `{alias: model_id, ...}`
- `POST /api/models` → `{alias, model_id}`
- `DELETE /api/models/{alias}`
- `start_run` reads models dynamically via `_load_models()`

### Frontend (`index.html`)
- "Manage" link next to model `<select>` opens a modal
- Modal: table of alias/model_id rows + delete buttons + "Add" form
- On close: refresh `<select>` from `GET /api/models`

**Complexity: Medium**

---

## Cross-Cutting: Path Traversal Guard

Add once to `app.py`; reused by Features 2, 3, 6:
```python
def _guard_project(name: str) -> Path:
    if not name or "/" in name or ".." in name:
        raise HTTPException(400, "Invalid project name")
    path = (WORKSPACE_HOST / name).resolve()
    workspace = WORKSPACE_HOST.resolve()
    if workspace not in path.parents and path != workspace:
        raise HTTPException(400, "Path traversal")
    if not path.exists():
        raise HTTPException(404, "Project not found")
    return path
```

---

## Implementation Order

| # | Feature | Complexity |
|---|---------|-----------|
| 0 | Agent progress + artifacts summary | Medium |
| 1 | Cancel running job | Low |
| 2 | Project deletion | Low |
| 3 | File browser | Medium |
| 4 | Mermaid rendering | Low |
| 6 | Download ZIP | Low |
| 5 | Run log history | Medium |
| 7 | Custom models | Medium |

## Files to Modify

- `app.py` — all backend endpoint changes
- `index.html` — all frontend changes
