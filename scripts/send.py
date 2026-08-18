"""The one POST /api/v1/messages in the codebase; every module posts through here."""

import argparse
import re
import sys
from pathlib import Path

import api
import constants
import personas as personas_mod
import prompts

ATTACH_LINE = re.compile(r"^[ \t]*\[attach:[ \t]*(.+?)[ \t]*\][ \t]*$")

ZWSP = "\u200b"
_WILDCARD = re.compile(r"@(_?)\*\*(all|everyone|channel|topic|stream)\*\*")
_PERSONA_MENTION = re.compile(
    r"@(\*\*(?:%s)(?:\|\d+)?\*\*)" % "|".join(map(re.escape, personas_mod.PERSONAS)),
    re.IGNORECASE,
)


def strip_wildcards(text):
    """Wildcard mentions are neutralized here; ping hygiene lives nowhere else, send.py is the choke point."""
    return _WILDCARD.sub(lambda m: ZWSP.join(("@", "%s**%s**" % (m.group(1), m.group(2)))), text or "")


def strip_persona_mentions(text, as_name):
    """Persona posts cannot wake other personas; bridge-issued kicks retain real mentions."""
    if as_name not in personas_mod.PERSONAS:
        return text
    return _PERSONA_MENTION.sub(lambda m: ZWSP.join(("@", m.group(1))), text or "")


def classify_attach(path, index, exists, is_dir, size, is_symlink=False):
    """Pure classifier: the refusal reason for one attach candidate, or None if it may be uploaded."""
    if index >= constants.ATTACH_MAX_FILES:
        return "too_many"
    text = str(path)
    if not text.startswith("/") or ".." in Path(text).parts:
        return "outside_roots"
    if not any(text == root or text.startswith(root.rstrip("/") + "/") for root in constants.ATTACH_ROOTS):
        return "outside_roots"
    # A symlink, or a resolved real path escaping the allowed roots, is refused before any read.
    if is_symlink:
        return "symlink"
    resolved = str(Path(text).resolve())
    if not any(resolved == root or resolved.startswith(root.rstrip("/") + "/") for root in constants.ATTACH_ROOTS):
        return "outside_roots"
    if Path(text).name.startswith("."):
        return "dot_name"
    if not exists:
        return "missing"
    if is_dir:
        return "directory"
    if size > constants.ATTACH_MAX_BYTES:
        return "too_large"
    return None


def _inspect(path):
    p = Path(path)
    is_link = p.is_symlink()
    try:
        st = p.stat()
        return True, p.is_dir(), st.st_size, is_link
    except OSError:
        return False, False, 0, is_link


def _extract(body, extra_files):
    """Refusals replace the attach line in the body and posting continues."""
    kept, accepted, index = [], [], 0
    for line in body.splitlines():
        m = ATTACH_LINE.match(line)
        if not m:
            kept.append(line)
            continue
        path = m.group(1)
        exists, is_dir, size, is_symlink = _inspect(path)
        reason = classify_attach(path, index, exists, is_dir, size, is_symlink)
        if reason:
            kept.append(prompts.ATTACH_REFUSALS[reason].format(path=path))
        else:
            accepted.append(path)
            index += 1
    out = "\n".join(kept)
    for path in extra_files or []:
        exists, is_dir, size, is_symlink = _inspect(path)
        reason = classify_attach(path, index, exists, is_dir, size, is_symlink)
        if reason:
            out += "\n" + prompts.ATTACH_REFUSALS[reason].format(path=path)
        else:
            accepted.append(path)
            index += 1
    return out, accepted


def _upload(as_name, path):
    cfg = api.load(as_name)
    data = Path(path).read_bytes()
    payload = api.check(
        api.request(cfg, "POST", "/api/v1/user_uploads", upload=(Path(path).name, data)),
        "POST /user_uploads",
    )
    url = payload.get("url") or payload.get("uri")
    if url and url.startswith("/"):
        url = cfg["site"] + url
    return url


def _ready(as_name):
    """enforce_identity then load, the pair every posting/verb function needs before its first
    API call; post, resolve_topic, move_topic, and react shared this shape before extraction."""
    api.enforce_identity(as_name)
    return api.load(as_name)


def with_footer(body, footer):
    """The footer goes last, below any upload links, and closes a fence the body left open: an
    unterminated fence swallows everything after it, the footer included."""
    if not footer:
        return body
    closer = prompts.open_fence(body)
    return (body or "").rstrip("\n") + ("\n" + closer if closer else "") + footer


