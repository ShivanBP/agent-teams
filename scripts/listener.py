"""The daemon: eight identities, one thread each, wakes only. Phase 3 grows the operator rails on
top of this file: Rail A continues an open loop after a persona's wake lands (handle_rail_a),
Rail B answers a direct tag of the "bridge" identity from the operator (handle_operator_tag),
and a background thread pauses loops behind inflight rows that stalled (stall_sweep_thread).

The zulip pip library is imported only in this module (amendment 6); everything else in the
daemon path (store, personas, prompts, runner, loops) stays stdlib.
"""

import argparse
import logging
import os
import subprocess
import sys
import threading
import time

import api
import constants
import digest
import loops
import monitor
import personas
import prompts
import read as read_mod
import runner
import send as send_mod
import store
import todo

log = logging.getLogger("agent-team.listener")

IDENTITIES = list(personas.PERSONAS) + [constants.BRIDGE_IDENTITY]

_USER_IDS = {}
_USER_IDS_LOCK = threading.Lock()


# --- pure helpers (table-tested by --selftest) -------------------------------------------------

def is_mention(event):
    """Flags ride on the EVENT object, not inside message (verified live 2026-08-12); message-level is the backfill fallback."""
    msg = event.get("message") or {}
    return "mentioned" in (event.get("flags") or msg.get("flags") or [])


def is_persona_sender(sender_email, persona_emails):
    return bool(sender_email) and sender_email in persona_emails


def is_resolved_topic(topic_name):
    """A topic is an open session (ruling 7): a name starting with the resolve marker is resolved.
    Reopening (removing the marker) starts a fresh session by design; nothing resurrects the old one."""
    return (topic_name or "").strip().startswith(constants.RESOLVED_PREFIX)


# A flag word is its vocabulary word with a dash: adding a model, effort level or provider is
# one edit in constants.py or a config file, never a second one here.
FLAG_TO_MODEL = {"-" + name: name for name in constants.CLAUDE_MODELS}
FLAG_TO_EFFORT = {"-" + level: level for level in constants.EFFORT_LEVELS}
FLAG_TO_PROVIDER = {"-" + name: name for name in constants.PROVIDERS}
FLAG_WORDS = tuple(FLAG_TO_MODEL) + tuple(FLAG_TO_EFFORT) + tuple(FLAG_TO_PROVIDER)


def parse_flags(content):
    """Detects flag words for any sender and always strips them from the body, so a persona never
    sees flag noise. Whether they apply is the caller's call: model/effort/provider flags parse only off
    resolved flag-holder user ids (invariant); handle_wake makes that decision, not this function."""
    words = (content or "").split()
    found = [w for w in words if w in FLAG_WORDS]
    if not found:
        return [], content
    stripped = " ".join(w for w in words if w not in FLAG_WORDS)
    return found, stripped


def flags_to_overrides(flags):
    model = None
    effort = None
    for f in flags:
        model = FLAG_TO_MODEL.get(f, model)
        effort = FLAG_TO_EFFORT.get(f, effort)
    return model, effort


def provider_for_wake(identity, flags, row, matrix=None):
    """Last explicit provider, sticky lane, persona default, then Claude. Every persona supports
    every provider, so a provider flag applies to any identity on the roster."""
    roster = matrix if matrix is not None else personas.PERSONAS
    explicit = None
    for flag in flags:
        candidate = FLAG_TO_PROVIDER.get(flag)
        if candidate and identity in roster:
            explicit = candidate
    if explicit:
        return explicit
    if row:
        return store.session_provider(row)
    defaults = matrix[identity] if matrix is not None else constants.matrix_defaults(identity)
    return defaults["provider"]


def resolve_wake_settings(identity, provider, model, effort, matrix=None):
    defaults = matrix[identity] if matrix is not None else constants.matrix_defaults(identity)
    if provider == defaults["provider"]:
        run_model = model if provider == "claude" and model else defaults["model"]
    elif provider == "codex":
        run_model = constants.CODEX_MODEL
    elif provider == "agy":
        run_model = constants.AGY_MODEL
    elif provider == "opencode":
        run_model = constants.OPENCODE_MODEL
    else:
        run_model = model if provider == "claude" else None
    if effort:
        level = effort
    elif provider == "claude" and model and model in constants.MODEL_EFFORT_DEFAULTS:
        level = constants.MODEL_EFFORT_DEFAULTS[model]
    elif provider != defaults["provider"] and provider in constants.HARNESS_DEFAULTS:
        level = constants.HARNESS_DEFAULTS[provider]["effort"]
    else:
        level = defaults["effort"]
    # the generic level travels beside the harness's translation: the footer reports the word
    # the operator types (low/mid/high/xtra), not the word the CLI takes.
    return run_model, constants.translate_effort(provider, level), level


