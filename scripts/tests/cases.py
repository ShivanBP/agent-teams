"""Case tables, pure data. A table changes in the same commit as its organ."""

import prompts

Z = "\u200b"

# Imports absent until listener runtime, so its offline selftest needs no third-party package.
LISTENER_LAZY_GLOBALS = ["zulip"]

# (input, expected) for api.strip_wildcards
WILDCARDS = [
    ("nothing to see", "nothing to see"),
    ("ping @**all** now", "ping @" + Z + "**all** now"),
    ("@**everyone**", "@" + Z + "**everyone**"),
    ("@**channel** and @**topic** and @**stream**",
     "@" + Z + "**channel** and @" + Z + "**topic** and @" + Z + "**stream**"),
    ("@_**all**", "@" + Z + "_**all**"),
    ("@**Mate Molnar** stays", "@**Mate Molnar** stays"),
    ("code `@**all**` too", "code `@" + Z + "**all**` too"),
    ("", ""),
]

# (sender, input, expected) for send.strip_persona_mentions
PERSONA_MENTIONS = [
    ("archie", "ask @**bob** to check", "ask @" + Z + "**bob** to check"),
    ("archie", "@**Bob|123** here", "@" + Z + "**Bob|123** here"),
    ("bridge", "kick @**archie** build", "kick @**archie** build"),
    ("archie", "alert @**Soto**", "alert @**Soto**"),
    ("archie", "@**all** hands", "@**all** hands"),
]

# (params, expected form encoding) for api._encode
ENCODE = [
    ({"topic": "phase 1"}, "topic=phase+1"),
    ({"to": 5}, "to=5"),
    ({"event_types": [], "fetch_event_types": ["realm"]},
     "event_types=%5B%5D&fetch_event_types=%5B%22realm%22%5D"),
    ({"apply_markdown": False}, "apply_markdown=false"),
]

# (as_name, AGENT_TEAM_IDENTITY or None, should exit 2) for api.enforce_identity
IDENTITY = [
    ("archie", None, False),
    ("archie", "archie", False),
    ("archie", "eve", True),
    ("bridge", "bridge", False),
]

# (path, index, exists, is_dir, size, is_symlink, expected refusal key or None) for send.classify_attach
ATTACHES = [
    ("/Users/soto/Projects/agent-team/plans/p.md", 0, True, False, 1024, False, None),
    ("/Users/soto/.config/agent-team/logs/day.log", 0, True, False, 1024, False, None),
    ("/Users/soto/Desktop/notes.md", 0, True, False, 10, False, "outside_roots"),
    ("relative/path.md", 0, True, False, 10, False, "outside_roots"),
    ("/Users/soto/Projects/../Desktop/x.md", 0, True, False, 10, False, "outside_roots"),
    ("/Users/soto/Projects/agent-team/.env", 0, True, False, 10, False, "dot_name"),
    ("/Users/soto/Projects/agent-team/plans", 0, True, True, 0, False, "directory"),
    ("/Users/soto/Projects/agent-team/big.bin", 0, True, False, 10 * 1024 * 1024 + 1, False, "too_large"),
    ("/Users/soto/Projects/agent-team/edge.bin", 0, True, False, 10 * 1024 * 1024, False, None),
    ("/Users/soto/Projects/agent-team/gone.md", 0, False, False, 0, False, "missing"),
    ("/Users/soto/Projects/agent-team/plans/p.md", 3, True, False, 10, False, None),
    ("/Users/soto/Projects/agent-team/plans/p.md", 4, True, False, 10, False, "too_many"),
    # symlink refusal fires before dot-name/missing/directory checks, regardless of target
    ("/Users/soto/Projects/agent-team/plans/.symlink-test", 0, True, False, 10, True, "symlink"),
    ("/Users/soto/Projects/agent-team/plans/link-to-ssh", 0, False, False, 0, True, "symlink"),
]

# (body length, window, should refuse) for the window boundary
WINDOW = [
    (0, 10000, False),
    (9999, 10000, False),
    (10000, 10000, False),
    (10001, 10000, True),
    (20000, 10000, True),
]

# (body, expected body after extraction, expected accepted paths) for send._extract
EXTRACTS = [
    ("plain body", "plain body", []),
    ("before\n[attach: /Users/soto/Desktop/x.md]\nafter",
     "before\n[attach refused: /Users/soto/Desktop/x.md is outside the allowed roots]\nafter",
     []),
    ("[attach: /Users/soto/Projects/agent-team/.hidden]",
     "[attach refused: /Users/soto/Projects/agent-team/.hidden is a dot-name file]",
     []),
    ("[attach: /Users/soto/Projects/agent-team/nope.md]",
     "[attach refused: /Users/soto/Projects/agent-team/nope.md does not exist]",
     []),
]

# (as_name, expected allowed) for send.verb_allowed (--resolve / --move-to are bridge-only)
VERB_GATE = [
    ("bridge", True),
    ("bob", False),
    ("archie", False),
    ("", False),
]

# (content, expected) for read.indent
INDENTS = [
    ("one line", "one line"),
    ("two\nlines", "two\n    lines"),
    ("**bold** stays", "**bold** stays"),
    ("a\nb\nc", "a\n    b\n    c"),
]

# (anchor argument, expected parsed value) for read._anchor
ANCHORS = [
    ("newest", "newest"),
    ("oldest", "oldest"),
    ("12345", 12345),
]

