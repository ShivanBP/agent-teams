"""Zulip transport: identity, HTTP, and the facts the server owns. Python 3 stdlib only."""

import argparse
import base64
import configparser
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import constants
import prompts

_CONF = {}
_ME = {}
_WINDOW = {}


def load(name):
    """Identity is whichever zuliprc loaded; secrets come from ~/.config/agent-team only, never the repo."""
    if name in _CONF:
        return _CONF[name]
    path = constants.CONFIG_DIR / ("%s.zuliprc" % name)
    if not path.is_file():
        sys.stderr.write("no zuliprc for identity %r at %s\n" % (name, path))
        raise SystemExit(2)
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path)
    if not parser.has_section("api"):
        sys.stderr.write("%s has no [api] section\n" % path)
        raise SystemExit(2)
    cfg = {}
    for key in ("email", "key", "site"):
        if not parser.has_option("api", key):
            sys.stderr.write("%s is missing %s\n" % (path, key))
            raise SystemExit(2)
        cfg[key] = parser.get("api", key).strip()
    cfg["site"] = cfg["site"].rstrip("/")
    cfg["name"] = name
    _CONF[name] = cfg
    return cfg


def enforce_identity(as_name):
    """Inside a wake, --as must equal AGENT_TEAM_IDENTITY or exit 2."""
    actual = os.environ.get("AGENT_TEAM_IDENTITY")
    if actual and actual != as_name:
        sys.stderr.write(prompts.IDENTITY_MISMATCH.format(asked=as_name, actual=actual) + "\n")
        raise SystemExit(2)


def _encode(params):
    flat = []
    for key, value in (params or {}).items():
        if not isinstance(value, str):
            value = json.dumps(value) if isinstance(value, (list, dict, bool)) else str(value)
        flat.append((key, value))
    return urllib.parse.urlencode(flat)


def _multipart(filename, data):
    boundary = "----agentteam%s" % base64.urlsafe_b64encode(os.urandom(12)).decode()
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    head = (
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
        "Content-Type: %s\r\n\r\n" % (boundary, filename.replace('"', ""), ctype)
    ).encode()
    tail = ("\r\n--%s--\r\n" % boundary).encode()
    return head + data + tail, "multipart/form-data; boundary=%s" % boundary


def request(cfg, method, path, params=None, upload=None, open_fn=urllib.request.urlopen):
    """One HTTPS door. The key rides in the auth header and is never printed or logged."""
    url = cfg["site"] + path
    body = None
    headers = {
        "Authorization": "Basic "
        + base64.b64encode(("%s:%s" % (cfg["email"], cfg["key"])).encode()).decode(),
        "User-Agent": "agent-team/phase1",
    }
    if upload is not None:
        body, ctype = _multipart(upload[0], upload[1])
        headers["Content-Type"] = ctype
    elif method in ("POST", "PATCH"):
        body = _encode(params).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif method in ("GET", "DELETE") and params:
        url += "?" + _encode(params)
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with open_fn(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"msg": raw[:400]}
        payload.setdefault("result", "error")
        payload["http_status"] = exc.code
        return payload
    except urllib.error.URLError as exc:
        raise SystemExit("network error talking to %s: %s" % (cfg["site"], exc.reason))


def check(payload, what):
    if payload.get("result") != "success":
        raise SystemExit("%s failed: %s" % (what, payload.get("msg", payload)))
    return payload


def me(name):
    if name not in _ME:
        cfg = load(name)
        _ME[name] = check(request(cfg, "GET", "/api/v1/users/me"), "GET /users/me")
    return _ME[name]


def window(name):
    """The window is max_message_length from POST /register, never a constant."""
    if name not in _WINDOW:
        cfg = load(name)
        payload = check(
            request(
                cfg,
                "POST",
                "/api/v1/register",
                {"event_types": [], "fetch_event_types": ["realm"]},
            ),
            "POST /register",
        )
        value = payload.get("max_message_length")
        if not isinstance(value, int):
            raise SystemExit("register returned no max_message_length")
        _WINDOW[name] = value
    return _WINDOW[name]


def stream_id(name, name_or_id):
    if isinstance(name_or_id, int):
        return name_or_id
    text = str(name_or_id).strip()
    if text.isdigit():
        return int(text)
    cfg = load(name)
    payload = request(cfg, "GET", "/api/v1/get_stream_id", {"stream": text})
    if payload.get("result") != "success":
        return None
    return payload["stream_id"]