def parse_operator_decision(text):
    """Finds the operator's single decision line ('KICK: <persona> <body>' or 'CLOSE: <reason>')
    amid any prose; zero or more than one decision line parses to None so a malformed or
    ambiguous answer never gets read as a kick (never-forge-a-kick, invariant)."""
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    decisions = [l for l in lines if l.startswith("KICK:") or l.startswith("CLOSE:")]
    if len(decisions) != 1:
        return None
    line = decisions[0]
    if line.startswith("KICK:"):
        rest = line[len("KICK:"):].strip()
        parts = rest.split(None, 1)
        if len(parts) == 2 and parts[0]:
            return ("kick", parts[0], parts[1])
        return None
    if line.startswith("CLOSE:"):
        reason = line[len("CLOSE:"):].strip()
        if reason:
            return ("close", reason)
        return None
    return None


def _location_from_refetch(payload, fallback_channel, fallback_topic):
    """Pure: turns a GET /messages/<id> refetch payload into (channel, topic), falling back to
    the wake-time lane when the refetch failed or returned no location. Shared by handle_wake
    and handle_operator_tag (this file) so a reply lands inside a topic just resolved or moved,
    instead of forking an unresolved twin, one spelling for both call sites."""
    if payload.get("result") != "success":
        return fallback_channel, fallback_topic
    msg = payload.get("message") or {}
    return msg.get("display_recipient", fallback_channel), msg.get("subject", fallback_topic)


def _close_loop(loop_id, channel, topic, reason):
    """Closes the loop then posts LOOP_BUDGET_OUT; a failed post is logged, never re-raised.
    handle_rail_a's budget-reached branch, its kick_record-refused branch, and its close-decision
    branch shared this shape before extraction."""
    loops.close(loop_id)
    try:
        send_mod.post(constants.BRIDGE_IDENTITY, channel, topic,
                       prompts.LOOP_BUDGET_OUT.format(reason=reason, mate=mate_mention()))
    except (Exception, SystemExit):
        log.exception("failed to post loop-close line for loop %s", loop_id)


def _append_cost(persona, lane, result, model=None, level=None):
    row = {
        "persona": persona,
        "lane": lane,
        "usd": result.cost_usd,
        "turns": result.turns,
        "cache_read": result.usage.get("cache_read_input_tokens", 0),
        "cache_creation": result.usage.get("cache_creation_input_tokens", 0),
        "input_tokens": result.usage.get("input_tokens", 0),
        "provider": result.provider,
        # the footer's own two fields, in the fleet's effort words, so a posted stamp is
        # checkable against the ledger; a kick posts no footer, so its row is the only stamp.
        "model": model,
        "effort": level,
    }
    for key in ("output_tokens", "thinking_tokens", "total_tokens"):
        if key in result.usage:
            row[key] = result.usage[key]
    store.cost_append(row)


def _post_at_current_location(identity, message_id, channel, topic, body, footer=""):
    """Refetches the message's current channel/topic before posting, so a resolve or rename
    between wake and reply lands the reply inside the moved topic instead of forking an
    unresolved twin. handle_wake and handle_operator_tag shared this shape before extraction.
    Returns (post_channel, post_topic) for callers that need the resolved location afterward."""
    post_channel, post_topic = channel, topic
    try:
        cur = api.request(api.load(identity), "GET", "/api/v1/messages/%d" % message_id)
        post_channel, post_topic = _location_from_refetch(cur, channel, topic)
    except (Exception, SystemExit):
        log.exception("current-location refetch failed for message %s; using wake-time lane", message_id)
    send_mod.post(identity, post_channel, post_topic, body, footer=footer)
    return post_channel, post_topic


def _failure_reason(exc):
    """One line for a posted note, never a traceback: str(exc) collapsed, class name if it is empty."""
    return " ".join(str(exc).split()) or type(exc).__name__


def _post_operator_failure(template, exc, channel, topic, message_id=None):
    body = template.format(reason=_failure_reason(exc))
    try:
        if message_id is None:
            send_mod.post(constants.BRIDGE_IDENTITY, channel, topic, body)
        else:
            _post_at_current_location(constants.BRIDGE_IDENTITY, message_id, channel, topic, body)
    except (Exception, SystemExit):
        log.exception("failed to post operator failure notice")


