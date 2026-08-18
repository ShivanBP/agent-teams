"""Subprocess bridge to the Claude, Codex and Agy CLIs; the zulip import stays out."""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import constants
import prompts
import store

REPO_DIR = Path(__file__).resolve().parent.parent
PERSONA_DIR = REPO_DIR / "agents"
log = logging.getLogger("agent-team.runner")

@dataclass
class Result:
    reply: str
    session_id: str
    cost_usd: float
    turns: int
    usage: dict = field(default_factory=dict)
    provider: str = "claude"


def _failure_output(stderr, stdout):
    return "\n".join(part.strip() for part in (stderr, stdout) if part.strip())[-2000:]


def _wake_log_path(lane):
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(lane)).strip("-") or "wake"
    return constants.LOGS_DIR / "wakes" / (slug + ".jsonl")


def _action_key(event):
    event_type = event.get("type")
    if event_type == "assistant":
        content = (event.get("message") or {}).get("content") or []
        for part in reversed(content):
            if part.get("type") in ("text", "tool_use"):
                return "claude:%s" % part["type"]
    if event_type == "item.completed":
        return "codex:%s" % (event.get("item") or {}).get("type")
    if event.get("event") == "step_update":
        return "agy:step_update"
    if event_type == "text":
        return "opencode:text"
    if event_type in ("tool", "tool_use"):
        return "opencode:tool"
    return None


def last_action(lane):
    path = _wake_log_path(lane)
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            key = _action_key(json.loads(line))
        except ValueError:
            continue
        if key in constants.LAST_ACTION_LABELS:
            return constants.LAST_ACTION_LABELS[key]
    return None


def _run_jsonl(cmd, *, cwd, env, timeout, wake_log, stdin=None):
    kwargs = {
        "cwd": str(cwd), "env": env, "stderr": subprocess.PIPE,
        "text": True, "timeout": timeout,
    }
    if stdin is not None:
        kwargs["stdin"] = stdin
    if wake_log is None:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, **kwargs)
        return proc, proc.stdout
    wake_log.parent.mkdir(parents=True, exist_ok=True)
    with wake_log.open("w") as output:
        proc = subprocess.run(cmd, stdout=output, **kwargs)
    return proc, wake_log.read_text()


def _build_cmd(persona, model, session, effort, prompt="hi"):
    """claude -p --agent <persona> with realtime JSON events and one terminal result."""
    # headless has nobody to approve, and an untrusted cwd (a build worktree) fences Bash off
    cmd = ["claude", "-p", "--dangerously-skip-permissions",
           "--output-format", "stream-json", "--verbose", "--agent", persona]
    if model:
        cmd += ["--model", model]
    if session:
        cmd += ["--resume", session]
    if effort:
        cmd += ["--effort", effort]
    cmd.append(prompt)
    return cmd


def _parse(payload):
    usage = payload.get("usage") or {}
    reply = payload.get("result", "")
    session_id = payload.get("session_id", "")
    cost_usd = float(payload.get("total_cost_usd") or 0.0)
    turns = int(payload.get("num_turns") or 0)
    usage_out = {
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "input_tokens": usage.get("input_tokens", 0),
    }
    return reply, session_id, cost_usd, turns, usage_out


def _parse_claude_stream(output):
    terminal = None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "result":
            terminal = event
    if terminal is None:
        raise RuntimeError("claude stream returned no terminal result event")
    if terminal.get("is_error") or terminal.get("subtype") != "success":
        raise RuntimeError("claude stream failed: %s" % terminal.get("result", terminal))
    return _parse(terminal)


def _build_cmd_codex(model, session, effort, output_path, prompt="hi"):
    cmd = [constants.CODEX_BIN, "exec"]
    if session:
        cmd.append("resume")
    cmd += [
        "--json", "--strict-config",
        "-c", 'approval_policy="never"',
        "-c", "features.memories=false",
        # full access, matching Claude Bob's reach; workspace-write hard-denies .git (Mate, 2026-08-13)
        "-c", 'sandbox_mode="danger-full-access"',
        "-c", 'model_reasoning_effort="%s"' % effort,
        "-m", model,
        "-o", output_path,
    ]
    if session:
        cmd.append(session)
    cmd.append(prompt)
    return cmd


def _build_cmd_agy(model, session, effort, cwd, timeout, prompt="hi"):
    cmd = [
        constants.AGY_BIN,
        "--dangerously-skip-permissions",
        "--disable-slash-commands",
        "--add-dir", str(cwd),
        "--model", model,
        "--effort", effort,
        "--output-format", "stream-json",
        "--print-timeout", "%ds" % timeout,
    ]
    if session:
        cmd += ["--conversation", session]
    cmd += ["-p", prompt]
    return cmd


