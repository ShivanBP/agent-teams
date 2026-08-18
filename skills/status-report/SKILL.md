---
name: status-report
description: Shape for any status report to the operator. Use whenever the operator asks for a status, an update, or where things stand.
---

# Status report

Report broken down by folder > channel > topic, a todo checklist within each topic.

- Folders group channels. Non-Zulip surfaces get a folder-level heading of their own at the
  end.
- One `##` heading per folder, bold topic names within their channel, `### channel` only
  when more than one channel is in play.
- Under each topic: checklist items. `[x]` done since the last report, `[ ]` open. Bold the
  actor on every open item; items waiting on the operator come first and are bold throughout.
- Two lines of prose per topic at most, above the checklist, only when the checklist alone
  would mislead.
- Order: most recently active first. Skip topics with nothing done and nothing open.
- End with a one-line "biggest lever" naming the single item that unblocks the most.
