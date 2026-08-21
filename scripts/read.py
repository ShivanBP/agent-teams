"""Read a topic back as a record: what was said, by whom, in raw markdown."""

import argparse
import datetime
import sys
from pathlib import Path

import api
import constants
import prompts


def _window(limit, newer):
    params = {"num_before": 0 if newer else limit, "num_after": limit if newer else 0}
    if newer:
        params["include_anchor"] = False
    return params


def _narrow(channel_id=None, topic=None, search=None):
    narrow = [{"operator": "channel", "operand": channel_id}] if channel_id is not None else [
        {"operator": "channels", "operand": "public"}
    ]
    if topic:
        narrow.append({"operator": "topic", "operand": topic})
    if search:
        narrow.append({"operator": "search", "operand": search})
    return narrow


def fetch(as_name, channel=None, topic=None, search=None, limit=constants.READ_LIMIT,
          anchor="newest", newer=False):
    """Self-guarding: enforces identity itself, matching send.py's post/react."""
    api.enforce_identity(as_name)
    cfg = api.load(as_name)
    sid = api.stream_id(as_name, channel) if channel else None
    if channel and sid is None:
        return None, "narrow_miss"
    params = dict({
        "anchor": anchor,
        "narrow": _narrow(sid, topic, search),
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


def render(messages, site=None, ids=False):
    """site set (--search only) appends a permalink per hit, so a search result can be cited
    without hand-building the #narrow URL."""
    lines = [prompts.RECORD_HEADER]
    for msg in messages:
        stamp = datetime.datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")
        line = prompts.RECORD_LINE.format(
            stamp=stamp, sender=msg.get("sender_full_name", "?"),
            body=indent(msg.get("content", "")))
        if ids:
            line += prompts.MESSAGE_ID_SUFFIX.format(message_id=msg["id"])
        if site:
            line += "\n    " + api.permalink(
                site, msg.get("stream_id"), msg.get("display_recipient", ""),
                msg.get("subject", ""), msg["id"],
            )
        lines.append(line)
    return "\n".join(lines)


def render_cross_channel(messages, site, ids=False):
    lines = [prompts.RECORD_HEADER]
    for msg in sorted(messages, key=lambda row: row["timestamp"], reverse=True):
        stamp = datetime.datetime.fromtimestamp(msg["timestamp"]).strftime("%Y-%m-%d %H:%M")
        body = " ".join(str(msg.get("content", "")).split())[:constants.CROSS_CHANNEL_BODY_CHARS]
        lines.append(prompts.CROSS_CHANNEL_RECORD_LINE.format(
            stamp=stamp,
            channel=msg.get("display_recipient", "?"),
            topic=msg.get("subject", "?"),
            sender=msg.get("sender_full_name", "?"),
            body=body,
            url=api.permalink(
                site, msg.get("stream_id"), msg.get("display_recipient", ""),
                msg.get("subject", ""), msg["id"],
            ),
            message_id=prompts.MESSAGE_ID_SUFFIX.format(message_id=msg["id"]) if ids else "",
        ))
    return "\n".join(lines)


def render_channels(streams):
    return "\n".join(prompts.CHANNEL_LIST_LINE.format(
        name=stream["name"], description=stream.get("description", "")) for stream in streams)


def render_topics(channel, topics):
    return "\n".join(prompts.TOPIC_LIST_LINE.format(
        channel=channel, topic=topic["name"]) for topic in topics)


def topic_line(messages, site):
    """One durable link for the whole topic. Zulip's with/ operator follows a rename or a move;
    near/ pins the one message it names, which a topic read is not about."""
    if not messages:
        return None
    last = messages[-1]
    return prompts.TOPIC_PERMALINK.format(url=api.permalink(
        site, last.get("stream_id"), last.get("display_recipient", ""),
        last.get("subject", ""), last["id"], operator="with"))


def render_attachment(ctype, data, link, out=None):
    """What it is, always; the text itself when it is text. Anything else stays bytes and only
    reaches a caller that named --out, so an image never becomes megabytes of stdout."""
    lines = [prompts.ATTACHMENT_HEAD.format(ctype=ctype, size=len(data), url=link)]
    if out:
        Path(out).write_bytes(data)
        lines.append(prompts.ATTACHMENT_SAVED.format(path=out))
    if ctype.split(";")[0].strip().startswith("text/"):
        lines.append("")
        lines.append(data.decode("utf-8", errors="replace"))
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
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--ids", action="store_true")
    ap.add_argument("--limit", type=int, default=constants.READ_LIMIT)
    ap.add_argument("--anchor", default="newest")
    ap.add_argument("--attachment", metavar="URL",
                    help="Fetch one Zulip upload by its link and print what it is; text is "
                         "printed whole. Needs --as and nothing else.")
    ap.add_argument("--out", metavar="PATH",
                    help="With --attachment, write the fetched bytes here.")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    if args.attachment:
        if not args.as_name:
            ap.error("--as is required")
        api.enforce_identity(args.as_name)
        got, err = api.attachment(args.as_name, args.attachment)
        if err == "not_an_upload":
            sys.stderr.write(prompts.ATTACHMENT_MISS.format(url=args.attachment) + "\n")
            raise SystemExit(1)
        if err is not None:
            sys.stderr.write(str(err) + "\n")
            raise SystemExit(1)
        print(render_attachment(*got, out=args.out))
        return
    if not args.as_name:
        ap.error("--as is required")
    if args.list:
        api.enforce_identity(args.as_name)
        if args.channel:
            stream_id = api.stream_id(args.as_name, args.channel)
            if stream_id is None:
                api.refuse_narrow_miss(args.as_name, args.channel)
            print(render_topics(args.channel, api.topics(args.as_name, stream_id)))
        else:
            print(render_channels(api.visible_streams(args.as_name, details=True)))
        return
    messages, err = fetch(
        args.as_name, args.channel, args.topic, args.search, args.limit, _anchor(args.anchor)
    )
    if err == "narrow_miss":
        api.refuse_narrow_miss(args.as_name, args.channel)
    if err is not None:
        sys.stderr.write(str(err) + "\n")
        raise SystemExit(1)
    site = api.load(args.as_name)["site"] if args.search or args.topic or not args.channel else None
    if args.channel:
        print(render(messages, site if args.search else None, ids=args.ids))
    else:
        print(render_cross_channel(messages, site, ids=args.ids))
    if args.topic:
        line = topic_line(messages, site)
        if line:
            print(line)


if __name__ == "__main__":
    main()