def _parse_codex(text):
    session_id = ""
    usage = {}
    completed = False
    errors = []
    turns = 0
    for line in (text or "").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        event_type = event.get("type", "")
        if event_type == "thread.started":
            session_id = event.get("thread_id", "")
        elif event_type == "turn.completed":
            completed = True
            turns += 1
            usage = event.get("usage") or {}
        elif event_type.endswith(".failed") or event_type == "error":
            errors.append(event)
    if errors:
        raise RuntimeError("codex reported an error event: %s" % errors[-1])
    if not completed:
        raise RuntimeError("codex JSONL has no turn.completed event")
    if not session_id:
        raise RuntimeError("codex JSONL has no thread id")
    usage_out = {
        "cache_read_input_tokens": usage.get("cached_input_tokens", 0),
        "cache_creation_input_tokens": 0,
        "input_tokens": usage.get("input_tokens", 0),
    }
    return session_id, turns, usage_out


def _parse_agy(text, session=None):
    payload = None
    for line in reversed((text or "").splitlines()):
        if not line.strip():
            continue
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if not isinstance(candidate, dict):
            continue
        if candidate.get("event") == "result" and isinstance(candidate.get("result"), dict):
            payload = candidate["result"]
            break
        if "event" not in candidate and "status" in candidate:
            payload = candidate
            break
    if payload is None:
        raise RuntimeError("agy stdout has no JSON object")
    if payload.get("status") != "SUCCESS":
        raise RuntimeError("agy status is not SUCCESS: %r" % payload.get("status"))
    session_id = payload.get("conversation_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise RuntimeError("agy JSON has no conversation id")
    if session and session_id != session:
        raise RuntimeError("agy resumed conversation %s returned id %s" % (session, session_id))
    reply = payload.get("response")
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError("agy returned an empty response")
    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        raw_usage = payload
    usage = {
        "cache_read_input_tokens": raw_usage.get("cache_read_tokens", 0),
        "cache_creation_input_tokens": 0,
        "input_tokens": raw_usage.get("input_tokens", 0),
    }
    for key in ("output_tokens", "thinking_tokens", "total_tokens"):
        if key in raw_usage:
            usage[key] = raw_usage[key]
    return reply, session_id, 1, usage


def _without_frontmatter(text):
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S).strip()


def _persona_file(persona):
    """The agent file on disk is the roster here: the operator seats have one and are not in
    personas.PERSONAS, which stays free of them for listener.py's wake and mention rules."""
    path = PERSONA_DIR / ("%s.md" % persona)
    if not path.is_file():
        raise RuntimeError("no agent file for persona %r at %s" % (persona, path))
    return path


def _memory_file(identity):
    """Keyed on the wake identity, not the agent file name: a seat woken as bridge reads
    memory/bridge instead of growing a memory tree per agent file."""
    return constants.MEMORY_DIR / identity / "MEMORY.md"


def _clip_memory(raw, max_lines=constants.MEMORY_MAX_LINES,
                 max_bytes=constants.MEMORY_MAX_BYTES):
    lines = raw.splitlines(keepends=True)
    line_limited = b"".join(lines[:max_lines])
    clipped = line_limited[:max_bytes]
    content = clipped.decode("utf-8", errors="ignore")
    return content, len(lines) > max_lines or len(line_limited) > max_bytes


def _memory_frame(identity):
    path = _memory_file(identity)
    raw = path.read_bytes() if path.is_file() else b""
    content, truncated = _clip_memory(raw)
    if truncated:
        log.info("memory snapshot truncated for %s at %s", identity, path)
    return prompts.memory_frame(
        constants.MEMORY_DIR, path, content, truncated,
        constants.MEMORY_MAX_LINES, constants.MEMORY_MAX_BYTES)


def _first_prompt(provider, persona, prompt, identity):
    memory = _memory_frame(_wake_identity(persona, identity))
    if provider == "claude":
        return prompts.with_memory_frame(memory, prompt)
    return prompts.provider_prompt(
        provider, persona, prompt,
        _without_frontmatter(_persona_file(persona).read_text()),
        memory,
    )


def _run_prompt(provider, persona, prompt, session, identity):
    return prompt if session else _first_prompt(provider, persona, prompt, identity)


def _wake_identity(persona, identity):
    """AGENT_TEAM_IDENTITY for the subprocess: identity is send.py's --as match, not always the
    --agent name (the operator-reply seat runs as the operator-reply agent but posts as bridge,
    its seat's whole send.py surface). Falls back to persona when no identity is given."""
    return identity or persona


