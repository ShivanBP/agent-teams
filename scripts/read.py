"""Read a topic back as a record: what was said, by whom, in raw markdown."""

import argparse
import datetime
import sys

import api
import constants
import prompts


def _window(limit, newer):
    params = {"num_before": 0 if newer else limit, "num_after": limit if newer else 0}
    if newer:
        params["include_anchor"] = False
    return params


def fetch(as_name, channel, topic=None, search=None, limit=constants.READ_LIMIT, anchor="newest",
          newer=False):
    """Self-guarding: enforces identity itself, matching send.py's post/react."""
    api.enforce_identity(as_name)
    cfg = api.load(as_name)
    sid = api.stream_id(as_name, channel)
    if sid is None:
        return None, "narrow_miss"
    narrow = [{"operator": "channel", "operand": sid}]
    if topic:
        narrow.append({"operator": "topic", "operand": topic})
    if search:
        narrow.append({"operator": "search", "operand": search})
    params = dict({
        "anchor": anchor,
        "narrow": narrow,
        "apply_markdown": False,
    }, **_window(limit, newer))
    payload = api.request(
        cfg,
        "GET",
        "/api/v1/messages",
        params,
    )
    if payload.get("result") != "success":
        return None, payload.get("msg", payload)
    return payload.get("messages", []), None


def indent(content):
    return str(content).replace("\n", "\n    ")


def render(messages, site=None):
    """site set (--search only) appends a permalink per hit, so a search result can be cited
    without hand-building the #narrow URL."""
    lines = [prompts.RECORD_HEADER]
    for msg in messages:
        stamp = datetime.datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")
        line = prompts.RECORD_LINE.format(
            stamp=stamp, sender=msg.get("sender_full_name", "?"),
            body=indent(msg.get("content", "")))
        if site:
            line += "\n    " + api.permalink(
                site, msg.get("stream_id"), msg.get("display_recipient", ""),
                msg.get("subject", ""), msg["id"],
            )
        lines.append(line)
    return "\n".join(lines)


def _selftest():
    from tests import read_selftest
    return read_selftest.run(sys.modules[__name__])


def _anchor(value):
    return int(value) if str(value).isdigit() else str(value)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--as", dest="as_name")
    ap.add_argument("--channel")
    ap.add_argument("--topic")
    ap.add_argument("--search")
    ap.add_argument("--limit", type=int, default=constants.READ_LIMIT)
    ap.add_argument("--anchor", default="newest")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if not args.as_name or not args.channel:
        ap.error("--as and --channel are required")
    messages, err = fetch(
        args.as_name, args.channel, args.topic, args.search, args.limit, _anchor(args.anchor)
    )
    if err == "narrow_miss":
        api.refuse_narrow_miss(args.as_name, args.channel)
    if err is not None:
        sys.stderr.write(str(err) + "\n")
        raise SystemExit(1)
    site = api.load(args.as_name)["site"] if args.search else None
    print(render(messages, site))


if __name__ == "__main__":
    main()