# (site, stream_id, stream_name, topic, message_id, expected url) for api.permalink
# All values are intentionally fake fixtures.
PERMALINKS = [
    ("https://example.zulipchat.com", 100, "setup", "Topic naming/numbering convention", 200,
     "https://example.zulipchat.com/#narrow/channel/100-setup/"
     "topic/Topic.20naming.2Fnumbering.20convention/near/200"),
    ("https://example.zulipchat.com", 7, "general", "plain topic", 100,
     "https://example.zulipchat.com/#narrow/channel/7-general/topic/plain.20topic/near/100"),
    ("https://example.zulipchat.com", 3, "two words", "topic", 1,
     "https://example.zulipchat.com/#narrow/channel/3-two-words/topic/topic/near/1"),
    # non-ASCII topic: multi-byte UTF-8 sequences each become their own .XX run
    ("https://example.zulipchat.com", 9, "setup", "café ☕", 42,
     "https://example.zulipchat.com/#narrow/channel/9-setup/"
     "topic/caf.C3.A9.20.E2.98.95/near/42"),
    # literal dot in a topic (Jan, 2026-08-12): quote() leaves '.' unencoded, so it must be
    # escaped to %2E before the %-to-. pass, or Zulip's decoder reads it back as a stray '%'
    ("https://example.zulipchat.com", 11, "setup", "api.py cleanup", 7,
     "https://example.zulipchat.com/#narrow/channel/11-setup/"
     "topic/api.2Epy.20cleanup/near/7"),
]

# (stream_id, topic, persona, expected) for store.lane_key
LANE_KEYS = [
    (42, "phase 1", "peter", "42:phase 1:peter"),
    ("42", "phase 1", "peter", "42:phase 1:peter"),
    (7, "topic:with:colons", "eve", "7:topic:with:colons:eve"),
    # resolve-normalization (finding 1a): resolved and plain forms of the same topic share a lane
    (1, "✔ gate2 item9", "peter", "1:gate2 item9:peter"),
    (1, "gate2 item9", "peter", "1:gate2 item9:peter"),
]

# (persona, model, session, effort, expected argv) for runner._build_cmd
FAILURE_OUTPUTS = [
    ("", '{"error":"quota"}\n', '{"error":"quota"}'),
    (" warning\n", " detail\n", "warning\ndetail"),
    ("x" * 2100, "", "x" * 2000),
]


RUNNER_CMDS = [
    ("peter", None, None, None,
     ["claude", "-p", "--dangerously-skip-permissions",
      "--output-format", "json", "--agent", "peter", "hi"]),
    ("bob", "sonnet", None, None,
     ["claude", "-p", "--dangerously-skip-permissions",
      "--output-format", "json", "--agent", "bob", "--model", "sonnet", "hi"]),
    ("archie", "fable", "sid-123", "high",
     ["claude", "-p", "--dangerously-skip-permissions",
      "--output-format", "json", "--agent", "archie",
      "--model", "fable", "--resume", "sid-123", "--effort", "high", "hi"]),
    ("eve", None, "sid-9", None,
     ["claude", "-p", "--dangerously-skip-permissions",
      "--output-format", "json", "--agent", "eve", "--resume", "sid-9", "hi"]),
]

CODEX_RUNNER_CMDS = [
    (
        "gpt-5.6-sol", None, "high", "/tmp/final.txt",
        [
            "/Applications/ChatGPT.app/Contents/Resources/codex", "exec",
            "--json", "--strict-config",
            "-c", 'approval_policy="never"',
            "-c", "features.memories=false",
            "-c", 'sandbox_mode="danger-full-access"',
            "-c", 'model_reasoning_effort="high"',
            "-m", "gpt-5.6-sol", "-o", "/tmp/final.txt", "hi",
        ],
    ),
    (
        "gpt-5.6-sol", "019ffcaf-probe", "medium", "/tmp/resume.txt",
        [
            "/Applications/ChatGPT.app/Contents/Resources/codex", "exec", "resume",
            "--json", "--strict-config",
            "-c", 'approval_policy="never"',
            "-c", "features.memories=false",
            "-c", 'sandbox_mode="danger-full-access"',
            "-c", 'model_reasoning_effort="medium"',
            "-m", "gpt-5.6-sol", "-o", "/tmp/resume.txt",
            "019ffcaf-probe", "hi",
        ],
    ),
]

# (provider, --model value, --effort value, fragments the built command must contain) for
# runner.run: a hand-driven run that omits either flag takes the harness default instead of
# handing subprocess a None. Fragments match against the joined command minus its prompt, so
# each one proves the flag and its value stayed adjacent.
RUNNER_DEFAULT_FALLBACKS = [
    ("codex", None, None, ("-m gpt-5.6-sol", 'model_reasoning_effort="high"')),
    ("codex", "gpt-5.6-sol-mini", "low", ("-m gpt-5.6-sol-mini", 'model_reasoning_effort="low"')),
    ("agy", None, None, ("--model gemini-3.7-flash", "--effort high")),
    ("agy", "gemini-3.7-pro", "low", ("--model gemini-3.7-pro", "--effort low")),
]

AGY_RUNNER_CMDS = [
    (
        "gemini-3.7-flash", None, "high", "/tmp/probe", 1800,
        [
            "/Users/soto/.local/bin/agy",
            "--dangerously-skip-permissions", "--disable-slash-commands",
            "--add-dir", "/tmp/probe", "--model", "gemini-3.7-flash",
            "--effort", "high", "--output-format", "stream-json",
            "--print-timeout", "1800s", "-p", "hi",
        ],
    ),
    (
        "gemini-3.7-flash", "agy-session-1", "medium", "/tmp/resume", 90,
        [
            "/Users/soto/.local/bin/agy",
            "--dangerously-skip-permissions", "--disable-slash-commands",
            "--add-dir", "/tmp/resume", "--model", "gemini-3.7-flash",
            "--effort", "medium", "--output-format", "stream-json",
            "--print-timeout", "90s", "--conversation", "agy-session-1", "-p", "hi",
        ],
    ),
]

FRONTMATTER_REMOVALS = [
    ("---\nname: bob\nmodel: opus\n---\nBuilder body.\n", "Builder body."),
    ("Persona body without frontmatter.\n", "Persona body without frontmatter."),
]

