# MetaGPT Local Setup

Local MetaGPT environment backed by [LM Studio](https://lmstudio.ai). Includes a CLI for terminal use and a web dashboard for managing projects, triggering runs, and monitoring live output.

## Requirements

- Docker
- LM Studio running on `localhost:1234`
- Python 3.x (for the web UI venv)
- `~/bin` on your `$PATH`

## Directory structure

```
~/sw/metagpt-ui/
├── bin/
│   ├── metagpt-run       # CLI runner
│   ├── metagpt-ps        # status viewer
│   ├── metagpt-models    # model list
│   └── metagpt-ui        # web UI launcher
├── app.py                # FastAPI backend
├── index.html            # web frontend
└── requirements.txt

~/sw/metagpt-workspace/        # generated projects land here
~/sw/metagpt-workspace/.logs/  # run log history (one file per run)
~/.metagpt/config2.yaml        # LLM + execution config (rewritten per run)
~/.metagpt/ui_models.json      # custom model aliases (managed via web UI)

~/bin/metagpt-*           # symlinks into bin/ above
```

## Web UI

```bash
metagpt-ui
```

Opens `http://localhost:8080` automatically. On first run it creates a Python venv and installs dependencies (~30s).

### Features

**Running projects**
- New project and incremental (modify existing) runs
- Model selector, rounds, code review and test toggles
- Live log streaming via SSE
- Cancel a running job without stopping the container

**Pipeline visibility**
- Live pipeline strip: **PM → Architect → ProjMgr → Engineer → QA** — each step lights up as the role becomes active, turns green when done
- Multi-round support: if `--n-round` is set above 5, additional round columns are appended dynamically as the run cycles

**Project management**
- Project list with file counts and snippets
- Delete a project (hover the project row to reveal the `✕` button)
- Artifacts summary: per-role cards showing PRD, system design, task list, and code plan pulled from `docs/`
- File browser: two-pane view (file tree + code viewer) with syntax display
- Mermaid diagram rendering for `.mmd` files and fenced ` ```mermaid ` blocks in `.md` files
- Download project as ZIP (source files only, or all files)

**Run history**
- "Logs" tab in the sidebar lists all past runs
- Click "View" to replay any historical log in the output pane

**Model management**
- "Manage" link next to the model selector opens a modal for adding/removing model aliases
- Aliases are persisted to `~/.metagpt/ui_models.json` and survive restarts

**Container controls**
- Start / Restart / Stop / Remove buttons with live status badge

## CLI

### Run a new project

```bash
metagpt-run "Write a CLI snake game with color support"
```

Project name is auto-derived from the first four words of the prompt.

### Run with options

```bash
metagpt-run -m fast -p snake_game -r 3 "Write a CLI snake game"
```

| Flag | Description |
|------|-------------|
| `-p, --project NAME` | Project subdirectory name |
| `-m, --model ALIAS` | `heavy` (default), `fast`, `gemma3` |
| `-r, --rounds N` | Agent rounds (default: 5) |
| `--no-review` | Skip code review pass |
| `--with-tests` | Enable QA test generation |
| `--stop-after` | Stop container after run |
| `--dry-run` | Print command without executing |
| `-l, --list` | List existing projects |

### Incremental (modify existing project)

If the project directory already contains `.dependencies.json`, the run automatically uses incremental mode — MetaGPT updates the existing project instead of creating a new one.

```bash
metagpt-run -p snake_game "Add a high score leaderboard"
```

### Other commands

```bash
metagpt-ps        # container status + recent projects
metagpt-models    # models currently loaded in LM Studio
```

## Model aliases

Default aliases (fallback if `~/.metagpt/ui_models.json` does not exist):

| Alias | Model | Use for |
|-------|-------|---------|
| `qwen3-14b` | qwen/qwen3-14b | Fast, capable default |
| `heavy` | gemma-4-26B-A4B | Full software projects, complex tasks |
| `fast` | gemma-4-E4B | Quick prototypes, simple tasks |
| `gemma3` | gemma-3-27B-qat | Alternative for comparison |

Custom aliases can be added via the web UI (Model → Manage) or by editing `~/.metagpt/ui_models.json` directly.

## Configuration

`~/.metagpt/config2.yaml` is rewritten before each run to set the chosen model. The original content is restored on exit (even on Ctrl-C). A lockfile at `~/.metagpt/.run.lock` prevents concurrent runs from racing on the config.

### Required config files

Both files must exist in `~/.metagpt/` before starting the container:

**`~/.metagpt/config2.yaml`** — LLM and execution settings:
```yaml
llm:
  api_type: "openai"
  base_url: "http://localhost:1234/v1"
  api_key: "lm-studio"
  model: "openai/qwen/qwen3-14b"
  use_system_prompt: true

code_execution:
  backend: "docker"
  image: "python:3.11-slim"
  timeout: 600

mermaid:
  engine: "nodejs"
  path: "mmdc"
  puppeteer_config: "/root/.metagpt/puppeteer-config.json"
```

**`~/.metagpt/puppeteer-config.json`** — required by the Mermaid diagram renderer (nodejs/mmdc) running inside the container. Without this file MetaGPT logs a warning for every diagram it tries to generate:
```json
{
    "args": ["--no-sandbox", "--disable-setuid-sandbox"]
}
```

The `--no-sandbox` flags are necessary because mmdc runs inside Docker where the default sandbox is unavailable.

The entire `~/.metagpt/` directory is bind-mounted into the container as `/root/.metagpt/`, so both files are visible to MetaGPT automatically.

## Container

The MetaGPT Docker container (`metagpt/metagpt:latest`) runs persistently with:
- `--net=host` so it can reach LM Studio on `localhost:1234`
- `--privileged` so it can spawn `python:3.11-slim` containers for code execution
- `~/.metagpt/` bind-mounted as `/root/.metagpt/` (config + puppeteer config)
- `~/sw/metagpt-workspace/` bind-mounted for project output

All CLI and UI commands auto-start the container if it isn't running.

## Run logs

Every run is saved to `~/sw/metagpt-workspace/.logs/` as a plain-text file named:

```
{project}_{run_id[:8]}_{YYYYmmddTHHMMSS}.log
```

Logs are accessible from the "Logs" tab in the web UI sidebar or by reading the files directly.