def refresh_topic_digest(identity, channel, topic):
    stream_id = api.stream_id(identity, channel)
    if stream_id is not None:
        return digest.refresh_topic(identity, stream_id, channel, topic)
    return None


def is_tag_stale(msg_timestamp, now_ts, max_age_min):
    """A tag older than TAG_MAX_AGE_MIN is skipped, never answered late (invariant). A missing
    timestamp is treated as stale rather than risk answering something we cannot date."""
    if msg_timestamp is None:
        return True
    return (now_ts - msg_timestamp) > max_age_min * 60


# --- identity / flag-holder resolution -----------------------------------------------------------

def _resolve_user_ids(identity):
    if "flag_holder_ids" in _USER_IDS:
        return
    with _USER_IDS_LOCK:
        if "flag_holder_ids" in _USER_IDS:
            return
        holder_emails = constants.AGENT_TEAM_MATE_EMAILS
        mate_email = constants.AGENT_TEAM_MATE_EMAIL
        mate_id = None
        mention = ""
        holder_ids = set()
        resolved_emails = set()
        if not holder_emails and not mate_email:
            log.warning("AGENT_TEAM_MATE_EMAILS and AGENT_TEAM_MATE_EMAIL are empty; "
                        "flags and operator-rail checks stay off")
        else:
            payload = api.request(api.load(identity), "GET", "/api/v1/users")
            if payload.get("result") == "success":
                for user in payload.get("members", []):
                    email = user.get("email")
                    if email in holder_emails:
                        holder_ids.add(user.get("user_id"))
                        resolved_emails.add(email)
                    if mate_email and email == mate_email:
                        mate_id = user.get("user_id")
                        mention = "@**%s**" % (user.get("full_name") or email.split("@")[0])
        missing = holder_emails - resolved_emails
        if missing:
            log.error("could not resolve flag-holder emails to user ids: %s",
                      ", ".join(sorted(missing)))
        if mate_email and mate_id is None:
            log.error("could not resolve AGENT_TEAM_MATE_EMAIL=%s to a user id", mate_email)
        elif not mate_email and holder_emails:
            log.warning("AGENT_TEAM_MATE_EMAIL is empty; operator-rail checks stay off")
        _USER_IDS["mate_id"] = mate_id
        _USER_IDS["mention"] = mention
        _USER_IDS["flag_holder_ids"] = frozenset(holder_ids)


def mate_user_id(identity=constants.BRIDGE_IDENTITY):
    """Resolve and cache the operator's Rail B id alongside the flag-holder id set."""
    _resolve_user_ids(identity)
    return _USER_IDS["mate_id"]


def flag_holder_user_ids(identity=constants.BRIDGE_IDENTITY):
    """Resolve and cache the user ids whose per-message flags apply."""
    _resolve_user_ids(identity)
    return _USER_IDS["flag_holder_ids"]


def mate_mention():
    """Zulip mentions of the operator are @-mentions of the resolved display name (amendment 3:
    there is no separate operator bot to tag from)."""
    mate_user_id()
    return _USER_IDS.get("mention", "")


# --- the delta record: only messages newer than the lane's stored anchor ------------------------

def build_delta_record(as_name, channel, topic, since_id):
    """Fetch topic messages newer than since_id (None on a lane's first wake => backfill up to the
    window), capped at RECORD_WINDOW. Returns (record_text_or_None, new_anchor)."""
    window = constants.RECORD_WINDOW
    cfg = api.load(as_name)
    sid = api.stream_id(as_name, channel)
    if sid is None:
        return None, since_id
    narrow = [{"operator": "channel", "operand": sid}, {"operator": "topic", "operand": topic}]
    if since_id:
        params = {
            "anchor": since_id, "num_before": 0, "num_after": window + 1,
            "narrow": narrow, "apply_markdown": False, "include_anchor": False,
        }
    else:
        params = {
            "anchor": "newest", "num_before": window, "num_after": 0,
            "narrow": narrow, "apply_markdown": False,
        }
    payload = api.request(cfg, "GET", "/api/v1/messages", params)
    if payload.get("result") != "success":
        return None, since_id
    messages = payload.get("messages", [])
    truncated = len(messages) > window
    shown = messages[-window:] if not since_id else messages[:window]
    if not shown:
        return None, since_id
    new_anchor = shown[-1]["id"]
    text = read_mod.render(shown)
    if truncated:
        text += "\n" + prompts.RECORD_TRUNCATION_NOTE.format(shown=len(shown), available=len(messages))
    return text, new_anchor


# --- resolve lifecycle: an update_message event that resolves a topic drops its sessions --------