def _run_claude(persona, prompt, *, model, effort, session, cwd, timeout, identity,
                wake_log=None):
    run_prompt = _run_prompt("claude", persona, prompt, session, identity)
    cmd = _build_cmd(persona, model, session, effort, run_prompt)
    env = dict(os.environ)
    env["AGENT_TEAM_IDENTITY"] = _wake_identity(persona, identity)
    env["CLAUDE_CODE_DISABLE_TERMINAL_TITLE"] = "1"
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    proc, stdout = _run_jsonl(
        cmd, cwd=Path(cwd or REPO_DIR).resolve(), env=env, timeout=timeout,
        wake_log=wake_log)
    if proc.returncode != 0:
        raise RuntimeError(
            "claude -p failed (exit %d) for persona %s: %s" %
            (proc.returncode, persona, _failure_output(proc.stderr, stdout))
        )
    try:
        reply, session_id, cost_usd, turns, usage = _parse_claude_stream(stdout)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError("claude -p returned a bad stream for persona %s: %s" % (persona, exc))
    return Result(reply=reply, session_id=session_id, cost_usd=cost_usd, turns=turns,
                  usage=usage, provider="claude")


def _run_codex(persona, prompt, *, model, effort, session, cwd, timeout, identity,
               wake_log=None):
    run_cwd = Path(cwd or REPO_DIR).resolve()
    final = tempfile.NamedTemporaryFile(prefix="agent-team-codex-", suffix=".txt", delete=False)
    final_path = final.name
    final.close()
    try:
        run_prompt = _run_prompt("codex", persona, prompt, session, identity)
        cmd = _build_cmd_codex(model, session, effort, final_path, run_prompt)
        env = dict(os.environ)
        env["AGENT_TEAM_IDENTITY"] = _wake_identity(persona, identity)
        # DEVNULL or codex exec reads its extra input from an open stdin and blocks (Peter, 2026-08-16).
        proc, stdout = _run_jsonl(
            cmd, cwd=run_cwd, env=env, stdin=subprocess.DEVNULL,
            timeout=timeout, wake_log=wake_log)
        if proc.returncode != 0:
            raise RuntimeError(
                "codex exec failed (exit %d) for persona %s: %s" %
                (proc.returncode, persona, _failure_output(proc.stderr, stdout)))
        session_id, turns, usage = _parse_codex(stdout)
        reply = Path(final_path).read_text().strip()
        if not reply:
            raise RuntimeError("codex exec returned an empty final message for persona %s" % persona)
        return Result(reply=reply, session_id=session_id, cost_usd=0.0, turns=turns,
                      usage=usage, provider="codex")
    finally:
        try:
            Path(final_path).unlink()
        except FileNotFoundError:
            pass


def _build_cmd_opencode(model, session, effort, cwd, prompt="hi"):
    cmd = [constants.OPENCODE_BIN, "run", "--format", "json", "--auto"]
    if model:
        cmd += ["--model", model]
    if session:
        cmd += ["--session", session]
    if effort:
        cmd += ["--variant", effort]
    if cwd:
        cmd += ["--dir", str(cwd)]
    cmd.append(prompt)
    return cmd


def _parse_opencode(text, session=None):
    session_id = ""
    reply_parts = []
    usage = {}
    cost_usd = 0.0
    turns = 0
    errors = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        event_type = event.get("type", "")
        if event_type == "step_start":
            session_id = event.get("sessionID", session_id)
        elif event_type == "text":
            part = event.get("part") or {}
            reply_parts.append(part.get("text", ""))
        elif event_type == "step_finish":
            turns += 1
            part = event.get("part") or {}
            tokens = part.get("tokens") or {}
            cache = tokens.get("cache") or {}
            usage = {
                "input_tokens": tokens.get("input", 0),
                "output_tokens": tokens.get("output", 0),
                "total_tokens": tokens.get("total", 0),
                "thinking_tokens": tokens.get("reasoning", 0),
                "cache_read_input_tokens": cache.get("read", 0),
                "cache_creation_input_tokens": cache.get("write", 0),
            }
            cost_usd = float(part.get("cost", 0) or 0)
        elif event_type == "error":
            errors.append(event)
    if errors:
        raise RuntimeError("opencode reported an error: %s" % errors[-1])
    if not session_id:
        raise RuntimeError("opencode output has no session id")
    reply = "".join(reply_parts).strip()
    if not reply:
        raise RuntimeError("opencode returned an empty reply")
    return reply, session_id, cost_usd, turns, usage


