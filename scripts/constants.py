"""Tunables and paths. Values built with _num() or os.environ.get() are overridable by an env
var of the same name; RESOLVED_PREFIX and the ATTACH_* values below are fixed, not wrapped."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path


log = logging.getLogger("agent-team.constants")
REPO_DIR = Path(__file__).resolve().parent.parent
MATRIX_PATH = REPO_DIR / "config" / "persona-matrix.json"
MATRIX_EXAMPLE_PATH = REPO_DIR / "config" / "persona-matrix.example.json"
HARNESS_DEFAULTS_PATH = REPO_DIR / "config" / "harness-defaults.json"
HARNESS_DEFAULTS_EXAMPLE_PATH = REPO_DIR / "config" / "harness-defaults.example.json"
MODEL_EFFORT_DEFAULTS_PATH = REPO_DIR / "config" / "model-effort-defaults.json"
MODEL_EFFORT_DEFAULTS_EXAMPLE_PATH = REPO_DIR / "config" / "model-effort-defaults.example.json"
RAILS_PATH = REPO_DIR / "config" / "rails.json"
RAILS_EXAMPLE_PATH = REPO_DIR / "config" / "rails.example.json"
CHANNELS_PATH = REPO_DIR / "config" / "channels.json"
CHANNELS_EXAMPLE_PATH = REPO_DIR / "config" / "channels.example.json"


def _load_json_object(path, example_path, label):
    if not path.is_file():
        log.warning("%s missing at %s; using %s", label, path, example_path)
        path = example_path
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError("root is not an object")
        return data
    except (OSError, ValueError) as exc:
        if path == example_path:
            raise RuntimeError("cannot load %s example: %s" % (label, exc))
        log.warning("%s malformed at %s; using %s", label, path, example_path)
        try:
            data = json.loads(example_path.read_text())
        except (OSError, ValueError) as fallback_exc:
            raise RuntimeError("cannot load %s example: %s" % (label, fallback_exc))
        if not isinstance(data, dict):
            raise RuntimeError("%s example root is not an object" % label)
        return data


def _load_matrix():
    return _load_json_object(MATRIX_PATH, MATRIX_EXAMPLE_PATH, "persona matrix")


def _load_harness_defaults():
    return _load_json_object(
        HARNESS_DEFAULTS_PATH, HARNESS_DEFAULTS_EXAMPLE_PATH, "harness defaults")


def _load_model_effort_defaults():
    return _load_json_object(
        MODEL_EFFORT_DEFAULTS_PATH, MODEL_EFFORT_DEFAULTS_EXAMPLE_PATH,
        "model effort defaults")


def _load_rails():
    return _load_json_object(RAILS_PATH, RAILS_EXAMPLE_PATH, "rails")


def _load_channels():
    return _load_json_object(CHANNELS_PATH, CHANNELS_EXAMPLE_PATH, "channels")


_MATRIX = _load_matrix()
HARNESS_DEFAULTS = _load_harness_defaults()
MODEL_EFFORT_DEFAULTS = _load_model_effort_defaults()
_RAILS = _load_rails()
_CHANNELS = _load_channels()

_EFFORT_SCALE = {
    "low": {"claude": "low", "codex": "low", "opencode": "low", "agy": "low"},
    "mid": {"claude": "medium", "codex": "medium", "opencode": "medium", "agy": "medium"},
    "high": {"claude": "high", "codex": "high", "opencode": "high", "agy": "high"},
    "xtra": {"claude": "xhigh", "codex": "xhigh", "opencode": "xhigh"},
}

# The fleet's vocabulary, one home each: the listener's flag words derive from these three.
EFFORT_LEVELS = tuple(_EFFORT_SCALE)
PROVIDERS = ("claude",) + tuple(HARNESS_DEFAULTS)  # claude is native, the rest are harnesses
CLAUDE_MODELS = ("opus", "fable", "sonnet")


def translate_effort(provider, level):
    try:
        return _EFFORT_SCALE[level][provider]
    except KeyError:
        raise RuntimeError("unsupported effort level %r for provider %r" % (level, provider))


def persona_matrix():
    """The matrix as loaded, in file order; personas.py takes the fleet roster from its keys."""
    return dict(_MATRIX)


def matrix_defaults(persona):
    try:
        return dict(_MATRIX[persona])
    except KeyError:
        raise RuntimeError("persona %r is absent from the matrix" % persona)


def rail_defaults(rail):
    """The operator rails' settings. Their own file, not the matrix: matrix keys are the fleet
    roster, and a rail is not a persona."""
    try:
        return dict(_RAILS[rail])
    except KeyError:
        raise RuntimeError("rail %r is absent from the rails config" % rail)


def _num(name, default, cast):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except ValueError:
        raise SystemExit("bad value for %s: %r" % (name, raw))


def _mate_emails(plural, singular):
    raw = singular if plural is None else plural
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


# Secrets and runtime state live under ~/.config/agent-team only, never in the repo.
# Prefixed env vars only: bare CONFIG_DIR/STATE_DIR/LOGS_DIR collide with generic shell vars.
CONFIG_DIR = Path(os.environ.get("AGENT_TEAM_CONFIG_DIR", Path.home() / ".config" / "agent-team"))
STATE_DIR = Path(os.environ.get("AGENT_TEAM_STATE_DIR", CONFIG_DIR / "state"))
LOGS_DIR = Path(os.environ.get("AGENT_TEAM_LOGS_DIR", CONFIG_DIR / "logs"))
MEMORY_DIR = Path(os.environ.get("AGENT_TEAM_MEMORY_DIR", REPO_DIR / "memory"))
MEMORY_MAX_LINES = 200
MEMORY_MAX_BYTES = 25 * 1024


def _load_dotenv():
    """~/.config/agent-team/.env: simple KEY=VALUE lines, no secrets beyond the resolved email.
    Only fills keys not already set in the real environment (explicit env wins)."""
    path = CONFIG_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


_load_dotenv()

STALL_MIN = _num("STALL_MIN", 10, int)
LSOF_BIN = os.environ.get("LSOF_BIN", "/usr/sbin/lsof")
BOARD_TOPIC_DAYS = _num("BOARD_TOPIC_DAYS", 7, int)
DIGEST_SWEEP_MIN = _num("DIGEST_SWEEP_MIN", 360, int)
DIGEST_MODEL = "sonnet"
DIGEST_MAX_CHARS = _num("DIGEST_MAX_CHARS", 30000, int)
DIGEST_FETCH_LIMIT = _num("DIGEST_FETCH_LIMIT", 1000, int)
DIGEST_SUMMARY_MAX = _num("DIGEST_SUMMARY_MAX", 120, int)
DIGEST_ITEM_MAX = _num("DIGEST_ITEM_MAX", 100, int)
DIGEST_OPEN_MAX = _num("DIGEST_OPEN_MAX", 5, int)
DIGEST_DONE_MAX = _num("DIGEST_DONE_MAX", 2, int)
DIGEST_FACT_TIMEOUT = _num("DIGEST_FACT_TIMEOUT", 5, int)
BOARD_IDLE_HOURS = _num("BOARD_IDLE_HOURS", 48, int)
TAG_MAX_AGE_MIN = _num("TAG_MAX_AGE_MIN", 30, int)
RECORD_WINDOW = _num("RECORD_WINDOW", 10, int)
LOOP_BUDGET_DEFAULT = _num("LOOP_BUDGET_DEFAULT", 5, int)
AGENT_TEAM_MATE_EMAIL = os.environ.get("AGENT_TEAM_MATE_EMAIL", "")
AGENT_TEAM_MATE_EMAILS = _mate_emails(
    os.environ.get("AGENT_TEAM_MATE_EMAILS"), AGENT_TEAM_MATE_EMAIL)

BRIDGE_IDENTITY = os.environ.get("BRIDGE_IDENTITY", "bridge")
LAUNCHD_LABEL = os.environ.get("AGENT_TEAM_LAUNCHD_LABEL", "com.agent-team")
EMOJI_RECEIPT = os.environ.get("EMOJI_RECEIPT", "eyes")
ALERTS_TOPIC = os.environ.get("ALERTS_TOPIC", "alerts")
BOARD_TOPIC = os.environ.get("BOARD_TOPIC", "board")
STATUS_STREAM = os.environ.get("STATUS_STREAM", "status")
RUN_TIMEOUT = _num("RUN_TIMEOUT", 1800, int)
GIT_LOCK_WAIT = _num("GIT_LOCK_WAIT", 120, int)
GIT_CMD_TIMEOUT = _num("GIT_CMD_TIMEOUT", 120, int)
IDLE_QUEUE_TIMEOUT = _num("IDLE_QUEUE_TIMEOUT", 604800, int)  # seconds Zulip keeps an idle event queue
REPLY_TRUNCATION_LIMIT = _num("REPLY_TRUNCATION_LIMIT", 4000, int)
READ_LIMIT = _num("READ_LIMIT", 30, int)
CODEX_BIN = os.environ.get("CODEX_BIN", "/Applications/ChatGPT.app/Contents/Resources/codex")
CODEX_MODEL = os.environ.get("CODEX_MODEL", HARNESS_DEFAULTS["codex"]["model"])
CODEX_EFFORT = os.environ.get("CODEX_EFFORT", HARNESS_DEFAULTS["codex"]["effort"])
AGY_BIN = os.environ.get("AGY_BIN", str(Path.home() / ".local" / "bin" / "agy"))
AGY_MODEL = os.environ.get("AGY_MODEL", HARNESS_DEFAULTS["agy"]["model"])
AGY_EFFORT = os.environ.get("AGY_EFFORT", HARNESS_DEFAULTS["agy"]["effort"])
OPENCODE_BIN = os.environ.get(
    "OPENCODE_BIN", str(Path.home() / ".opencode" / "bin" / "opencode"))
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", HARNESS_DEFAULTS["opencode"]["model"])
OPENCODE_VARIANT = os.environ.get("OPENCODE_VARIANT", HARNESS_DEFAULTS["opencode"]["effort"])

LAST_ACTION_LABELS = {
    "claude:text": "writing reply",
    "claude:tool_use": "using tool",
    "codex:agent_message": "writing reply",
    "codex:command_execution": "running command",
    "codex:file_change": "editing files",
    "codex:web_search": "searching web",
    "agy:step_update": "writing reply",
    "opencode:text": "writing reply",
    "opencode:tool": "using tool",
}


def board_groups(channels=None):
    """The board's channel groups, in config order."""
    rows = _CHANNELS if channels is None else channels
    return tuple((group, tuple(names)) for group, names in rows.items())