def handle_topic_resolved(event):
    """A topic edit that sets the new subject to a resolved name ends every persona's session on
    that stream+topic (finding 1b): otherwise a resolved rename orphans the row instead of
    dropping it. Reopening (ruling 7) never resurrects it; a later mention starts fresh."""
    new_subject = event.get("subject")
    stream_id = event.get("stream_id")
    if new_subject is None or stream_id is None or not is_resolved_topic(new_subject):
        return
    normalized = store.normalize_topic(new_subject)
    for persona in personas.PERSONAS:
        store.session_drop(store.lane_key(stream_id, normalized, persona))

    def clear_parked(data):
        data.pop(digest.digest_key(stream_id, normalized), None)
        return data

    store.mutate(constants.PARKED_STATE, clear_parked)
    log.info("topic resolved stream_id=%s topic=%r; dropped sessions for %d personas",
              stream_id, normalized, len(personas.PERSONAS))


# --- the wake path ---------------------------------------------------------------------------

def handle_wake(identity, event, flag_holder_ids):
    msg = event.get("message") or {}
    stream_id = msg.get("stream_id")
    channel = msg.get("display_recipient")
    topic = msg.get("subject", "")
    sender_id = msg.get("sender_id")
    content = msg.get("content", "")
    message_id = msg.get("id")

    lane = store.lane_key(stream_id, topic, identity)

    if is_resolved_topic(topic):
        store.session_drop(lane)
        log.info("skip wake: lane %s topic is resolved", lane)
        return

    flags, body = parse_flags(content)
    if flags:
        if sender_id in flag_holder_ids:
            pass  # applied below via flags_to_overrides
        else:
            log.info("flags %s from non-flag-holder sender %s ignored on lane %s",
                     flags, sender_id, lane)
            flags = []
    model, effort = flags_to_overrides(flags)

    try:
        send_mod.react(identity, message_id, constants.EMOJI_RECEIPT)
    except (Exception, SystemExit):
        log.exception("react receipt failed for lane %s", lane)

    # inflight is visible before the lane lock is even requested, so a waiter blocked on the lock
    # can already see this wake (finding 2); cleared only after the lock is released. stream_id
    # and topic ride along so the stall sweep can find this lane's loop without unparsing the key.
    store.inflight_add(lane, {"persona": identity, "message_id": message_id, "stream_id": stream_id, "topic": topic})
    try:
        with store.lane_lock(lane):
            row = store.session_get(lane) or {}
            record_anchor = row.get("record_anchor")
            provider = provider_for_wake(identity, flags, row)

            def _set_provider(data, lane=lane, provider=provider):
                if lane in data:
                    data[lane]["provider"] = provider

            store.mutate("inflight", _set_provider)
            session_id = store.session_for_provider(row, provider)

            try:
                record, new_anchor = build_delta_record(identity, channel, topic, record_anchor)
                run_cwd, worktree_notice = runner.wake_cwd(identity, stream_id, topic)
                prompt = prompts.wake_prompt(body, record, worktree_notice,
                                             constants.domain_root(channel))
                run_model, run_effort, run_level = resolve_wake_settings(
                    identity, provider, model, effort)
                log.info("running %s lane=%s provider=%s model=%s effort=%s resume=%s",
                         identity, lane, provider, run_model, run_effort, bool(session_id))
                result = runner.run(identity, prompt, provider=provider, model=run_model,
                                    effort=run_effort, session=session_id,
                                    cwd=run_cwd, lane=lane)
                # write-back lands immediately after a successful run, before any posting, so a
                # failed post never loses it (finding 3a). Session stays keyed to the wake-time
                # lane by design: a rename between kick and reply starts a new lane, not a migration.
                store.session_set(lane, result.session_id,
                                  new_anchor if new_anchor is not None else record_anchor,
                                  result.provider)
                _append_cost(identity, lane, result, run_model, run_level)
                # a reply follows its topic; a rename between kick and reply must not fork the thread.
                # the footer is stamped here from the runner's resolved values, never by the persona.
                post_channel, post_topic = _post_at_current_location(
                    identity, message_id, channel, topic,
                    prompts.with_notice(result.reply, worktree_notice),
                    prompts.wake_footer(result.provider, run_model, run_level, result.session_id,
                                        result.degraded))
            except (Exception, SystemExit) as exc:
                log.exception("wake failed for lane %s", lane)
                # a failed wake leaves no resumable session: dropped before the note, so a post
                # that fails cannot leave the next wake resuming a corpse.
                store.session_drop(lane)
                try:
                    send_mod.post(identity, channel, topic,
                                  prompts.WAKE_FAILED_NOTE.format(reason=_failure_reason(exc)))
                except (Exception, SystemExit):
                    log.exception("failed to post the wake-failure note for lane %s", lane)
            else:
                try:
                    refresh_topic_digest(identity, post_channel, post_topic)
                except (Exception, SystemExit):
                    log.exception("topic digest refresh failed for lane %s", lane)
                # Rail A: only after the reply landed, and its own failures never read back as a
                # failed wake (they are logged and swallowed inside handle_rail_a itself). Current
                # topic goes to the loop lookup too, so a rename doesn't miss the loop.
                try:
                    handle_rail_a(stream_id, post_channel, post_topic, record, result.reply)
                except (Exception, SystemExit) as exc:
                    log.exception("rail A continuation check failed for lane %s", lane)
                    _post_operator_failure(prompts.OPERATOR_CONTINUATION_FAILED, exc,
                                           post_channel, post_topic)
    finally:
        store.inflight_clear(lane)


