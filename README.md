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

~/sw/metagpt-workspace/   # generated projects land here
~/.metagpt/config2.yaml   # LLM + execution config (rewritten per run)

~/bin/metagpt-*           # symlinks into bin/ above
```

## Web UI

```bash
metagpt-ui
```

Opens `http://localhost:8080` automatically. On first run it creates a Python venv and installs dependencies (~30s).

Features:
- Project list with file counts and snippets
- New project and incremental (modify existing) runs
- Model selector, rounds, code review and test toggles
- Live log streaming via SSE
- Container start/stop with live status badge

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

| Alias | Model | Use for |
|-------|-------|---------|
| `heavy` | gemma-4-26B-A4B | Full software projects, complex tasks |
| `fast` | gemma-4-E4B | Quick prototypes, simple tasks |
| `gemma3` | gemma-3-27B-qat | Alternative for comparison |

## Configuration

`~/.metagpt/config2.yaml` is rewritten before each run to set the chosen model. The original content is restored on exit (even on Ctrl-C). A lockfile at `~/.metagpt/.run.lock` prevents concurrent runs from racing on the config.

To permanently change the default model, edit `MODEL_IDS` and the `MODEL_ALIAS` default in `bin/metagpt-run` and the `MODEL_IDS` dict in `app.py`.

## Container

The MetaGPT Docker container (`metagpt/metagpt:latest`) runs persistently with:
- `--net=host` so it can reach LM Studio on `localhost:1234`
- `--privileged` so it can spawn `python:3.11-slim` containers for code execution
- `~/.metagpt/config2.yaml` and `~/sw/metagpt-workspace` bind-mounted

All CLI and UI commands auto-start the container if it isn't running.