BOARD_GROUPS = board_groups()


def board_channels(groups=None):
    return {channel for _, channels in (BOARD_GROUPS if groups is None else groups)
            for channel in channels}


BOARD_STATE_KEYS = {
    "activity": "board",
    "workshop": "board-workshop",
    "domains": "board-domains",
}
PARKED_STATE = "parked"

RESOLVED_PREFIX = "✔"  # Zulip's own resolve marker on a topic name; startswith semantics only.

ATTACH_MAX_BYTES = 10 * 1024 * 1024
ATTACH_MAX_FILES = 4
ATTACH_ROOTS = (str(Path.home() / "Projects"), str(LOGS_DIR))

# One build worktree per topic. Kept under ~/Projects so ATTACH_ROOTS still admits a path a
# build wake attaches; builders always get one, verifiers only join one that already exists.
WORKTREE_ROOT = Path.home() / "Projects" / "agent-team-worktrees"


def worktree_roles(role, matrix=None):
    """Personas the matrix gives that worktree role, in matrix order."""
    rows = _MATRIX if matrix is None else matrix
    return tuple(name for name, row in rows.items()
                 if isinstance(row, dict) and row.get("worktree") == role)


WORKTREE_PERSONAS = worktree_roles("build")
WORKTREE_JOIN = worktree_roles("join")

_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _selftest():
    from tests import constants_selftest
    return constants_selftest.run(sys.modules[__name__])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; constants.py is a library")