# --- Rail A: a finished persona wake continues its lane's open loop, if any ---------------------

def handle_rail_a(stream_id, channel, topic, record, reply):
    """After a persona's reply has landed, continue any open loop on this stream+topic: the
    budget floor is checked against the ledger before the continuation is even spawned (invariant);
    at budget the loop closes without a run. Otherwise the operator continuation decides kick or
    close, and a reply that fails to parse leaves the loop untouched (never forge a kick)."""
    loop = loops.loop_for_lane(constants.BRIDGE_IDENTITY, stream_id, topic)
    if loop is None:
        return
    loop_id = loop["id"]
    dest_channel = loop["current_channel"] or channel
    dest_topic = loop["current_topic"] or topic

    if loops.budget_reached(loop_id):
        _close_loop(loop_id, dest_channel, dest_topic, prompts.LOOP_BUDGET_REASON.format(budget=loop["budget"]))
        return

    reply_text = reply or ""
    if len(reply_text) > constants.REPLY_TRUNCATION_LIMIT:
        reply_text = (reply_text[:constants.REPLY_TRUNCATION_LIMIT] +
                      prompts.REPLY_TRUNCATION_NOTE.format(limit=constants.REPLY_TRUNCATION_LIMIT, total=len(reply_text)))

    brief = prompts.OPERATOR_BRIEF.format(
        header_id=loop["header_id"],
        header_text=loop.get("header_text") or "",
        n=loop["kicks"],
        remaining=loop["budget"] - loop["kicks"],
        budget=loop["budget"],
        reply=reply_text,
        state=prompts.state_block(store.state_summary()),
        record=record or "",
    )
    # both operator rails spawn under the bridge identity, or the two grow separate memory trees.
    # settings come from config/rails.json, the one source: the agent frontmatter carries none.
    try:
        rail = constants.rail_defaults("operator")
        result = runner.run("operator", brief, provider=rail["provider"], model=rail["model"],
                            effort=constants.translate_effort(rail["provider"], rail["effort"]),
                            identity=constants.BRIDGE_IDENTITY)
    except (Exception, SystemExit) as exc:
        log.exception("operator continuation run failed for loop %s", loop_id)
        _post_operator_failure(prompts.OPERATOR_CONTINUATION_FAILED, exc, dest_channel, dest_topic)
        return
    _append_cost("operator", dest_topic, result, rail["model"], rail["effort"])
    decision = parse_operator_decision(result.reply)
    if decision is None:
        log.info("continuation reply for loop %s did not parse to a decision; loop left untouched; reply was: %r",
                 loop_id, (result.reply or "")[:300])
        return

    if decision[0] == "kick":
        _, persona_name, body = decision
        if persona_name not in personas.PERSONAS:
            log.warning("operator kicked unknown persona %r for loop %s; skipped, never forged", persona_name, loop_id)
            return
        # ledger row written before the kick posts (invariant); check-and-append is atomic
        # against a concurrent continuation on the same loop (finding 3).
        n = loops.kick_record(loop_id, persona=persona_name)
        if n is False:
            _close_loop(loop_id, dest_channel, dest_topic, prompts.LOOP_BUDGET_REASON.format(budget=loop["budget"]))
            return
        # A kick must mention its persona or the wake never fires; same shape as loops.py's CLI kick.
        kick_text = prompts.kick_body(
            prompts.MENTION.format(name=personas.display_name(persona_name), body=body),
            n, loop["budget"])
        try:
            send_mod.post(constants.BRIDGE_IDENTITY, dest_channel, dest_topic, kick_text)
        except (Exception, SystemExit):
            log.exception("kick post failed for loop %s (ledger already advanced to %d/%d)", loop_id, n, loop["budget"])
    elif decision[0] == "close":
        _, reason = decision
        _close_loop(loop_id, dest_channel, dest_topic, reason)