MEMORY_CLIPS = [
    (b"short\nmemory\n", 200, 25 * 1024, ("short\nmemory\n", False)),
    (b"one\ntwo\nthree\n", 2, 25 * 1024, ("one\ntwo\n", True)),
    ("éé\n".encode("utf-8"), 200, 3, ("é", True)),
]

MEMORY_PROMPT_PROVIDERS = ("claude", "codex", "agy", "opencode")

CLAUDE_ENVIRONMENTS = [
    (None, "1"),
    ("0", "1"),
    ("1", "1"),
]


# (persona, identity kwarg, expected AGENT_TEAM_IDENTITY) for runner._wake_identity
WAKE_IDENTITIES = [
    ("bob", None, "bob"),
    ("archie", None, "archie"),
    # operator-reply-as-bridge: the seat runs as the operator-reply agent but its subprocess
    # env carries AGENT_TEAM_IDENTITY=bridge, so --as bridge from inside it passes.
    ("operator-reply", "bridge", "bridge"),
    ("bridge", "bridge", "bridge"),
]

# (stream_id, topic, expected) for runner.wake_slug: safe as both a directory and a branch name,
# and resolved and plain forms of one topic must land in the same worktree.
WAKE_SLUGS = [
    (11, "worktree build", "11-worktree-build"),
    (11, "✔ worktree build", "11-worktree-build"),
    ("11", "worktree build", "11-worktree-build"),
    (7, "api.py cleanup: ünicode!", "7-api-py-cleanup-nicode"),
    (11, "worktrees for parallel builders in one repo",
     "11-worktrees-for-parallel-builders-in-one-r"),
    # truncation landing mid-separator still leaves no trailing hyphen
    (11, "a b c d e f g h i j k l m n o p q r s t u v",
     "11-a-b-c-d-e-f-g-h-i-j-k-l-m-n-o-p-q-r-s-t"),
    (5, "✔", "5"),
]

WAKE_LOG_SLUGS = [
    ("42:plain topic:jan", "42-plain-topic-jan.jsonl"),
    ("7:path/hostile: Bob?", "7-path-hostile-Bob.jsonl"),
    ("::", "wake.jsonl"),
]

# (identity, worktree exists, expected) for runner.wants_worktree
WORKTREE_ROUTES = [
    ("bob", False, True),
    ("peter", False, True),
    ("jan", False, False),
    ("jan", True, True),
    ("eve", True, True),
    ("archie", True, False),
]

# (starting state of one link, expected end state) for runner._ensure_links, which runs on every
# wake: anything but a real file of the fleet's own is repaired, so one bad creation is not forever.
LINK_REPAIRS = [
    ("missing", "link"),
    ("correct", "link"),
    ("elsewhere", "link"),
    ("dangling", "link"),
    ("real", "real"),
]

# (argv, refusal substring) for commit.main before it can take the git lock.
COMMIT_REFUSALS = [
    ([], "-m is required"),
    (["-m", "", "one.txt"], "non-empty"),
    (["-m", "message"], "at least one path"),
    (["-m", "message", "../outside.txt"], "outside"),
]

# (scenario, expected truth) for commit_selftest's repository and lock probes.
COMMIT_SCENARIOS = [
    ("clean push", True),
    ("rejected push rebases and pushes", True),
    ("rebase conflict aborts and names sha", True),
    ("dirty tree refusal skips abort", True),
    ("lock wait is bounded", True),
    ("pull fast-forwards", True),
    ("pull non-ff no-ops", True),
    ("killed holder frees lock", True),
    ("concurrent commits serialize", True),
    ("concurrent memory appends union", True),
    ("same-line memory edits stack", True),
    ("memory deletion race resurrects", True),
    ("absolute symlink path commits", True),
    ("missing private repo refuses personal path", True),
    ("personal paths route to private overlay", True),
]

# (failed git args or None, behind count, expected notice, exact git args) for the handoff refresh.
WORKTREE_REFRESHES = [
    (None, "0", "", [("fetch", "origin"), ("rebase", "origin/main")]),
    (("rebase", "origin/main"), "7",
     prompts.WORKTREE_STALE_WARNING.format(behind="7"),
     [("fetch", "origin"), ("rebase", "origin/main"), ("rebase", "--abort"),
      ("rev-list", "--count", "HEAD..origin/main")]),
    (("fetch", "origin"), "3",
     prompts.WORKTREE_STALE_WARNING.format(behind="3"),
     [("fetch", "origin"), ("rebase", "--abort"),
      ("rev-list", "--count", "HEAD..origin/main")]),
]

# (persona, True for an existing agent file or "raised") for runner._persona_file; the operator
# seats have an agent file and are deliberately absent from personas.PERSONAS.
PERSONA_FILES = [
    ("bob", True),
    ("operator", True),
    ("operator-reply", True),
    ("nobody", "raised"),
]

# (label, fixture files, expected names, error count) for personas._scan. The selftest never
# reads live agents/, which is private and absent from a public clone.
PERSONA_FIXTURES = [
    ("valid",
     [("planner.md", "---\nname: planner\nrole: planner\n---\nbody\n"),
      ("reviewer.md", "---\nname: reviewer\nrole: reviewer\n---\nbody\n"),
      ("operator.md", "operator fixture without frontmatter\n")],
     {"planner", "reviewer"}, 0),
    ("name mismatch",
     [("planner.md", "---\nname: other\n---\nbody\n")],
     set(), 1),
    ("missing frontmatter",
     [("planner.md", "plain body\n")],
     set(), 1),
]

# (persona, identity kwarg, expected memory dir name) for the frame runner._first_prompt builds:
# the memory dir follows the wake identity, so an operator seat woken as bridge reads memory/bridge.
MEMORY_IDENTITIES = [
    ("bob", None, "bob"),
    ("operator-reply", "bridge", "bridge"),
    ("operator", "bridge", "bridge"),
]