def _run_opencode(persona, prompt, *, model, effort, session, cwd, timeout, identity,
                  wake_log=None):
    run_cwd = Path(cwd or REPO_DIR).resolve()
    run_prompt = _run_prompt("opencode", persona, prompt, session, identity)
    cmd = _build_cmd_opencode(model, session, effort, run_cwd, run_prompt)
    env = dict(os.environ)
    env["AGENT_TEAM_IDENTITY"] = _wake_identity(persona, identity)
    env["OPENCODE_DISABLE_CLAUDE_CODE"] = "true"
    proc, stdout = _run_jsonl(
        cmd, cwd=run_cwd, env=env, timeout=timeout, wake_log=wake_log)
    if proc.returncode != 0:
        raise RuntimeError(
            "opencode run failed (exit %d) for persona %s: %s" %
            (proc.returncode, persona, _failure_output(proc.stderr, stdout)))
    reply, session_id, cost_usd, turns, usage = _parse_opencode(stdout, session)
    return Result(reply=reply, session_id=session_id, cost_usd=cost_usd, turns=turns,
                  usage=usage, provider="opencode")


def _run_agy(persona, prompt, *, model, effort, session, cwd, timeout, identity,
             wake_log=None):
    run_cwd = Path(cwd or REPO_DIR).resolve()
    run_prompt = _run_prompt("agy", persona, prompt, session, identity)
    cmd = _build_cmd_agy(model, session, effort, run_cwd, timeout, run_prompt)
    env = dict(os.environ)
    env["AGENT_TEAM_IDENTITY"] = _wake_identity(persona, identity)
    proc, stdout = _run_jsonl(
        cmd, cwd=run_cwd, env=env, timeout=timeout, wake_log=wake_log)
    if proc.returncode != 0:
        raise RuntimeError(
            "agy failed (exit %d) for persona %s: %s" %
            (proc.returncode, persona, _failure_output(proc.stderr, stdout)))
    reply, session_id, turns, usage = _parse_agy(stdout, session)
    return Result(reply=reply.strip(), session_id=session_id, cost_usd=0.0, turns=turns,
                  usage=usage, provider="agy")


def wake_slug(stream_id, topic):
    """Directory and branch name for a topic's build worktree; normalize_topic only strips the
    resolve prefix, so the rest of the filesystem-unsafe characters go here."""
    text = store.normalize_topic(topic).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    return "%s-%s" % (stream_id, slug) if slug else str(stream_id)


def wants_worktree(identity, exists):
    """Builders always get one; verifiers join the topic's worktree only when it is already there."""
    if identity in constants.WORKTREE_PERSONAS:
        return True
    return bool(exists) and identity in constants.WORKTREE_JOIN


_WORKTREE_LINKS = (".venv", "config/persona-matrix.json", "config/harness-defaults.json",
                   "config/model-effort-defaults.json")


