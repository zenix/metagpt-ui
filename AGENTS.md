# MetaGPT Agents

MetaGPT simulates a software company by chaining specialized LLM agents, each with a distinct role. Understanding what each agent does helps you write better prompts and diagnose where a run goes wrong.

## Agent pipeline

```
Prompt → ProductManager → Architect → ProjectManager → Engineer(s) → QAEngineer
```

Each agent reads the previous agent's output and produces a structured document before passing control to the next.

### ProductManager

Writes the **Product Requirements Document (PRD)**.

- Extracts goals, user stories, and constraints from your prompt
- Defines the scope: what is and isn't in the product
- Output: `docs/prd/*.json` and `resources/prd/*.md`

Tips:
- Be specific about who the user is and what pain they have
- Mention platform (CLI, web, desktop), language preference, and key features
- Constraints ("no external dependencies", "must run offline") go here

### Architect

Writes the **System Design**.

- Chooses tech stack, file structure, module boundaries
- Produces sequence diagrams (Mermaid) and data/API design
- Output: `docs/system_design/*.json`, `resources/system_design/*.md`, `resources/seq_flow/*.mmd`

Tips:
- If you have a strong opinion on stack (Python, Node, Rust…), state it in the prompt
- Mention integration points ("uses SQLite", "outputs JSON to stdout")

### ProjectManager

Breaks the design into **tasks** and assigns them to engineers.

- Produces a task list with file-level assignments
- Output: `docs/task/*.json`

### Engineer

Implements the code.

- Writes source files according to the task list
- May run code in a sandboxed `python:3.11-slim` container (when `code_execution.backend: docker` is set) to verify output
- Output: actual source files in the project directory

Tips:
- More rounds (`-r`) give the engineer more attempts to fix errors
- `--code-review` (default on) adds a self-review pass before finalizing each file

### QAEngineer (optional)

Writes and runs tests. Enabled with `--with-tests`.

- Generates unit tests for the implemented code
- Runs them in the sandbox and reports results

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