# (rail label, expected persona, expected identity kwarg) for the runner.run call each operator
# rail makes: both spawn under the bridge identity, so neither keys memory on its agent file name.
OPERATOR_SPAWNS = [
    ("rail A", "operator", "bridge"),
    ("rail B", "operator-reply", "bridge"),
]

# (label, tag age in minutes, expected receipt reactions) for handle_operator_tag. A tag the
# staleness guard drops must not get one, or backfill replays would react and then answer nothing.
OPERATOR_TAG_RECEIPTS = [
    ("fresh tag", 0, 1),
    ("stale tag", 24 * 60, 0),
]

# (rail label, substring the brief must carry) for the same two spawns. The state block reaches
# both rails, so dropping either state= kwarg at a call site turns one of these red.
OPERATOR_BRIEF_CONTAINS = [
    ("rail A", "Fleet state, read from the ledgers"),
    ("rail B", "Fleet state, read from the ledgers"),
]

# (argv after the program name, expected identity kwarg at the run() call) for runner.main.
# main() is driven for real with run() stubbed, so a flag that parses but is never passed
# through still fails: delete identity=args.identity and row one goes red.
CLI_IDENTITY_ARGS = [
    (["--persona", "operator-reply", "--provider", "claude", "--identity", "bridge", "go"],
     "bridge"),
    (["--persona", "bob", "--provider", "claude", "go"], None),
]

# Probe ledger names for the store.state_summary rows below; never the live ledger names.
STATE_PROBE_DAY = "selftest-day"
STATE_PROBE_LOOPS = "selftest-loops"
STATE_PROBE_INFLIGHT = "selftest-inflight"

# (fixture written to the probe ledgers, now, expected summary minus 'day') for
# store.state_summary. Cost rows carry ts None so 'at' pins the fallback exactly; the wall-clock
# rendering is timezone-dependent and is checked for shape in the selftest instead.
STATE_SUMMARIES = [
    (
        {"loops": {}, "inflight": {}, "cost": [], "kicks": []},
        1000.0,
        {"open_loops": [], "inflight": [], "wakes": [], "kicks": 0, "spend": 0.0},
    ),
    (
        {
            "loops": {"rows": {
                "aa": {"id": "aa", "channel": "setup", "topic": "voice", "kicks": 1,
                       "budget": 3, "status": "open"},
                "bb": {"id": "bb", "channel": "setup", "topic": "done", "kicks": 3,
                       "budget": 3, "status": "closed"},
            }},
            "inflight": {"1:voice:bob": {"ts": 700.0}},
            "cost": [{"persona": "bob", "usd": 0.5, "ts": None},
                     {"persona": "jan", "usd": 0.25, "ts": None}],
            "kicks": [{"persona": "bob"}, {"persona": "jan"}],
        },
        1000.0,
        {
            "open_loops": [{"id": "aa", "channel": "setup", "topic": "voice",
                            "kicks": 1, "budget": 3}],
            "inflight": [{"lane": "1:voice:bob", "age_min": 5}],
            "wakes": [{"persona": "bob", "at": "-"}, {"persona": "jan", "at": "-"}],
            "kicks": 2,
            "spend": 0.75,
        },
    ),
]

# (lane, stored session args, error the run raises, expected session_get afterwards) for the
# wake-failure path. One row: the drop is independent of provider, persona and error type.
WAKE_SESSION_CLEARED_ON_FAILURE = [
    ("selftest:wake failure:jan", ("sid-dead", 42, "claude"), RuntimeError("boom"), None),
]

# (event dict, expected is_mention) for listener.is_mention
MENTIONS = [
    ({"type": "message", "flags": ["mentioned"], "message": {}}, True),
    ({"type": "message", "message": {"flags": ["mentioned"]}}, True),  # backfill fallback
    ({"type": "message", "flags": ["read"], "message": {}}, False),
    ({"type": "message", "flags": [], "message": {}}, False),
    ({"type": "message", "message": {}}, False),
    ({"type": "message", "flags": ["wildcard_mentioned", "mentioned"], "message": {}}, True),
]

# (sender email, persona email set, expected refusal) for listener.is_persona_sender
PERSONA_SENDERS = [
    ("archie@example.com", {"archie@example.com", "bob@example.com"}, True),
    ("mate@example.com", {"archie@example.com", "bob@example.com"}, False),
    (None, {"archie@example.com"}, False),
    ("", {"archie@example.com"}, False),
]

# (topic name, expected is_resolved) for listener.is_resolved_topic
RESOLVED_TOPICS = [
    ("✔ phase 2 wake path", True),
    ("phase 2 wake path", False),
    ("  ✔ leading space", True),
    ("", False),
]

# (content, expected (flags, stripped_body)) for listener.parse_flags
# parse_flags detects and strips flag words for any sender; whether they apply is handle_wake's
# call (sender must resolve to Mate's own user id), never parse_flags'.
FLAG_PARSES = [
    ("plain body, no flags", ([], "plain body, no flags")),
    ("-opus do the thing", (["-opus"], "do the thing")),
    ("-high go", (["-high"], "go")),
    ("-low go", (["-low"], "go")),
    ("-mid go", (["-mid"], "go")),
    ("-xtra go", (["-xtra"], "go")),
    ("-fable draft this", (["-fable"], "draft this")),
    ("-sonnet go", (["-sonnet"], "go")),
    ("-codex build this", (["-codex"], "build this")),
    ("-claude take over", (["-claude"], "take over")),
    ("-agy spike this", (["-agy"], "spike this")),
    ("-opencode review this", (["-opencode"], "review this")),
    ("-codex -claude last wins", (["-codex", "-claude"], "last wins")),
    ("no dashes here -notaflag", ([], "no dashes here -notaflag")),
]