def _worktree_add(path, branch):
    """git worktree add, reusing the branch if it exists."""
    constants.WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    has_branch = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True, text=True, timeout=60).returncode == 0
    cmd = ["git", "-C", str(REPO_DIR), "worktree", "add"]
    cmd += [str(path), branch] if has_branch else ["-b", branch, str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError("git worktree add failed (exit %d): %s" %
                           (proc.returncode, _failure_output(proc.stderr, proc.stdout)))


def _ensure_links(path):
    """The four gitignored paths symlinked in from the main checkout. Runs on every wake, not
    only at creation, so a link that failed once is repaired instead of carried forever."""
    for name in _WORKTREE_LINKS:
        source, target = REPO_DIR / name, path / name
        if not source.exists():
            continue
        if target.is_symlink():
            if target.readlink() == source:
                continue
            target.unlink()  # points elsewhere, or dangles: exists() follows the link and lies
        elif target.exists():
            log.warning("worktree %s has a real %s, not linking", path, name)
            continue
        target.symlink_to(source)


def _refresh_worktree(path):
    """Fetch and rebase before handoff. A failed refresh keeps the isolated tree and warns."""
    try:
        for cwd, args in ((REPO_DIR, ("fetch", "origin")),
                          (path, ("rebase", "origin/main"))):
            proc = subprocess.run(
                ["git", "-C", str(cwd)] + list(args),
                capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                raise RuntimeError("git %s failed (exit %d): %s" %
                                   (" ".join(args), proc.returncode,
                                    _failure_output(proc.stderr, proc.stdout)))
        return ""
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        behind = "unknown"
        try:
            subprocess.run(
                ["git", "-C", str(path), "rebase", "--abort"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            count = subprocess.run(
                ["git", "-C", str(path), "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=60)
            if count.returncode == 0 and count.stdout.strip():
                behind = count.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
        notice = prompts.WORKTREE_STALE_WARNING.format(behind=behind)
        log.warning("%s Refresh failure: %s", notice, exc)
        return notice


def wake_cwd(identity, stream_id, topic):
    """Return (path, notice) for the shared checkout or the topic's revalidated build worktree.

    Creation failure falls back to REPO_DIR. Refresh failure aborts the rebase and keeps the stale
    worktree, because an isolated stale tree is safer than silently entering the shared checkout.
    """
    slug = wake_slug(stream_id, topic)
    path = constants.WORKTREE_ROOT / slug
    exists = (path / ".git").exists()  # the pointer file, not the directory: a plain dir is not one
    if not wants_worktree(identity, exists):
        return REPO_DIR, ""
    if not exists:
        try:
            _worktree_add(path, "build/%s" % slug)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            log.warning("worktree %s unavailable, running in %s: %s", path, REPO_DIR, exc)
            return REPO_DIR, ""
    try:
        _ensure_links(path)
    except OSError as exc:
        log.warning("worktree %s is missing a link: %s", path, exc)
    return path, _refresh_worktree(path)


def run(persona, prompt, *, provider, model=None, effort=None, session=None, cwd=None,
        timeout=constants.RUN_TIMEOUT, identity=None, lane=None):
    if provider == "claude":
        return _run_claude(persona, prompt, model=model, effort=effort, session=session,
                           cwd=cwd, timeout=timeout, identity=identity,
                           wake_log=_wake_log_path(lane) if lane is not None else None)
    # codex and agy spell both flags unconditionally, so a hand-driven run that omits either one
    # hands subprocess a None. The harness defaults stand in; their effort is a fleet word
    # (low/mid/high/xtra) and reaches the CLI translated, as a listener wake's already does.
    if provider == "codex":
        return _run_codex(
            persona, prompt, model=model or constants.CODEX_MODEL,
            effort=effort or constants.translate_effort("codex", constants.CODEX_EFFORT),
            session=session, cwd=cwd, timeout=timeout, identity=identity,
            wake_log=_wake_log_path(lane) if lane is not None else None)
    if provider == "agy":
        return _run_agy(
            persona, prompt, model=model or constants.AGY_MODEL,
            effort=effort or constants.translate_effort("agy", constants.AGY_EFFORT),
            session=session, cwd=cwd, timeout=timeout, identity=identity,
            wake_log=_wake_log_path(lane) if lane is not None else None)
    if provider == "opencode":
        return _run_opencode(
            persona, prompt, model=model, effort=effort, session=session, cwd=cwd,
            timeout=timeout, identity=identity,
            wake_log=_wake_log_path(lane) if lane is not None else None)
    raise RuntimeError("unknown provider %r" % provider)


def _selftest():
    global PERSONA_DIR

    import tests.cases as cases

    passed = failed = 0
    original_persona_dir = PERSONA_DIR
    persona_fixture = tempfile.TemporaryDirectory()
    PERSONA_DIR = Path(persona_fixture.name)
    for persona, expected in cases.PERSONA_FILES:
        if expected is True:
            (PERSONA_DIR / (persona + ".md")).write_text("fixture\n")
    for stderr, stdout, expected in cases.FAILURE_OUTPUTS:
        got = _failure_output(stderr, stdout)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _failure_output(%r, %r) -> %r wanted %r" %
                  (stderr, stdout, got, expected))

    for persona, model, session, effort, expected in cases.RUNNER_CMDS:
        got = _build_cmd(persona, model, session, effort, "hi")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _build_cmd(%r,%r,%r,%r) -> %r wanted %r" % (persona, model, session, effort, got, expected))

    for payload, expected in cases.RUNNER_PARSES:
        got = _parse(payload)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _parse(%r) -> %r wanted %r" % (payload, got, expected))

    for output, expected in cases.CLAUDE_STREAM_PARSES:
        try:
            got = _parse_claude_stream(output)
        except RuntimeError as exc:
            got = type(exc)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _parse_claude_stream(...) -> %r wanted %r" % (got, expected))

    for model, session, effort, output_path, expected in cases.CODEX_RUNNER_CMDS:
        got = _build_cmd_codex(model, session, effort, output_path, "hi")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _build_cmd_codex(%r,%r,%r) -> %r wanted %r" %
                  (model, session, effort, got, expected))

    for payload, expected in cases.CODEX_PARSES:
        try:
            got = _parse_codex(payload)
        except RuntimeError as exc:
            got = type(exc)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _parse_codex(...) -> %r wanted %r" % (got, expected))

    for model, session, effort, cwd, timeout, expected in cases.AGY_RUNNER_CMDS:
        got = _build_cmd_agy(model, session, effort, cwd, timeout, "hi")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _build_cmd_agy(%r,%r,%r,%r,%r) -> %r wanted %r" %
                  (model, session, effort, cwd, timeout, got, expected))

    for payload, session, expected in cases.AGY_PARSES:
        try:
            got = _parse_agy(payload, session)
        except RuntimeError as exc:
            got = type(exc)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _parse_agy(...) -> %r wanted %r" % (got, expected))

    for raw, expected in cases.FRONTMATTER_REMOVALS:
        got = _without_frontmatter(raw)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _without_frontmatter(%r) -> %r wanted %r" % (raw, got, expected))

    for raw, max_lines, max_bytes, expected in cases.MEMORY_CLIPS:
        got = _clip_memory(raw, max_lines, max_bytes)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _clip_memory(...) -> %r wanted %r" % (got, expected))

    original_memory_dir = constants.MEMORY_DIR
    try:
        with tempfile.TemporaryDirectory() as root:
            constants.MEMORY_DIR = Path(root)
            memory_dir = constants.MEMORY_DIR / "bob"
            memory_dir.mkdir()
            (memory_dir / "MEMORY.md").write_text("hot sentinel\n")
            memory = _memory_frame("bob")
            for provider in cases.MEMORY_PROMPT_PROVIDERS:
                fresh = _run_prompt(provider, "bob", "wake", None, None)
                resumed = _run_prompt(provider, "bob", "wake", "session", None)
                if fresh.count(memory) == 1 and resumed == "wake":
                    passed += 1
                else:
                    failed += 1
                    print("FAIL %s fresh/resume memory framing" % provider)
            for persona, identity, expected in cases.MEMORY_IDENTITIES:
                seat_dir = constants.MEMORY_DIR / expected
                seat_dir.mkdir(exist_ok=True)
                (seat_dir / "MEMORY.md").write_text("sentinel for %s\n" % expected)
                got = _first_prompt("agy", persona, "wake", identity)
                if ("sentinel for %s" % expected) in got:
                    passed += 1
                else:
                    failed += 1
                    print("FAIL memory frame for persona %r identity %r wanted %s" %
                          (persona, identity, expected))
    finally:
        constants.MEMORY_DIR = original_memory_dir

    for persona, expected in cases.PERSONA_FILES:
        try:
            got = _persona_file(persona).is_file()
        except RuntimeError:
            got = "raised"
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _persona_file(%r) resolved %r wanted %r" %
                  (persona, got, expected))

    for model, session, effort, cwd, expected in cases.OPENCODE_RUNNER_CMDS:
        got = _build_cmd_opencode(model, session, effort, cwd, "hi")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _build_cmd_opencode(%r,%r,%r,%r) -> %r wanted %r" %
                  (model, session, effort, cwd, got, expected))

    for payload, session, expected in cases.OPENCODE_PARSES:
        try:
            got = _parse_opencode(payload, session)
        except RuntimeError as exc:
            got = type(exc)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _parse_opencode(...) -> %r wanted %r" % (got, expected))

    for inherited, expected in cases.OPENCODE_ENVIRONMENTS:
        before = os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE")
        captured = {}

        def fake_run(*args, **kwargs):
            captured.update(kwargs["env"])
            return subprocess.CompletedProcess(
                args[0], 0, stdout=(
                    '{"type":"step_start","sessionID":"session"}\n'
                    '{"type":"text","part":{"text":"ok"}}'), stderr="")

        original_run = subprocess.run
        try:
            if inherited is None:
                os.environ.pop("OPENCODE_DISABLE_CLAUDE_CODE", None)
            else:
                os.environ["OPENCODE_DISABLE_CLAUDE_CODE"] = inherited
            subprocess.run = fake_run
            _run_opencode(
                "bob", "hi", model="model", effort="high", session="session",
                cwd=REPO_DIR, timeout=1, identity=None)
        finally:
            subprocess.run = original_run
            if before is None:
                os.environ.pop("OPENCODE_DISABLE_CLAUDE_CODE", None)
            else:
                os.environ["OPENCODE_DISABLE_CLAUDE_CODE"] = before
        got = captured.get("OPENCODE_DISABLE_CLAUDE_CODE")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL OpenCode env from %r -> %r wanted %r" %
                  (inherited, got, expected))

    for inherited, expected in cases.CLAUDE_ENVIRONMENTS:
        before = os.environ.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY")
        captured = {}

        def fake_run(*args, **kwargs):
            captured.update(kwargs["env"])
            return subprocess.CompletedProcess(
                args[0], 0, stdout=(
                    '{"type":"result","subtype":"success","is_error":false,'
                    '"result":"ok","session_id":"session","total_cost_usd":0,'
                    '"num_turns":1}\n'), stderr="")

        original_run = subprocess.run
        try:
            if inherited is None:
                os.environ.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY", None)
            else:
                os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = inherited
            subprocess.run = fake_run
            _run_claude(
                "bob", "hi", model="model", effort="high", session="session",
                cwd=REPO_DIR, timeout=1, identity=None)
        finally:
            subprocess.run = original_run
            if before is None:
                os.environ.pop("CLAUDE_CODE_DISABLE_AUTO_MEMORY", None)
            else:
                os.environ["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = before
        got = captured.get("CLAUDE_CODE_DISABLE_AUTO_MEMORY")
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL Claude env from %r -> %r wanted %r" %
                  (inherited, got, expected))

    claude_logs = []
    old_claude = _run_claude
    try:
        globals()["_run_claude"] = lambda *a, **kw: claude_logs.append(kw.get("wake_log"))
        run("bob", "hi", provider="claude", lane=cases.CLAUDE_LOG_LANE)
    finally:
        globals()["_run_claude"] = old_claude
    expected_log = _wake_log_path(cases.CLAUDE_LOG_LANE)
    if claude_logs == [expected_log]:
        passed += 1
    else:
        failed += 1
        print("FAIL Claude run lane log -> %r wanted %r" % (claude_logs, expected_log))

    for stream_id, topic, expected in cases.WAKE_SLUGS:
        got = wake_slug(stream_id, topic)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL wake_slug(%r, %r) -> %r wanted %r" % (stream_id, topic, got, expected))

    for lane, expected in cases.WAKE_LOG_SLUGS:
        got = _wake_log_path(lane).name
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _wake_log_path(%r) -> %r wanted %r" % (lane, got, expected))

    for event, expected in cases.LAST_ACTION_EVENTS:
        got = _action_key(event)
        got = constants.LAST_ACTION_LABELS.get(got)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL last action event %r -> %r wanted %r" % (event, got, expected))

    with tempfile.TemporaryDirectory() as root:
        old_logs = constants.LOGS_DIR
        try:
            constants.LOGS_DIR = Path(root)
            path = _wake_log_path(cases.LAST_ACTION_LANE)
            path.parent.mkdir(parents=True)
            path.write_text(cases.LAST_ACTION_LOG)
            got = last_action(cases.LAST_ACTION_LANE)
        finally:
            constants.LOGS_DIR = old_logs
        if got == cases.LAST_ACTION_EXPECTED:
            passed += 1
        else:
            failed += 1
            print("FAIL last_action tail -> %r wanted %r" % (got, cases.LAST_ACTION_EXPECTED))

    with tempfile.TemporaryDirectory() as root:
        wake_log = Path(root) / "wake.jsonl"
        wake_log.write_text("stale\n")
        proc, got = _run_jsonl(
            [sys.executable, "-c", 'print("fresh")'], cwd=REPO_DIR,
            env=dict(os.environ), timeout=5, wake_log=wake_log)
        if proc.returncode == 0 and got == "fresh\n" and wake_log.read_text() == got:
            passed += 1
        else:
            failed += 1
            print("FAIL wake log truncate/read -> %r, %r" % (proc.returncode, got))
        try:
            _run_jsonl(
                [sys.executable, "-c",
                 'import time; print("partial", flush=True); time.sleep(5)'],
                cwd=REPO_DIR, env=dict(os.environ), timeout=0.2, wake_log=wake_log)
        except subprocess.TimeoutExpired:
            got = wake_log.read_text()
        else:
            got = "no timeout"
        if got == "partial\n":
            passed += 1
        else:
            failed += 1
            print("FAIL killed wake log retained %r wanted 'partial\\n'" % got)

    original_repo_dir = REPO_DIR
    log.disabled = True  # the "real" row logs its own warning by design
    try:
        for state, expected in cases.LINK_REPAIRS:
            with tempfile.TemporaryDirectory() as root:
                globals()["REPO_DIR"] = Path(root) / "repo"
                (REPO_DIR / "config").mkdir(parents=True)
                for name in _WORKTREE_LINKS:
                    (REPO_DIR / name).write_text("source")
                tree = Path(root) / "tree"
                (tree / "config").mkdir(parents=True)
                name = _WORKTREE_LINKS[1]
                target = tree / name
                if state == "correct":
                    target.symlink_to(REPO_DIR / name)
                elif state == "elsewhere":
                    target.symlink_to(REPO_DIR / _WORKTREE_LINKS[2])
                elif state == "dangling":
                    target.symlink_to(REPO_DIR / "gone")
                elif state == "real":
                    target.write_text("mine")
                _ensure_links(tree)
                if expected == "link":
                    got = target.is_symlink() and target.readlink() == REPO_DIR / name
                else:
                    got = not target.is_symlink() and target.read_text() == "mine"
                if got:
                    passed += 1
                else:
                    failed += 1
                    print("FAIL _ensure_links from %r link state wanted %r" % (state, expected))
    finally:
        globals()["REPO_DIR"] = original_repo_dir
        log.disabled = False

    log.disabled = True
    for failed_args, behind, expected, expected_args in cases.WORKTREE_REFRESHES:
        calls = []

        def fake_run(cmd, **kwargs):
            args = tuple(cmd[3:])
            calls.append(args)
            if args == ("rev-list", "--count", "HEAD..origin/main"):
                return subprocess.CompletedProcess(cmd, 0, stdout=behind + "\n", stderr="")
            return subprocess.CompletedProcess(
                cmd, 1 if args == failed_args else 0, stdout="", stderr="stopped")

        original_run = subprocess.run
        subprocess.run = fake_run
        try:
            got = _refresh_worktree(Path("/tmp/tree"))
        finally:
            subprocess.run = original_run
        if got == expected and calls == expected_args:
            passed += 1
        else:
            failed += 1
            print("FAIL _refresh_worktree failed=%r -> (%r, %r) wanted (%r, %r)" %
                  (failed_args, got, calls, expected, expected_args))
    log.disabled = False

    # run() driven for real with subprocess stubbed, so each fallback is proved at the call site.
    for provider, model, effort, fragments in cases.RUNNER_DEFAULT_FALLBACKS:
        captured = []

        def fake_run(*args, **kwargs):
            captured.extend(args[0])
            return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="stop")

        original_run = subprocess.run
        subprocess.run = fake_run
        try:
            run("bob", "hi", provider=provider, model=model, effort=effort,
                cwd=REPO_DIR, timeout=1)
        except RuntimeError:
            pass
        finally:
            subprocess.run = original_run
        built = " ".join(str(part) for part in captured[:-1])  # the prompt is always last
        for fragment in fragments:
            if fragment in built:
                passed += 1
            else:
                failed += 1
                print("FAIL run(provider=%r, model=%r, effort=%r) built no %r" %
                      (provider, model, effort, fragment))

    for identity, exists, expected in cases.WORKTREE_ROUTES:
        got = wants_worktree(identity, exists)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL wants_worktree(%r, %r) -> %r wanted %r" %
                  (identity, exists, got, expected))

    for persona, identity, expected in cases.WAKE_IDENTITIES:
        got = _wake_identity(persona, identity)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL _wake_identity(%r, %r) -> %r wanted %r" % (persona, identity, got, expected))

    # main() driven for real with run() stubbed: a flag that parses but never reaches run() fails here.
    import io

    class _Result:
        pass

    for argv, expected in cases.CLI_IDENTITY_ARGS:
        seen = []

        def _capture_run(persona, prompt, **kw):
            seen.append(kw.get("identity"))
            return _Result()

        saved = (sys.argv, globals()["run"], sys.stdout)
        try:
            sys.argv = ["runner.py"] + argv
            globals()["run"] = _capture_run
            sys.stdout = io.StringIO()
            main()
        finally:
            sys.argv, globals()["run"], sys.stdout = saved
        got = seen[0] if seen else "run() never called"
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL main(%r) passed identity %r wanted %r" % (argv, got, expected))

    PERSONA_DIR = original_persona_dir
    persona_fixture.cleanup()
    print("runner.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--persona")
    ap.add_argument("--provider", choices=("claude", "codex", "agy", "opencode"))
    ap.add_argument("--model")
    ap.add_argument("--effort")
    ap.add_argument("--resume")
    # without this a hand-run operator seat falls back to the persona name and reads
    # memory/operator-reply/, which does not exist; both listener rails pass identity=bridge.
    ap.add_argument("--identity")
    ap.add_argument("prompt", nargs="?")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if not args.persona or not args.provider or not args.prompt:
        ap.error("--persona, --provider and a prompt are required")
    result = run(args.persona, args.prompt, provider=args.provider, model=args.model,
                 effort=args.effort, session=args.resume, identity=args.identity)
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