def post(as_name, stream, topic, body, files=(), footer=""):
    cfg = _ready(as_name)
    body, accepted = _extract(body or "", files)
    links = []
    for path in accepted:
        links.append("[%s](%s)" % (Path(path).name, _upload(as_name, path)))
    if links:
        body = body.rstrip("\n") + "\n\n" + "\n".join(links)
    body = with_footer(body, footer)
    body = strip_persona_mentions(body, as_name)
    body = strip_wildcards(body)
    limit = api.window(as_name)
    if len(body) > limit:
        # An over-window body is refused, never truncated; the caller attaches a file instead.
        sys.stderr.write(prompts.OVER_WINDOW_NOTE.format(size=len(body), window=limit) + "\n")
        raise SystemExit(3)
    sid = api.stream_id(as_name, stream)
    if sid is None:
        api.refuse_narrow_miss(as_name, stream)
    payload = api.check(
        api.request(
            cfg,
            "POST",
            "/api/v1/messages",
            {"type": "stream", "to": sid, "topic": topic, "content": body},
        ),
        "POST /messages",
    )
    return payload["id"]


def update(as_name, message_id, content):
    cfg = _ready(as_name)
    content = strip_persona_mentions(content or "", as_name)
    content = strip_wildcards(content)
    limit = api.window(as_name)
    if len(content) > limit:
        sys.stderr.write(prompts.OVER_WINDOW_NOTE.format(size=len(content), window=limit) + "\n")
        raise SystemExit(3)
    api.check(
        api.request(
            cfg,
            "PATCH",
            "/api/v1/messages/%d" % int(message_id),
            {"content": content},
        ),
        "PATCH /messages (content)",
    )
    return int(message_id)


def verb_allowed(as_name):
    """--resolve and --move-to are bridge-only (the topic-verbs ledger grant); every other
    identity is refused before any API call, regardless of AGENT_TEAM_IDENTITY."""
    return as_name == "bridge"


def _topic_anchor_message(as_name, channel, topic):
    """Any message id in this channel+topic anchors the update-message call; propagate_mode
    change_all applies the edit to the whole topic regardless of which message anchors it."""
    sid = api.stream_id(as_name, channel)
    if sid is None:
        return None
    narrow = [{"operator": "channel", "operand": sid}, {"operator": "topic", "operand": topic}]
    payload = api.request(api.load(as_name), "GET", "/api/v1/messages", {
        "anchor": "newest", "num_before": 1, "num_after": 0,
        "narrow": narrow, "apply_markdown": False,
    })
    if payload.get("result") != "success":
        return None
    messages = payload.get("messages", [])
    return messages[-1]["id"] if messages else None


def _topic_anchor_or_refuse(as_name, channel, topic):
    """Returns an anchoring message id for channel+topic, or refuses (TOPIC_ANCHOR_MISS, exit 4)
    if the topic has no messages; resolve_topic and move_topic shared this shape before extraction."""
    message_id = _topic_anchor_message(as_name, channel, topic)
    if message_id is None:
        sys.stderr.write(prompts.TOPIC_ANCHOR_MISS.format(channel=channel, topic=topic) + "\n")
        raise SystemExit(4)
    return message_id


def resolve_topic(as_name, channel, topic):
    """Rename to the ✔ prefix, propagate_mode change_all: probed live 2026-08-12 against a
    scratch topic, this is native resolve, notification-bot marker included, not a lookalike."""
    if not verb_allowed(as_name):
        sys.stderr.write(prompts.VERB_BRIDGE_ONLY.format(verb="--resolve", asked=as_name) + "\n")
        raise SystemExit(2)
    cfg = _ready(as_name)
    message_id = _topic_anchor_or_refuse(as_name, channel, topic)
    new_topic = topic if topic.strip().startswith(constants.RESOLVED_PREFIX) else (constants.RESOLVED_PREFIX + " " + topic)
    api.check(
        api.request(cfg, "PATCH", "/api/v1/messages/%d" % message_id,
                    {"topic": new_topic, "propagate_mode": "change_all"}),
        "PATCH /messages (resolve)",
    )
    return message_id


def move_topic(as_name, channel, topic, target_channel):
    """Cross-channel move: stream_id plus propagate_mode change_all, probed live 2026-08-12."""
    if not verb_allowed(as_name):
        sys.stderr.write(prompts.VERB_BRIDGE_ONLY.format(verb="--move-to", asked=as_name) + "\n")
        raise SystemExit(2)
    cfg = _ready(as_name)
    target_sid = api.stream_id(as_name, target_channel)
    if target_sid is None:
        api.refuse_narrow_miss(as_name, target_channel)
    message_id = _topic_anchor_or_refuse(as_name, channel, topic)
    api.check(
        api.request(cfg, "PATCH", "/api/v1/messages/%d" % message_id,
                    {"stream_id": target_sid, "propagate_mode": "change_all"}),
        "PATCH /messages (move)",
    )
    return message_id


def react(as_name, message_id, emoji_name):
    cfg = _ready(as_name)
    api.check(
        api.request(
            cfg,
            "POST",
            "/api/v1/messages/%d/reactions" % int(message_id),
            {"emoji_name": emoji_name},
        ),
        "POST /reactions",
    )