# (identity, flags, session row, matrix, expected provider) for listener.provider_for_wake
TEST_MATRIX = {
    "archie": {"provider": "claude", "model": "opus", "effort": "high"},
    "bob": {"provider": "claude", "model": "opus", "effort": "high"},
    "eve": {"provider": "claude", "model": "sonnet", "effort": "high"},
    "jan": {"provider": "opencode", "model": "fireworks-ai/accounts/fireworks/models/deepseek-v4-pro", "effort": "high"},
    "peter": {"provider": "agy", "model": "gemini-3.7-flash", "effort": "high"},
}

PROVIDER_SELECTIONS = [
    ("bob", ["-codex"], {}, TEST_MATRIX, "codex"),
    ("bob", ["-claude"], {"provider": "codex", "session_id": "c"}, TEST_MATRIX, "claude"),
    ("bob", [], {"provider": "codex", "session_id": "c"}, TEST_MATRIX, "codex"),
    ("bob", [], {"session_id": "legacy"}, TEST_MATRIX, "claude"),
    ("eve", ["-codex"], {}, TEST_MATRIX, "codex"),
    ("eve", ["-agy"], {}, TEST_MATRIX, "agy"),
    ("jan", ["-agy", "-claude", "-codex"], {}, TEST_MATRIX, "codex"),
    ("jan", [], {}, TEST_MATRIX, "opencode"),
    ("jan", [], {"provider": "claude", "session_id": "c"}, TEST_MATRIX, "claude"),
    ("jan", ["-opencode"], {}, TEST_MATRIX, "opencode"),
    ("peter", [], {}, TEST_MATRIX, "agy"),
    ("peter", [], {"provider": "claude", "session_id": "c"}, TEST_MATRIX, "claude"),
    ("archie", [], {}, TEST_MATRIX, "claude"),
]

# (identity, provider, model flag, effort flag, matrix, expected settings or exception)
WAKE_SETTINGS = [
    ("bob", "claude", None, None, TEST_MATRIX, ("opus", "high", "high")),
    ("bob", "claude", "fable", None, TEST_MATRIX, ("fable", "medium", "mid")),
    ("bob", "claude", "sonnet", None, TEST_MATRIX, ("sonnet", "high", "high")),
    ("bob", "claude", "opus", None, TEST_MATRIX, ("opus", "high", "high")),
    ("bob", "codex", None, None, TEST_MATRIX, ("gpt-5.6-sol", "high", "high")),
    ("bob", "codex", "opus", None, TEST_MATRIX, ("gpt-5.6-sol", "high", "high")),
    ("bob", "codex", None, "low", TEST_MATRIX, ("gpt-5.6-sol", "low", "low")),
    ("jan", "opencode", None, None, TEST_MATRIX,
     ("fireworks-ai/accounts/fireworks/models/deepseek-v4-pro", "high", "high")),
    ("jan", "agy", None, "xtra", TEST_MATRIX, RuntimeError),
]

EFFORT_TRANSLATIONS = [
    ("claude", "low", "low"),
    ("codex", "low", "low"),
    ("opencode", "low", "low"),
    ("agy", "low", "low"),
    ("claude", "mid", "medium"),
    ("codex", "mid", "medium"),
    ("opencode", "mid", "medium"),
    ("agy", "mid", "medium"),
    ("claude", "high", "high"),
    ("codex", "high", "high"),
    ("opencode", "high", "high"),
    ("agy", "high", "high"),
    ("claude", "xtra", "xhigh"),
    ("codex", "xtra", "xhigh"),
    ("opencode", "xtra", "xhigh"),
    ("agy", "xtra", RuntimeError),
]

MONITOR_INPUT = {
    "inflight": {"lane": {"persona": "jan", "provider": "codex", "topic": "setup"}},
    "cost_rows": [
        {"persona": "bob", "usd": 0.023},
        {"persona": "bob", "usd": 0.002},
        {"persona": "jan", "usd": 0.008},
    ],
    "kick_rows": [{"persona": "peter"}, {"persona": "peter"}],
    "matrix": {
        "archie": {"provider": "claude"},
        "bob": {"provider": "claude"},
        "chella": {"provider": "claude"},
        "eve": {"provider": "claude"},
        "jan": {"provider": "opencode"},
        "peter": {"provider": "agy"},
        "writer": {"provider": "claude"},
    },
}

MONITOR_EXPECTED = {
    "archie": {"provider": "claude", "status": "--", "topic": None,
               "cost_today": 0.0, "runs_today": 0, "kicks_today": 0},
    "bob": {"provider": "claude", "status": "--", "topic": None,
            "cost_today": 0.025, "runs_today": 2, "kicks_today": 0},
    "chella": {"provider": "claude", "status": "--", "topic": None,
               "cost_today": 0.0, "runs_today": 0, "kicks_today": 0},
    "eve": {"provider": "claude", "status": "--", "topic": None,
            "cost_today": 0.0, "runs_today": 0, "kicks_today": 0},
    "jan": {"provider": "codex", "status": "running", "topic": "setup",
            "cost_today": 0.008, "runs_today": 1, "kicks_today": 0},
    "peter": {"provider": "agy", "status": "--", "topic": None,
              "cost_today": 0.0, "runs_today": 0, "kicks_today": 2},
    "writer": {"provider": "claude", "status": "--", "topic": None,
               "cost_today": 0.0, "runs_today": 0, "kicks_today": 0},
}

# (with-narrow response payload, expected (stream_id, topic, content)) for loops._extract_location
WITH_NARROW = [
    ({"result": "success", "messages": [{"stream_id": 7, "subject": "phase 3 loop", "content": "kick off"}]},
     (7, "phase 3 loop", "kick off")),
    ({"result": "success", "messages": []}, (None, None, None)),
    ({"result": "error", "msg": "nope"}, (None, None, None)),
]

# (header_id, expected params) for loops._with_narrow_params -- the live-proven fix: anchor is
# the header id itself, never "newest" (that shape returns zero messages on this server, always)
WITH_NARROW_PARAMS = [
    (123456, {
        "anchor": 123456, "num_before": 0, "num_after": 0,
        "narrow": [{"operator": "with", "operand": 123456}], "apply_markdown": False,
    }),
    ("999", {
        "anchor": 999, "num_before": 0, "num_after": 0,
        "narrow": [{"operator": "with", "operand": 999}], "apply_markdown": False,
    }),
]