# --- Rail B: the operator tags the bridge directly -----------------------------------------

def handle_operator_tag(event, mate_id):
    """A message event on the bridge identity with the mentioned flag: proven to be from the
    operator by user id, within TAG_MAX_AGE_MIN of the message timestamp, or it is skipped and
    logged, never answered late and never answered on a bot's say-so."""
    msg = event.get("message") or {}
    sender_id = msg.get("sender_id")
    message_id = msg.get("id")
    stream_id = msg.get("stream_id")
    channel = msg.get("display_recipient")
    topic = msg.get("subject", "")
    content = msg.get("content", "")
    ts = msg.get("timestamp")

    if mate_id is None or sender_id != mate_id:
        log.info(prompts.TAG_NOT_OPERATOR.format(sender=sender_id))
        return
    if is_tag_stale(ts, time.time(), constants.TAG_MAX_AGE_MIN):
        log.info(prompts.TAG_STALE.format(sender=sender_id, message_id=message_id, max_age=constants.TAG_MAX_AGE_MIN))
        return
    if is_resolved_topic(topic):
        log.info("operator tag on resolved topic %r; skipped", topic)
        return

    # eyes means accepted and running, same as the wake path; a dropped tag gets none.
    try:
        send_mod.react(constants.BRIDGE_IDENTITY, message_id, constants.EMOJI_RECEIPT)
    except (Exception, SystemExit):
        log.exception("react receipt failed for operator tag %s", message_id)

    loop = loops.loop_for_lane(constants.BRIDGE_IDENTITY, stream_id, topic)
    loop_note = ""
    if loop is not None:
        loop_note = prompts.LOOP_NOTE.format(loop_id=loop["id"], n=loop["kicks"], budget=loop["budget"])

    record, _ = build_delta_record(constants.BRIDGE_IDENTITY, channel, topic, None)
    # the tag message is the id the seat opens a loop against: it is the header, never one of
    # Bridge's own posts.
    brief = prompts.BRIDGE_BRIEF.format(message=content.strip(), record=record or "",
                                                loop_note=loop_note, message_id=message_id,
                                                state=prompts.state_block(store.state_summary()))
    try:
        # agent name and identity are the same word now: AGENT_TEAM_IDENTITY=bridge is what
        # lets the seat call send.py --as bridge itself.
        rail = constants.rail_defaults("bridge")
        result = runner.run("bridge", brief, provider=rail["provider"], model=rail["model"],
                            effort=constants.translate_effort(rail["provider"], rail["effort"]),
                            identity=constants.BRIDGE_IDENTITY)
        _append_cost("bridge", topic, result, rail["model"], rail["effort"])
        # a reply follows its topic; resolve-then-reply must land inside the resolved topic,
        # not fork an unresolved twin (shares handle_wake's refetch helper, _post_at_current_location above).
        # The footer stamps what the runner was told, in the fleet's effort word, same as a wake's.
        _post_at_current_location(constants.BRIDGE_IDENTITY, message_id, channel, topic, result.reply,
                                   prompts.wake_footer(result.provider, rail["model"], rail["effort"],
                                                       result.session_id, result.degraded))
    except (Exception, SystemExit) as exc:
        log.exception("bridge seat run failed for tag message %s", message_id)
        _post_operator_failure(prompts.BRIDGE_REPLY_FAILED, exc, channel, topic, message_id)


# --- stall sweep: a periodic thread pausing loops behind inflight rows that never wrote back -----