def _hash_component(text):
    """RFC 3986 percent-encoding with '.' standing in for '%', matching Zulip's own
    encodeHashComponent. Verified against a real Zulip-issued link (topic-verbs ticket).
    quote() never emits a literal '.', so the escape below only ever touches a source dot;
    Zulip's own decoder maps '.' back to '%' first, so an unescaped dot decodes wrong (Jan,
    2026-08-12: "api.py cleanup" broke)."""
    return urllib.parse.quote(text, safe="").replace(".", "%2E").replace("%", ".")


def permalink(site, stream_id, stream_name, topic, message_id, operator="near"):
    """A message's #narrow URL. Stream slug is id-name with spaces turned to hyphens before
    encoding; topic is hash-encoded whole. One pure function, callers have every ingredient
    already (stream_id() resolves the numeric id).

    operator="with" is the same link addressed to the topic instead of the message: Zulip
    follows it through a rename or a move, where near pins the message it names."""
    stream_frag = "%s-%s" % (stream_id, _hash_component(stream_name.replace(" ", "-")))
    topic_frag = _hash_component(topic)
    return "%s/#narrow/channel/%s/topic/%s/%s/%s" % (
        site, stream_frag, topic_frag, operator, message_id)


UPLOAD_MARK = "/user_uploads/"


def upload_path(url):
    """The path_id inside any shape of an upload link: a full URL, a site-relative path, or the
    path_id alone. None means the caller handed us something that is not an upload."""
    text = str(url or "").strip()
    if UPLOAD_MARK not in text:
        return None
    tail = text.split(UPLOAD_MARK, 1)[1].split("?")[0].split("#")[0]
    return tail or None


def attachment(name, url, open_fn=urllib.request.urlopen):
    """Fetch one upload. Zulip answers /user_uploads/<path_id> with a short-lived signed URL,
    which is then fetched with no credentials at all. Returns ((content_type, bytes, link), None)
    or (None, reason), matching read.fetch's shape."""
    path_id = upload_path(url)
    if path_id is None:
        return None, "not_an_upload"
    cfg = load(name)
    payload = request(cfg, "GET", "/api/v1" + UPLOAD_MARK + path_id)
    if payload.get("result") != "success":
        return None, payload.get("msg", payload)
    link = payload.get("url") or ""
    if link.startswith("/"):
        link = cfg["site"] + link
    try:
        with open_fn(urllib.request.Request(link), timeout=60) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
    except urllib.error.URLError as exc:
        return None, "fetching the temporary link failed: %s" % exc.reason
    return (ctype, data, link), None


def user_ids(name, emails):
    """Resolve realm emails to user ids. Returns (ids, unresolved), because a channel create
    needs integer ids (subscribers[0] is not an integer, probed 2026-08-20) and the caller
    should hear which address the realm does not know rather than a type error."""
    cfg = load(name)
    payload = request(cfg, "GET", "/api/v1/users")
    if payload.get("result") != "success":
        return [], list(emails)
    known = {u.get("email"): u.get("user_id") for u in payload.get("members", [])}
    ids, missing = [], []
    for email in emails:
        (ids if email in known else missing).append(known.get(email, email))
    return ids, missing


def visible_streams(name):
    cfg = load(name)
    payload = request(cfg, "GET", "/api/v1/streams")
    if payload.get("result") != "success":
        return []
    return sorted(s["name"] for s in payload.get("streams", []))


def topics(name, stream_id):
    cfg = load(name)
    payload = request(cfg, "GET", "/api/v1/users/me/%d/topics" % int(stream_id))
    if payload.get("result") != "success":
        return []
    return payload.get("topics", [])


def refuse_narrow_miss(as_name, channel):
    """Refuses with NARROW_MISS plus the identity's visible streams, exit 4; send.py's post,
    move_topic, and read.py's main() shared this block before extraction."""
    sys.stderr.write(
        prompts.NARROW_MISS.format(channel=channel, identity=as_name)
        + "\n"
        + "\n".join(visible_streams(as_name))
        + "\n"
    )
    raise SystemExit(4)


def _selftest():
    from tests import api_selftest
    return api_selftest.run(sys.modules[__name__])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    if ap.parse_args().selftest:
        sys.exit(_selftest())
    ap.error("nothing to do; api.py is a library")