# (kicks, budget, expected reached) for loops._budget_reached
BUDGET_REACHED = [
    (0, 5, False),
    (4, 5, False),
    (5, 5, True),
    (6, 5, True),
    (0, 0, True),
]

# (operator reply text, expected parsed decision or None) for listener.parse_operator_decision
OPERATOR_DECISIONS = [
    ("KICK: peter build the thing", ("kick", "peter", "build the thing")),
    ("CLOSE: budget reached", ("close", "budget reached")),
    ("CLOSE:", None),
    ("KICK: peter", None),
    ("KICK:    peter   build it well", ("kick", "peter", "build it well")),
    ("just some prose", None),
    ("KICK: peter build it\nextra prose line", ("kick", "peter", "build it")),
    ("Reasoning first.\nCLOSE: work is done\nSigning off.", ("close", "work is done")),
    ("KICK: peter go\nCLOSE: or maybe not", None),
    ("", None),
    ("  KICK: bob ship it  ", ("kick", "bob", "ship it")),
]

# (refetch payload, fallback channel, fallback topic, expected (channel, topic)) for
# listener._location_from_refetch -- the refetch-fallback row: a failed refetch posts on the
# wake-time lane rather than raising or forging a location
LOCATION_REFETCH = [
    ({"result": "success", "message": {"display_recipient": "setup", "subject": "✔ renamed topic"}},
     "old-channel", "old-topic", ("setup", "✔ renamed topic")),
    ({"result": "success", "message": {}}, "old-channel", "old-topic", ("old-channel", "old-topic")),
    ({"result": "error", "msg": "nope"}, "old-channel", "old-topic", ("old-channel", "old-topic")),
    ({}, "old-channel", "old-topic", ("old-channel", "old-topic")),
]

# (message timestamp, now, TAG_MAX_AGE_MIN, expected is_stale) for listener.is_tag_stale
TAG_STALENESS = [
    (1000, 1000, 30, False),
    (1000, 1000 + 29 * 60, 30, False),
    (1000, 1000 + 30 * 60, 30, False),
    (1000, 1000 + 31 * 60, 30, True),
    (None, 1000, 30, True),
]

# (result JSON payload, expected Result fields) for runner._parse
RUNNER_PARSES = [
    (
        {"result": "RUNNER-OK", "session_id": "abc", "total_cost_usd": 0.0123, "num_turns": 1,
         "usage": {"cache_read_input_tokens": 100, "cache_creation_input_tokens": 50, "input_tokens": 10}},
        ("RUNNER-OK", "abc", 0.0123, 1, {"cache_read_input_tokens": 100, "cache_creation_input_tokens": 50, "input_tokens": 10}),
    ),
    (
        {"result": "no usage field", "session_id": "xyz", "total_cost_usd": 0.0, "num_turns": 0},
        ("no usage field", "xyz", 0.0, 0, {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "input_tokens": 0}),
    ),
]

# Captured from codex-cli 0.144.2 on this Mac, 2026-08-13.
CODEX_PARSES = [
    (
        '\n'.join([
            '{"type":"thread.started","thread_id":"019ffcaf-probe"}',
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}',
            '{"type":"turn.completed","usage":{"input_tokens":13956,'
            '"cached_input_tokens":9984,"output_tokens":11,"reasoning_output_tokens":0}}',
        ]),
        ("019ffcaf-probe", 1, {
            "cache_read_input_tokens": 9984,
            "cache_creation_input_tokens": 0,
            "input_tokens": 13956,
        }),
    ),
    (
        '\n'.join([
            '{"type":"thread.started","thread_id":"019ffcaf-probe"}',
            '{"type":"turn.failed","error":{"message":"model unavailable"}}',
        ]),
        RuntimeError,
    ),
    (
        '{"type":"thread.started","thread_id":"019ffcaf-probe"}',
        RuntimeError,
    ),
]

# Captured contract shape from agy 1.1.12, with failure variants reduced to parsed fields.
OPENCODE_RUNNER_CMDS = [
    (
        "fireworks-ai/accounts/fireworks/models/deepseek-v4-pro", None, "high", "/tmp/probe",
        [
            "/Users/soto/.opencode/bin/opencode", "run", "--format", "json", "--auto",
            "--model", "fireworks-ai/accounts/fireworks/models/deepseek-v4-pro",
            "--variant", "high", "--dir", "/tmp/probe", "hi",
        ],
    ),
    (
        "fireworks-ai/accounts/fireworks/models/deepseek-v4-pro", "sid-oc-1", None, None,
        [
            "/Users/soto/.opencode/bin/opencode", "run", "--format", "json", "--auto",
            "--model", "fireworks-ai/accounts/fireworks/models/deepseek-v4-pro",
            "--session", "sid-oc-1", "hi",
        ],
    ),
]

OPENCODE_ENVIRONMENTS = [
    (None, "true"),
    ("false", "true"),
]

