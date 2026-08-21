# Operating notes

Working rules for this fleet. Empty on purpose: add a rule only after the fleet teaches it,
and date it when you do.

## Status rules

Where status lives and what is never resolved there. First guesses are fine; label them as
first guesses so a later reader knows what has been tested.

## Ticket flow

Which channel opens a ticket, and where that ticket's build and verification run.

## Jobfinder board handoff

When Bridge works the Jobfinder board live with Mate, it hands the cleared run to Chella once,
not item by item. Post under the current `#1jf-sweeps` review post, following message 618018455:

```markdown
## Actions taken, <YYYY-MM-DD>

**Sent**
- **<item>. <counterpart>**: <what went and what was deliberately omitted>. Message id `<id>`.

**Dropped, no reply**
- **<item>. <counterpart>**: <reason>.

**Held**
- **<item>. <counterpart>**: hold.

**Revise**
- **<item>. <counterpart>**: <Mate's revision note>.

@Chella
```

Every sent item carries its Gmail or LinkedIn message id so Chella can copy the send record into
the counterpart topic without searching or sending again. Chella treats this batch as Mate's
verdict, records sends and drops, returns revisions under the same review post, and rebuilds the
board.

Resolve is cosmetic and stays Bridge-only. During a live send session, Bridge resolves its sent
and dropped topics together after posting the batch. On a later live session, Bridge may resolve
a batch from Chella's `## Sent` and `## Dropped` topic records. Board rows clear from those records
and never wait for Bridge to be awake.

## Links and attachments

How documents ship. Sha-pinned links for what a reader can pull; attachments for the rest.

## Permissions ledger

Every capability grant, recorded here in the same commit as the change that uses it, so the
cost stays visible.

## Channel descriptions (canonical copy)

Your chat tool likely keeps no edit history for channel descriptions. Keep the canonical text
here and paste it by hand.
