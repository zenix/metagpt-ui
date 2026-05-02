# MetaGPT Agents

MetaGPT simulates a software company by chaining specialized LLM agents, each with a distinct role. Understanding what each agent does helps you write better prompts and diagnose where a run goes wrong.

## Agent pipeline

```
Prompt → ProductManager → Architect → ProjectManager → Engineer(s) → QAEngineer
```

Each agent reads the previous agent's output and produces a structured document before passing control to the next.

The web UI pipeline strip tracks this in real time. It advances when MetaGPT logs a role action line (`RoleName: to do ActionName`) or, for Engineer and QA who skip that log line, when their first action (`write_code:run`, `write_test:run`) appears.

### ProductManager

Writes the **Product Requirements Document (PRD)**.

- Extracts goals, user stories, and constraints from your prompt
- Defines the scope: what is and isn't in the product
- Output: `docs/prd/*.json` and `resources/prd/*.md`
- Log signals: `Alice(Product Manager): to do WritePRD`

Tips:
- Be specific about who the user is and what pain they have
- Mention platform (CLI, web, desktop), language preference, and key features
- Constraints ("no external dependencies", "must run offline") go here

### Architect

Writes the **System Design**.

- Chooses tech stack, file structure, module boundaries
- Produces sequence diagrams (Mermaid) and data/API design
- Output: `docs/system_design/*.json`, `resources/system_design/*.md`, `resources/seq_flow/*.mmd`
- Log signals: `Bob(Architect): to do WriteDesign`

Tips:
- If you have a strong opinion on stack (Python, Node, Rust…), state it in the prompt
- Mention integration points ("uses SQLite", "outputs JSON to stdout")

### ProjectManager

Breaks the design into **tasks** and assigns them to engineers.

- Produces a task list with file-level assignments and package requirements
- Output: `docs/task/*.json`, `requirements.txt`
- Log signals: `Eve(Project Manager): to do WriteTasks`

### Engineer

Implements the code. This is usually the longest phase.

- Writes each source file according to the task list
- Immediately reviews and rewrites each file (code review pass) before moving to the next
- May run code in a sandboxed `python:3.11-slim` container to verify output
- Output: actual source files in the project directory, `docs/code_plan_and_change/*.json`
- Log signals: `actions.write_code:run — Writing <file>`, `actions.write_code_review:run — Code review and rewrite <file>: N/2`

Note: Engineer does **not** emit a role announcement line before starting. The pipeline strip advances when the first `write_code:run` line appears.

Tips:
- More rounds (`-r`) give the engineer more attempts to fix errors across the full file set
- `--code-review` (default on) adds a self-review pass before finalizing each file — this roughly doubles the time per file but produces significantly better output

### QAEngineer (optional)

Writes and runs tests. Enabled with `--with-tests` / `run tests` checkbox in the web UI.

- Generates unit tests for the implemented code
- Runs them in the sandbox and reports results
- Log signals: `actions.write_test:run`, `actions.run_code:run`

## What "rounds" means

`--n-round` controls how many agent turns MetaGPT takes in total across the whole team. With the default of 5, a typical run is:

| Round | Agent | Action |
|-------|-------|--------|
| 1 | ProductManager | PrepareDocuments + WritePRD |
| 2 | Architect | WriteDesign |
| 3 | ProjectManager | WriteTasks |
| 4–5 | Engineer | WriteCode + CodeReview per file |

Setting `--n-round 10` gives Engineer more turns to write and review additional files or fix issues across multiple passes. The pipeline strip in the web UI extends dynamically when a new round starts.

## Incremental runs

When you pass a prompt to an existing project (the project directory contains `.dependencies.json`), MetaGPT enters **incremental mode**:

1. ProductManager re-reads the existing PRD and merges in the new requirement
2. Only affected modules are redesigned and re-implemented
3. Previously generated files are preserved unless explicitly changed

This is the right mode for: adding features, fixing bugs, refactoring, or extending an existing project.

## Writing effective prompts

**Be specific about outputs:**
> "Write a CLI snake game in Python. It must run in the terminal using curses, support WASD and arrow key controls, display a score, and end with a game-over screen."

**State constraints upfront:**
> "No external libraries except the Python standard library. Must work on Linux."

**For incremental runs, describe the delta:**
> "Add a high score leaderboard that persists to ~/.snake_scores.json and displays the top 5 on the game-over screen."

**Avoid vague requests** like "make it better" — the ProductManager will interpret this broadly and may restructure the whole project.

## Diagnosing failures

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Run hangs after "Architect" | LM Studio context window too small for the system design | Use `--n-round 3` or a simpler prompt to reduce output size |
| Engineer writes incomplete files | Model ran out of context mid-file | Try `heavy` model; reduce rounds |
| Code execution fails silently | Docker-in-Docker not working | Check container has `--privileged`; verify `python:3.11-slim` image is pullable |
| Incremental run rewrites everything | `.dependencies.json` missing or corrupted | Delete the project dir and start fresh |
| Mermaid diagrams missing | Puppeteer/Chromium issue in container | Diagrams are optional; source code is unaffected |
| Pipeline strip stuck on ProjMgr | Engineer skips `_act` log line | Strip advances on `write_code:run` instead — wait for the first file to start |

## Useful MetaGPT CLI flags

These are passed through by `metagpt-run` or can be used directly inside the container:

```
--n-round N          Max agent rounds (default 5)
--code-review        Enable code review pass (default on)
--no-code-review     Disable code review
--run-tests          Enable QA test generation
--project-name NAME  Set project directory name
--project-path PATH  Target existing project (enables incremental mode)
--recover-path PATH  Resume an interrupted run from a checkpoint
```