# Captured from opencode run --format json on this Mac, 2026-08-13.
OPENCODE_PARSES = [
    (
        '\n'.join([
            '{"type":"step_start","timestamp":1786682178360,"sessionID":"ses_oc_probe","part":{"id":"prt_1","messageID":"msg_1","sessionID":"ses_oc_probe","type":"step-start"}}',
            '{"type":"text","timestamp":1786682178711,"sessionID":"ses_oc_probe","part":{"id":"prt_2","messageID":"msg_1","sessionID":"ses_oc_probe","type":"text","text":"hello","time":{"start":1786682178587,"end":1786682178698}}}',
            '{"type":"step_finish","timestamp":1786682178711,"sessionID":"ses_oc_probe","part":{"id":"prt_3","reason":"stop","messageID":"msg_1","sessionID":"ses_oc_probe","type":"step-finish","tokens":{"total":8409,"input":8389,"output":20,"reasoning":0,"cache":{"write":0,"read":0}},"cost":0.01466646}}',
        ]),
        None,
        ("hello", "ses_oc_probe", 0.01466646, 1, {
            "input_tokens": 8389, "output_tokens": 20, "total_tokens": 8409,
            "thinking_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }),
    ),
    (
        '{"type":"error","timestamp":1786682172205,"sessionID":"ses_err","error":{"name":"UnknownError","data":{"message":"server error"}}}',
        None,
        RuntimeError,
    ),
    (
        '{"type":"step_start","sessionID":"ses_no_text","part":{"type":"step-start"}}',
        None,
        RuntimeError,
    ),
]

AGY_PARSES = [
    (
        '\n'.join([
            '{"event":"init","conversation_id":"agy-stream"}',
            '{"event":"step_update","step_update":{"text_delta":"stream ok"}}',
            '{"event":"result","result":{"status":"SUCCESS",'
            '"conversation_id":"agy-stream","response":"stream ok",'
            '"usage":{"input_tokens":12,"output_tokens":2,"total_tokens":14}}}',
        ]),
        None,
        ("stream ok", "agy-stream", 1, {
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "input_tokens": 12, "output_tokens": 2, "total_tokens": 14,
        }),
    ),
    (
        '{"status":"SUCCESS","conversation_id":"agy-fresh","response":"fresh ok",'
        '"usage":{"input_tokens":10,"cache_read_tokens":4,"output_tokens":3,'
        '"thinking_tokens":2,"total_tokens":19}}',
        None,
        ("fresh ok", "agy-fresh", 1, {
            "cache_read_input_tokens": 4, "cache_creation_input_tokens": 0,
            "input_tokens": 10, "output_tokens": 3, "thinking_tokens": 2,
            "total_tokens": 19,
        }),
    ),
    (
        '{"status":"SUCCESS","conversation_id":"agy-resume","response":"resume ok",'
        '"usage":{"input_tokens":8,"cache_read_tokens":6}}',
        "agy-resume",
        ("resume ok", "agy-resume", 1, {
            "cache_read_input_tokens": 6, "cache_creation_input_tokens": 0,
            "input_tokens": 8,
        }),
    ),
    (
        'startup diagnostic\n{"status":"SUCCESS","conversation_id":"agy-noisy",'
        '"response":"noise ignored"}',
        None,
        ("noise ignored", "agy-noisy", 1, {
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "input_tokens": 0,
        }),
    ),
    ('{"status":"ERROR","conversation_id":"agy-bad","response":"no"}', None, RuntimeError),
    ('{"status":"SUCCESS","response":"no id"}', None, RuntimeError),
    ('{"status":"SUCCESS","conversation_id":"other","response":"wrong"}', "wanted", RuntimeError),
    ('{"status":"SUCCESS","conversation_id":"agy-empty","response":""}', None, RuntimeError),
    ("diagnostic only", None, RuntimeError),
]

# (substring) guards for prompts.WAKE_HEADER: ask-and-stop and distillation, reaching every
# persona from this one home
WAKE_HEADER_CONTAINS = [
    "post one question and end the wake",
    "only if the answer changes the work",
    "name a default so Mate can answer in one word",
    "writes the closing post and any STATE block before finishing",
    "sha-pinned GitHub link",
    "never blob/main",
    "put [attach: /abs/path] alone on a line",
    "the deliverable itself goes in the reply, never a pointer",
    "full history is searchable any time with scripts/read.py --search",
]

# (prompts attribute name, required substring): guards strings whose content no other table
# asserts, the vacancy behind the TAG_NOT_MATE copy-paste bug
PROMPT_CONTAINS = [
    ("TAG_NOT_MATE", "{sender}"),
    ("TAG_NOT_MATE", "not Mate"),
    ("TAG_STALE", "{max_age}"),
    ("MENTION", "@**{name}**"),
    ("MENTION", "{body}"),
    ("LOOP_BUDGET_REASON", "{budget}"),
    ("LOOP_NOTE", "only wake a persona here"),
    ("REPLY_TRUNCATION_NOTE", "{limit}"),
    ("PROVIDER_FRAME", "current waking message or approved brief"),
    ("PROVIDER_FRAME", "repository AGENTS.md is binding"),
    ("PROVIDER_FRAME", "persona definition governs your role"),
    ("PROVIDER_FRAME", "canonical memory snapshot is advisory"),
    ("MEMORY_FRAME", "Canonical persona memory root"),
    ("MEMORY_FRAME", "Use only this persona directory"),
    ("MEMORY_FRAME", "Write memory only through this absolute root"),
    ("MEMORY_TRUNCATION_NOTE", "memory truncated"),
    ("STATE_BLOCK", "already fetched"),
    ("OPERATOR_BRIEF", "{state}"),
    ("OPERATOR_REPLY_BRIEF", "{state}"),
]

# (summary dict, substrings the rendered block must carry) for prompts.state_block. Row one is
# the empty fleet: every line still renders, so a quiet fleet reads as quiet and not as a block
# that failed to build.
STATE_BLOCKS = [
    ({}, ["Open loops: none", "Lanes running now: none", "Wakes today: none",
          "Kicks today: 0", "Spend today: $0.00"]),
    (None, ["Open loops: none", "Spend today: $0.00"]),
    ({"open_loops": [{"id": "aa", "channel": "setup", "topic": "voice", "kicks": 1, "budget": 3}],
      "inflight": [{"lane": "1:voice:bob", "age_min": 5}],
      "wakes": [{"persona": "bob", "at": "09:12"}, {"persona": "jan", "at": "09:20"},
                {"persona": "bob", "at": "09:41"}],
      "kicks": 2, "spend": 0.75},
     ["aa in setup > voice (1/3 kicks)", "1:voice:bob (5m)",
      # counted per persona and carrying each one's latest time, not the raw list: the raw
      # form measured 1900 characters on 2026-08-16 and ships in every operator wake.
      "bob 2 (latest 09:41), jan 1 (latest 09:20), 3 total",
      "Kicks today: 2", "Spend today: $0.75"]),
]

# (body, record, handoff notice, expected) for prompts.wake_prompt
WAKE_PROMPTS = [
    ("do the thing", "", "",
     prompts.WAKE_HEADER + "\n\ndo the thing"),
    ("do the thing", "topic record here", "",
     prompts.WAKE_HEADER + "\n\ndo the thing\n\ntopic record here"),
    ("do the thing", "topic record here", "stale warning",
     prompts.WAKE_HEADER + "\n\nstale warning\n\ndo the thing\n\ntopic record here"),
]

NOTICED_REPLIES = [
    ("done", "", "done"),
    ("done", "stale warning", "stale warning\n\ndone"),
]

# (provider_prompt args, required substrings, forbidden substrings); the three providers auto-load
# AGENTS.md themselves, so no row may carry a repo rules block.
PROVIDER_PROMPTS = [
    (
        ("agy", "peter", "wake", "persona body", "memory body"),
        ("Peter running through agy", "memory body", "Current wake:\nwake"),
        ("Repository rules:",),
    ),
    (
        ("codex", "eve", "wake", "persona body", ""),
        ("Eve running through codex", "Current wake:\nwake"),
        ("Canonical persona memory root:", "Repository rules:"),
    ),
    (
        ("opencode", "jan", "wake", "persona body", "memory body"),
        ("Jan running through opencode", "memory body", "Current wake:\nwake"),
        ("Repository rules:",),
    ),
]

MEMORY_FRAMES = [
    (
        ("/tmp/memory", "/tmp/memory/bob/MEMORY.md", "hot\n", False, 200, 25600),
        "Canonical persona memory root: /tmp/memory\n"
        "Hot index snapshot: /tmp/memory/bob/MEMORY.md\n"
        "Use only this persona directory for durable judgment. Fetch current state fresh and read "
        "linked topic files only when needed.\n"
        "Write memory only through this absolute root; a relative memory/ path in a worktree is "
        "lost with the tree.\n\nhot\n",
    ),
    (
        ("/tmp/memory", "/tmp/memory/bob/MEMORY.md", "hot", True, 200, 25600),
        "Canonical persona memory root: /tmp/memory\n"
        "Hot index snapshot: /tmp/memory/bob/MEMORY.md\n"
        "Use only this persona directory for durable judgment. Fetch current state fresh and read "
        "linked topic files only when needed.\n"
        "Write memory only through this absolute root; a relative memory/ path in a worktree is "
        "lost with the tree.\n\nhot\n"
        "[memory truncated: loaded at most 200 lines or 25600 bytes from "
        "/tmp/memory/bob/MEMORY.md; read the file directly for the rest]",
    ),
]

WITH_MEMORY_FRAMES = [
    ("memory", "wake", "memory\n\nwake"),
    ("", "wake", "wake"),
]

# ((provider, model, level, session_id), expected) for prompts.wake_footer; spelled out rather
# than formatted from WAKE_FOOTER, so the table pins the rendered shape and not the template.
WAKE_FOOTERS = [
    (("claude", "opus", "high", "sid-123"),
     "\n\n```\nclaude | opus | high | session sid-123\n```"),
    (("opencode", "fireworks-ai/accounts/fireworks/models/deepseek-v4-pro", "mid", "sid-9"),
     "\n\n```\nopencode | deepseek-v4-pro | mid | session sid-9\n```"),
    (("claude", None, None, "sid-1"),
     "\n\n```\nclaude | - | - | session sid-1\n```"),
    (("claude", "opus", "xtra", ""),
     "\n\n```\nclaude | opus | xtra | session -\n```"),
    ((None, None, None, None),
     "\n\n```\n- | - | - | session -\n```"),
]

# (body, footer, expected) for send.with_footer: the footer lands last, one blank line down,
# after whatever closer the body still owes.
FOOTER_BODIES = [
    ("all done", "\n\nFOOT", "all done\n\nFOOT"),
    ("all done\n\n\n", "\n\nFOOT", "all done\n\nFOOT"),
    ("all done", "", "all done"),
    ("intro\n```\ncode", "\n\nFOOT", "intro\n```\ncode\n```\n\nFOOT"),
    ("intro\n~~~\ncode", "\n\nFOOT", "intro\n~~~\ncode\n~~~\n\nFOOT"),
    ("intro\n```\ncode\n```", "\n\nFOOT", "intro\n```\ncode\n```\n\nFOOT"),
    ("````\n```\ninner\n```\n````", "\n\nFOOT", "````\n```\ninner\n```\n````\n\nFOOT"),
]

# (text, expected closer) for prompts.open_fence. Naive backtick counting is wrong in both
# directions: the tilde rows are the false negative, the nested-fence row the false positive.
OPEN_FENCES = [
    ("plain body, no fence", ""),
    ("", ""),
    (None, ""),
    ("intro\n```\ncode\n```\noutro", ""),
    ("intro\n```\ncode", "```"),
    ("```python\ncode\n```", ""),
    ("```python\ncode", "```"),
    ("~~~\ncode\n~~~", ""),
    ("~~~\ncode", "~~~"),
    ("````\n```\ninner\n```\n````", ""),
    ("````\n```\ninner\n```", "````"),
    ("a line with `inline` code", ""),
    ("```one-liner``` and prose", ""),
]

# (raw env value or None, default, cast, expect SystemExit, expected result) for constants._num
NUM_PARSES = [
    (None, 30, int, False, 30),
    ("45", 30, int, False, 45),
    ("", 30, int, False, 30),
    ("   ", 30, int, False, 30),
    ("not-a-number", 30, int, True, None),
]
