"""Offline fixtures for runner.py, run in the organ namespace."""

from pathlib import Path
import sys
from types import FunctionType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(module):
    return FunctionType(_body.__code__, module.__dict__)()


def _body():
    global PERSONA_DIR

    import os
    from pathlib import Path
    import subprocess
    import sys
    import tempfile

    import constants
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

    for inherited, expected, expected_stdin in cases.OPENCODE_ENVIRONMENTS:
        before = os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE")
        captured = {}

        def fake_run(*args, **kwargs):
            captured["stdin"] = kwargs.get("stdin")
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
        got_stdin = "devnull" if captured.get("stdin") == subprocess.DEVNULL else "other"
        if got == expected and got_stdin == expected_stdin:
            passed += 1
        else:
            failed += 1
            print("FAIL OpenCode env/stdin from %r -> %r/%r wanted %r/%r" %
                  (inherited, got, got_stdin, expected, expected_stdin))

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
        import time as time_mod
        code, expected_stdout, expected_stderr, max_seconds = cases.JSONL_BACKGROUND
        started = time_mod.monotonic()
        proc, got = _run_jsonl(
            [sys.executable, "-c", code], cwd=REPO_DIR,
            env=dict(os.environ), timeout=5, wake_log=wake_log, tee_stderr=True)
        elapsed = time_mod.monotonic() - started
        stderr_log = wake_log.with_suffix(".err")
        if (proc.returncode == 0 and got == expected_stdout
                and stderr_log.read_text() == expected_stderr
                and _failure_output(proc.stderr, got) == "child err\nfresh"
                and elapsed < max_seconds):
            passed += 1
        else:
            failed += 1
            print("FAIL stderr tee -> %r, %r, %.2fs wanted %r, %r, <%.2fs" %
                  (proc.returncode, got, elapsed, expected_stdout, expected_stderr, max_seconds))

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
        got = wants_worktree(identity, exists, cases.WORKTREE_BUILD, cases.WORKTREE_JOIN)
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