def _selftest():
    import tests.cases as cases

    passed = failed = 0
    for text, expected in cases.WILDCARDS:
        got = strip_wildcards(text)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL strip_wildcards(%r) -> %r, wanted %r" % (text, got, expected))
    for as_name, text, expected in cases.PERSONA_MENTIONS:
        got = strip_persona_mentions(text, as_name)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL strip_persona_mentions(%r, %r) -> %r, wanted %r" %
                  (text, as_name, got, expected))
    for path, index, exists, is_dir, size, is_symlink, expected in cases.ATTACHES:
        got = classify_attach(path, index, exists, is_dir, size, is_symlink)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL classify_attach(%r, %d) -> %r, wanted %r" % (path, index, got, expected))
    for size, limit, should_refuse in cases.WINDOW:
        refused = size > limit
        if refused == should_refuse:
            passed += 1
        else:
            failed += 1
            print("FAIL window boundary size=%d limit=%d refused=%s" % (size, limit, refused))
    for body, expect_body, expect_accepted in cases.EXTRACTS:
        got_body, got_accepted = _extract(body, [])
        if got_body == expect_body and got_accepted == expect_accepted:
            passed += 1
        else:
            failed += 1
            print("FAIL _extract(%r) -> (%r, %r)" % (body, got_body, got_accepted))
    for body, footer, expected in cases.FOOTER_BODIES:
        got = with_footer(body, footer)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL with_footer(%r, %r) -> %r, wanted %r" % (body, footer, got, expected))
    for as_name, expected in cases.VERB_GATE:
        got = verb_allowed(as_name)
        if got == expected:
            passed += 1
        else:
            failed += 1
            print("FAIL verb_allowed(%r) -> %r, wanted %r" % (as_name, got, expected))
    old_ready, old_window, old_request, old_check = _ready, api.window, api.request, api.check
    calls = []
    try:
        globals()["_ready"] = lambda as_name: {"name": as_name}
        api.window = lambda as_name: 10000
        api.request = lambda cfg, method, path, params: calls.append(
            (cfg, method, path, params)) or {"result": "success"}
        api.check = lambda payload, what: payload
        for as_name, message_id, content, expected in cases.UPDATES:
            got = update(as_name, message_id, content)
            call = calls.pop(0)
            wanted = ({"name": as_name}, "PATCH", "/api/v1/messages/%d" % message_id,
                      {"content": expected})
            if got == message_id and call == wanted:
                passed += 1
            else:
                failed += 1
                print("FAIL update(%r, %r, %r) -> %r call %r, wanted %r" %
                      (as_name, message_id, content, got, call, wanted))
    finally:
        globals()["_ready"] = old_ready
        api.window, api.request, api.check = old_window, old_request, old_check
    print("send.py selftest: %d PASS, %d FAIL" % (passed, failed))
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--as", dest="as_name")
    ap.add_argument("--channel")
    ap.add_argument("--topic")
    ap.add_argument("--body")
    ap.add_argument("--body-file")
    ap.add_argument("--react-to", type=int)
    ap.add_argument("--emoji")
    ap.add_argument("--attach", action="append", default=[])
    ap.add_argument("--resolve", action="store_true",
                     help="Bridge-only. Marks --channel/--topic resolved (native ✔ rename, "
                          "notification-bot marker included). Moving a live topic resets its "
                          "lane; #status topics are never resolved or moved.")
    ap.add_argument("--move-to", dest="move_to", metavar="CHANNEL",
                     help="Bridge-only. Moves --channel/--topic to CHANNEL. Moving a live topic "
                          "resets its lane; #status topics are never resolved or moved.")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if not args.as_name:
        ap.error("--as is required")
    if args.resolve or args.move_to:
        if args.resolve and args.move_to:
            ap.error("give exactly one of --resolve or --move-to")
        if not args.channel or not args.topic:
            ap.error("--channel and --topic are required")
        if args.resolve:
            print(resolve_topic(args.as_name, args.channel, args.topic))
        else:
            print(move_topic(args.as_name, args.channel, args.topic, args.move_to))
        return
    if args.react_to:
        if not args.emoji:
            ap.error("--react-to needs --emoji")
        react(args.as_name, args.react_to, args.emoji)
        print(args.react_to)
        return
    if not args.channel or not args.topic:
        ap.error("--channel and --topic are required")
    if bool(args.body) == bool(args.body_file):
        ap.error("give exactly one of --body or --body-file")
    body = args.body if args.body else Path(args.body_file).read_text()
    mid = post(args.as_name, args.channel, args.topic, body, args.attach)
    print(mid)
    sid = api.stream_id(args.as_name, args.channel)
    if sid is not None:
        print(api.permalink(api.load(args.as_name)["site"], sid, args.channel, args.topic, mid))


if __name__ == "__main__":
    main()