def stalled_wake(lane, now_ts, run=subprocess.run, own_pid=os.getpid):
    """Return report fields when a quiet wake log has no holder or no TCP-active holder."""
    wake_log = runner._wake_log_path(lane)
    try:
        quiet_s = max(0, now_ts - wake_log.stat().st_mtime)
    except OSError:
        return None
    if quiet_s <= constants.STALL_MIN * 60:
        return None

    try:
        holders = run([constants.LSOF_BIN, "-t", "--", str(wake_log)],
                      capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        log.exception("failed to inspect wake-log holders for lane %s", lane)
        return None
    if holders.returncode not in (0, 1):
        log.warning("lsof failed inspecting wake-log holders for lane %s: %s",
                    lane, holders.stderr.strip())
        return None
    pids = []
    for raw in holders.stdout.splitlines():
        try:
            pid = int(raw)
        except ValueError:
            continue
        # The listener holds the redirected output file beside its harness child.
        if pid != own_pid():
            pids.append(pid)
    if not pids:
        return {"pid": prompts.NO_PROCESS_PID, "quiet_s": quiet_s,
                "wake_log": str(wake_log)}

    for pid in pids:
        try:
            sockets = run([constants.LSOF_BIN, "-a", "-p", str(pid), "-iTCP", "-Fn"],
                          capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            log.exception("failed to inspect TCP sockets for lane %s pid %s", lane, pid)
            return None
        if sockets.returncode not in (0, 1):
            log.warning("lsof failed inspecting TCP sockets for lane %s pid %s: %s",
                        lane, pid, sockets.stderr.strip())
            return None
        if any(line.startswith("n") for line in sockets.stdout.splitlines()):
            return None
    return {"pid": pids[0], "quiet_s": quiet_s, "wake_log": str(wake_log)}

def stall_sweep_once(now_ts=None):
    """Finish every local stall check before best-effort Zulip posts and the board update."""
    now_ts = now_ts if now_ts is not None else time.time()
    alerts = []
    for lane, info in list(store.inflight_all().items()):
        if info.get("alerted"):
            continue
        hit = stalled_wake(lane, now_ts)
        if hit is None:
            continue

        stream_id = info.get("stream_id")
        topic = info.get("topic")
        if stream_id is not None and topic is not None:
            loop = loops.loop_for_lane(constants.BRIDGE_IDENTITY, stream_id, topic)
            if loop is not None and loop.get("status") == loops.STATUS_OPEN:
                loops.pause(loop["id"])
                log.info("paused loop %s: stall on lane %s", loop["id"], lane)

        alerts.append((lane, hit))

    for lane, hit in alerts:
        try:
            send_mod.post(constants.BRIDGE_IDENTITY, constants.STATUS_STREAM, constants.ALERTS_TOPIC,
                           prompts.STALLED_WAKE_ALERT.format(
                               lane=lane, pid=hit["pid"], quiet_min=int(hit["quiet_s"] // 60),
                               wake_log=hit["wake_log"]))
        except (Exception, SystemExit):
            log.exception("failed to post stall alert for lane %s", lane)
            continue

        def fn(data, lane=lane):
            if lane in data:
                data[lane]["alerted"] = True

        store.mutate("inflight", fn)
    monitor.update_board()


def stall_sweep_thread(interval=60):
    while True:
        try:
            stall_sweep_once()
        except (Exception, SystemExit):
            log.exception("stall sweep pass failed")
        time.sleep(interval)


def start_sweep_threads(thread_factory=threading.Thread):
    threads = [
        thread_factory(target=stall_sweep_thread, name="stall-sweep", daemon=True),
        thread_factory(target=todo.sweep_thread, name="todo-sweep", daemon=True),
    ]
    for thread in threads:
        thread.start()
    return threads


def operator_tag_worker(event, mate_id):
    # Same SystemExit-safety as wake_worker: a thread callback must never die silently.
    try:
        handle_operator_tag(event, mate_id)
    except (Exception, SystemExit):
        log.exception("unhandled error handling operator tag on message %s", (event.get("message") or {}).get("id"))


# --- catch-up: backfill mentions missed while the daemon was down --------------------------------

def backfill(identity, mate_id, flag_holder_ids, persona_emails):
    """Covers personas and the bridge identity: a queue re-registration gap can drop a Rail B
    tag as silently as a persona wake, and handle_operator_tag's staleness guard makes replaying
    an old tag here safe (it skips and logs rather than answering late)."""
    last_id = store.seen_get(identity)
    cfg = api.load(identity)
    narrow = [{"operator": "is", "operand": "mentioned"}]
    if last_id:
        payload = api.request(cfg, "GET", "/api/v1/messages", {
            "anchor": last_id, "num_before": 0, "num_after": 200,
            "narrow": narrow, "apply_markdown": False, "include_anchor": False,
        })
    else:
        payload = api.request(cfg, "GET", "/api/v1/messages", {
            "anchor": "newest", "num_before": 0, "num_after": 0,
            "narrow": narrow, "apply_markdown": False,
        })
    if payload.get("result") != "success":
        log.error("backfill failed for %s: %s", identity, payload.get("msg", payload))
        return
    messages = payload.get("messages", [])
    for msg in messages:
        if identity in personas.PERSONAS and is_persona_sender(msg.get("sender_email"), persona_emails):
            log.info("skip wake: persona sender mentioned %s on message %s", identity, msg.get("id"))
            continue
        fake_event = {"type": "message", "message": dict(msg, flags=list(set(msg.get("flags", [])) | {"mentioned"}))}
        try:
            if identity == constants.BRIDGE_IDENTITY:
                handle_operator_tag(fake_event, mate_id)
            else:
                handle_wake(identity, fake_event, flag_holder_ids)
        except (Exception, SystemExit):
            log.exception("backfill wake failed for %s message %s", identity, msg.get("id"))
    if messages:
        store.seen_set(identity, messages[-1]["id"])


# --- per-identity thread ---------------------------------------------------------------------

def run_identity(identity, persona_emails):
    import zulip

    log.info("starting identity %s", identity)
    me = api.me(identity)
    mate_id = mate_user_id()
    flag_holder_ids = flag_holder_user_ids()

    if identity in personas.PERSONAS or identity == constants.BRIDGE_IDENTITY:
        try:
            backfill(identity, mate_id, flag_holder_ids, persona_emails)
        except Exception:
            log.exception("backfill crashed for %s", identity)

    def wake_worker(msg_id, event, flag_holder_ids_for_wake):
        # Never let SystemExit (send.py, api.py) escape a thread's callback and kill it silently;
        # KeyboardInterrupt is not Exception or SystemExit, so it still propagates (finding 3b).
        try:
            handle_wake(identity, event, flag_holder_ids_for_wake)
        except (Exception, SystemExit):
            log.exception("unhandled error waking %s on message %s", identity, msg_id)

    def cb(event):
        if event.get("type") == "update_message":
            try:
                handle_topic_resolved(event)
            except (Exception, SystemExit):
                log.exception("update_message handling failed for identity %s", identity)
            return
        if event.get("type") != "message":
            return
        msg = event.get("message") or {}
        if msg.get("sender_id") == me.get("user_id"):
            return
        if not is_mention(event):
            return
        if identity not in personas.PERSONAS:
            if identity == constants.BRIDGE_IDENTITY:
                # Advances the same seen-id ledger backfill() reads, so a later restart's
                # anchor is this tag, not None (which would fetch nothing and re-open the gap).
                store.seen_set(identity, msg.get("id"))
                threading.Thread(
                    target=operator_tag_worker,
                    args=(event, mate_user_id()),
                    name="railb-%s" % msg.get("id"),
                    daemon=True,
                ).start()
            return
        msg_id = msg.get("id")
        store.seen_set(identity, msg_id)
        if is_persona_sender(msg.get("sender_email"), persona_emails):
            log.info("skip wake: persona sender mentioned %s on message %s", identity, msg_id)
            return
        # Each wake runs in its own thread; the lane lock (plus finding 2's ordering) already
        # serializes same-lane wakes, so cross-lane wakes of one persona overlap instead of
        # queuing behind each other (finding 4).
        threading.Thread(
            target=wake_worker,
            args=(msg_id, event, flag_holder_user_ids()),
            name="wake-%s-%s" % (identity, msg_id),
            daemon=True,
        ).start()

    client = zulip.Client(config_file=str(constants.CONFIG_DIR / ("%s.zuliprc" % identity)))
    client.call_on_each_event(
        cb,
        event_types=["message", "update_message"],
        all_public_streams=True,
        idle_queue_timeout=constants.IDLE_QUEUE_TIMEOUT,
    )


def check_rails(lookup=constants.rail_defaults):
    """Resolves both rail rows once at boot, so a rails.json missing a key, missing a field, or
    naming an effort its provider has no scale for stops restart.sh instead of a live wake."""
    for rail in ("operator", "bridge"):
        try:
            row = lookup(rail)
            constants.translate_effort(row["provider"], row["effort"])
            log.info("rail %s: provider=%s model=%s effort=%s",
                     rail, row["provider"], row["model"], row["effort"])
        except (RuntimeError, KeyError) as exc:
            raise SystemExit("rails config unusable for %s: %s" % (rail, exc))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log.info("agent-team listener starting: identities=%s", IDENTITIES)
    check_rails()
    persona_emails = frozenset(api.load(identity)["email"] for identity in personas.PERSONAS)
    start_sweep_threads()
    threads = []
    for identity in IDENTITIES:
        t = threading.Thread(target=run_identity, args=(identity, persona_emails),
                             name="listener-%s" % identity, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


def _selftest():
    from tests import listener_selftest
    return listener_selftest.run(sys.modules[__name__])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(_selftest())
    main()
