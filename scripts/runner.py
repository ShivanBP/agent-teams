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
    degraded: str = ""


def _failure_output(stderr, stdout):
    if isinstance(stderr, Path):
        try:
            stderr = stderr.read_text()
        except OSError:
            stderr = ""
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


def _run_jsonl(cmd, *, cwd, env, timeout, wake_log, stdin=subprocess.DEVNULL,
               tee_stderr=False):
    stderr_log = wake_log.with_suffix(".err") if wake_log is not None and tee_stderr else None
    # Without DEVNULL an unattended CLI can read or hold the listener's stdin open and block
    # (Peter, 2026-08-16).
    kwargs = {
        "cwd": str(cwd), "env": env,
        "text": True, "timeout": timeout, "stdin": stdin,
    }
    if wake_log is None:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
        return proc, proc.stdout
    wake_log.parent.mkdir(parents=True, exist_ok=True)
    if stderr_log is None:
        with wake_log.open("w") as output:
            proc = subprocess.run(cmd, stdout=output, stderr=subprocess.PIPE, **kwargs)
    else:
        with wake_log.open("w") as output, stderr_log.open("w") as errors:
            proc = subprocess.run(cmd, stdout=output, stderr=errors, **kwargs)
        proc.stderr = stderr_log
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
    reply = payload.get("response")
    degraded = ""
    if payload.get("status") != "SUCCESS" and (
            not isinstance(reply, str) or not reply.strip()):
        raise RuntimeError("agy status is not SUCCESS: %r" % payload.get("status"))
    session_id = payload.get("conversation_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise RuntimeError("agy JSON has no conversation id")
    if session and session_id != session:
        raise RuntimeError("agy resumed conversation %s returned id %s" % (session, session_id))
    if not isinstance(reply, str) or not reply.strip():
        raise RuntimeError("agy returned an empty response")
    if payload.get("status") != "SUCCESS":
        degraded = " ".join(str(payload.get("error") or payload.get("status")).split())[:2000]
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
    return reply, session_id, 1, usage, degraded


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
        proc, stdout = _run_jsonl(
            cmd, cwd=run_cwd, env=env, timeout=timeout, wake_log=wake_log,
            tee_stderr=True)
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
        cmd, cwd=run_cwd, env=env, timeout=timeout, wake_log=wake_log,
        tee_stderr=True)
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
        cmd, cwd=run_cwd, env=env, timeout=timeout, wake_log=wake_log,
        tee_stderr=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "agy failed (exit %d) for persona %s: %s" %
            (proc.returncode, persona, _failure_output(proc.stderr, stdout)))
    reply, session_id, turns, usage, degraded = _parse_agy(stdout, session)
    return Result(reply=reply.strip(), session_id=session_id, cost_usd=0.0, turns=turns,
                  usage=usage, provider="agy", degraded=degraded)


def wake_slug(stream_id, topic):
    """Directory and branch name for a topic's build worktree; normalize_topic only strips the
    resolve prefix, so the rest of the filesystem-unsafe characters go here."""
    text = store.normalize_topic(topic).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")
    return "%s-%s" % (stream_id, slug) if slug else str(stream_id)


def wants_worktree(identity, exists, build=None, join=None):
    """Builders always get one; verifiers join the topic's worktree only when it is already there."""
    if identity in (constants.WORKTREE_PERSONAS if build is None else build):
        return True
    return bool(exists) and identity in (constants.WORKTREE_JOIN if join is None else join)


_WORKTREE_LINKS = (".venv", "config/persona-matrix.json", "config/harness-defaults.json",
                   "config/model-effort-defaults.json", "config/channels.json")


def _worktree_add(path, branch):
    """git worktree add, reusing the branch if it exists."""
    constants.WORKTREE_ROOT.mkdir(parents=True, exist_ok=True)
    has_branch = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True, text=True, timeout=constants.GIT_CMD_TIMEOUT).returncode == 0
    cmd = ["git", "-C", str(REPO_DIR), "worktree", "add"]
    cmd += [str(path), branch] if has_branch else ["-b", branch, str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=constants.GIT_CMD_TIMEOUT)
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
                capture_output=True, text=True, timeout=constants.GIT_CMD_TIMEOUT)
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
                capture_output=True, text=True, timeout=constants.GIT_CMD_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            count = subprocess.run(
                ["git", "-C", str(path), "rev-list", "--count", "HEAD..origin/main"],
                capture_output=True, text=True, timeout=constants.GIT_CMD_TIMEOUT)
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
            persona, prompt, model=model or constants.OPENCODE_MODEL,
            effort=effort or constants.translate_effort("opencode", constants.OPENCODE_VARIANT),
            session=session, cwd=cwd,
            timeout=timeout, identity=identity,
            wake_log=_wake_log_path(lane) if lane is not None else None)
    raise RuntimeError("unknown provider %r" % provider)


def _selftest():
    from tests import runner_selftest
    return runner_selftest.run(sys.modules[__name__])


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
