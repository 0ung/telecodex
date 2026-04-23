# Telecodex As A Personal Development Tool

`telecodex` is not a generic chatbot product.
It is a personal development tool that lets me keep a coding session alive from Telegram while Gemini and Codex split the work.

## The problem it solves

Codex is strong at implementation, but real feature work still needs repeated human steering.
When the goal is not fully specified up front, I usually have to keep checking the result, restating the objective, and deciding the next step myself.

I built `telecodex` to reduce that loop:

- I give the first goal
- Gemini keeps the goal, reviews progress, and decides what should happen next
- Codex executes concrete development work
- I only step back in when more information is actually needed

## Core idea

The system treats one chat thread as one development session.

High-level loop:

1. I send a goal from Telegram
2. Gemini turns that goal into a plan and acceptance criteria
3. Codex edits code, runs commands, and reports what changed
4. Gemini reviews the result against the goal
5. The session either continues, asks me for missing input, or finishes

This makes it feel less like "chatting with a code model" and more like running a lightweight personal coding workflow.

## Why the architecture looks like this

The project is split into three main parts:

- `gateway`
  - the conversational entrypoint
  - receives Telegram messages
  - formats status updates back to the user
- `worker`
  - the private orchestration runtime
  - runs the Gemini and Codex loop
  - stores sessions and artifacts
- `shared`
  - config models
  - typed contracts
  - CLI wrappers
  - reusable helpers

This separation keeps messaging concerns away from orchestration concerns.
It also makes it easier to keep Telegram as just one adapter instead of the whole system.

## Why Gemini, Codex, and MCP each exist

- `Gemini`
  - planner and reviewer
  - interprets the goal
  - generates or updates acceptance criteria
  - decides whether to continue, ask the user something, or finish
- `Codex`
  - executor
  - edits files
  - runs commands
  - performs verification and reports what changed
- `MCP-style session bridge`
  - shared state access layer
  - keeps Gemini and Codex looking at the same session state
  - avoids raw free-for-all file editing of shared session data

The point is not to use multiple AI tools for novelty.
It is to give each part a narrower responsibility.

## Why Python instead of Go

The earlier shape of the project leaned more toward service infrastructure.
The new shape is much more about orchestration:

- calling external CLIs
- parsing structured output
- storing session artifacts
- iterating on prompts and control flow quickly

That pushed the center of gravity from "static server implementation" toward "fast AI workflow iteration."
Python fit that better for this version of the tool.

In short:

- Go was fine for a straightforward bot backend
- Python was better for a session-based AI orchestration runtime

## What I actually owned while building it

Even when AI tools helped with implementation, the core engineering decisions were still mine:

- defining the problem and the intended workflow
- choosing the Gemini planner / Codex executor split
- designing the session model and status machine
- deciding to use a shared session document and structured state access
- shaping user-facing Telegram responses
- defining acceptance criteria and completion behavior
- reviewing generated code, fixing bad edges, and reworking broken flows
- testing, debugging, and deciding what counted as "good enough" to deploy

That is the honest framing:

AI accelerated implementation, but I owned the architecture, constraints, verification, and final integration.

## How to show that it is a real tool I actually use

The easiest demo is not a slide.
It is a real session.

Example flow:

1. Send:

```text
/run Make the Telegram status replies shorter and more readable in Korean.
```

2. Let Gemini define the plan and criteria

3. Let Codex make changes and report back

4. Ask for status:

```text
/status
```

5. Provide extra direction if needed:

```text
Keep the summary compact and avoid raw JSON in chat replies.
```

6. Show the updated reply behavior in Telegram

That demonstrates all of the important pieces:

- real session tracking
- Gemini planning and review
- Codex execution
- user follow-up inside the same conversation
- goal-driven completion instead of one-shot prompting

## Why the single-node story is the honest default

This project is a personal tool first.
Because of that, the most honest deployment story is:

- one Linux server
- host-managed `codex` and `gemini`
- `telecodex` installed with `venv + systemd`
- `worker` bound to localhost
- `gateway` polling Telegram locally

That reflects how I would actually run it on a small personal server.
More complex deployment shapes can exist later, but they should stay secondary to the real usage model.

## Honest limits

Current limits are real and worth saying out loud:

- Telegram is the only active chat adapter in v1
- the system depends on external `codex` and `gemini` CLIs already being installed and authenticated
- file-based state is fine for personal use, but not meant for large multi-user production workloads
- very small servers can run it, but heavy builds or tests will still be constrained by local CPU and RAM

## One-line portfolio summary

I built a personal AI development tool that lets me start and continue coding sessions from Telegram, using Gemini to plan and review, Codex to execute, and a shared session state layer to keep one goal moving across multiple turns.
