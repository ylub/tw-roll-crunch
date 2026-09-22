# Roll

Roll keeps the due dates of dependent tasks aligned with the work before them.
It runs as a Taskwarrior `on-exit` hook and uses only Python's standard library.

## Model

Each pending or waiting rolling task identifies one predecessor by UUID in
`roll`. Roll chooses a base date from that predecessor:

- completed predecessor: its `end` timestamp
- pending or waiting predecessor: its `due` timestamp

The task's `roll_offset` is applied to that base date. This makes the ordinary
relationship:

```text
task due = predecessor base + roll_offset
```

When an ordinary task's predecessor completes, Roll applies that calculation
one final time and removes the child's `roll` and `roll_offset`. The child is
then independent, so later due-date edits remain in place. Existing links to
already-completed predecessors are released without changing the current due
date. Fixed milestones keep their links for slack calculation.

For example:

```text
A --2d--> B --4d--> C (checkpoint) --> D --> E (finish-line milestone)
```

Here B follows A by two days, and C follows B by four days. C can hold an
intermediate checkpoint; E can hold the chain's fixed finish-line milestone.

## Fields

### `roll`

Stores the UUID of this task's predecessor. An absent value leaves the task
outside the rolling chain.

```sh
task TASK_ID modify roll:PREDECESSOR_UUID
```

### `roll_offset`

Stores the calculated duration between a predecessor's base date and this
task's due date. When the linked task has both `remaining` and a matching
`roll.capacity.PROJECT`, Roll sets it to:

```text
remaining hours / weekly capacity * 7 calendar days
```

Saturday and Sunday count. The value is rounded to a 30-minute slot and recalculated
after task changes. Tasks without capacity or `remaining` keep their existing
manual offset.

### `roll_manual`

Set `roll_manual:yes` to keep a linked task's `roll_offset` from being replaced
by capacity auto-spacing. Roll still moves its due date from the predecessor,
so this is the right choice for a once-daily task:

```sh
task TASK_ID modify roll_manual:yes roll_offset:P1D
```

Clear the override with `task TASK_ID modify roll_manual:` to return to automatic
capacity spacing. When the predecessor completes and Roll releases the link, it
also clears `roll_manual`.

### `phoenix`

Set a positive duration to create one new task with the same description when
this task completes. The new task is due after the completion time. It keeps
the project and tags, but does not inherit `phoenix`, Roll fields, or tracking
session data.

```sh
task TASK_ID modify phoenix:PT90M
```

Completing `Laundry` with this field creates one new `Laundry` task due 90
minutes later. This keeps one future task instead of a pre-made chain.

### `roll_fixed`

Marks a due date as fixed instead of freely rolling it.

- `checkpoint` keeps an intermediate due date fixed while the chain continues.
- `finish-line` stores a fixed finish-line milestone for the chain.

The older values `yes`, `true`, `on`, and `fixed` remain accepted as aliases
for the same finish-line behavior. New configuration should use `checkpoint`
or `finish-line`, because those values state the intent.

Any other nonempty value is rejected with a warning, and Roll leaves that task
unchanged. This prevents a typo from turning a fixed milestone into a rolling
due date.

### `roll_slack`

On a fixed task, records the hours between its fixed due date and the fully
projected arrival. The projection follows the whole chain and treats earlier
fixed milestones as ordinary rolling tasks instead of schedule resets.

Positive slack means the projected chain arrives early; zero means it reaches
the fixed date; negative slack means it arrives late.

Treat `roll_slack` as Roll output. Do not use it as a substitute for
`roll_offset`.

### `r_mark`

Roll writes a compact display marker for Taskwarrior reports. `󰍃 +GAP` means an
ordinary rolling child, ` finish-line` marks a fixed finish-line deadline,
and `󰩈 checkpoint` marks a fixed checkpoint. It is output only; edit `roll`,
`roll_offset`, and `roll_fixed` instead.

## Fixed dates

A checkpoint is an intermediate boundary. Roll does not overwrite the
checkpoint's due date. A downstream task uses that fixed due date as its
ordinary predecessor base.

A finish-line (`roll_fixed:finish-line`) is the fixed milestone at the end of a
chain. Roll keeps that due date stable and uses `roll_slack` to expose whether
the fully projected chain arrives early or late. The literal Taskwarrior value
is:

```text
roll_fixed:finish-line
```

## Rock

Rock is the deadline-facing companion to Roll. Given an active finish-line,
`task rock FINISH_ID` walks its single active predecessor path backward and
prints the required root due date. By default it is a dry run; applying the
root-date command lets ordinary Roll schedule the rest of the chain forward.

`task rock FINISH_ID --apply` runs that root-date command when the plan has no
warnings. It refuses checkpoint, missing-link, completed-path, and branch
warnings instead of changing tasks.

Rock requires a complete active path. It stops at a checkpoint and refuses to
plan through completed or missing links. If the root feeds another active
branch, Rock warns before printing the command.

### Capacity cushion

Set weekly hours in `~/.taskrc`:

```ini
roll.capacity.posek=25
roll.capacity.posek.ch=8
```

The longest dotted project match wins, so `posek.ch_12` uses `posek=25` while
`posek.ch.section` uses `posek.ch=8`. `task chain` shows each finish-line
path with `CAPACITY` as:

```text
(calendar days from today through deadline, including Saturday and Sunday)
* weekly capacity / 7 - total path remaining
```

Positive hours are spare capacity; negative hours mean the path exceeds that
capacity. `—` means capacity or a `remaining` estimate is missing. This value
is read fresh on every view and does not move due dates or change `R_mark`.

## Errors and cycles

Roll refuses to guess when a schedule cannot be calculated safely. Typical
errors include a predecessor UUID that does not resolve, a predecessor without
the required `due` or `end` timestamp, an unusable predecessor status, a fixed
task without a due date, and an invalid or missing `roll_offset`.

Dependency cycles have no valid first task. Roll detects a cycle, reports the
affected tasks, and leaves their dates unchanged. Fix the dependency graph and
run Taskwarrior again.

## Example

```sh
task TASK_ID modify roll:PREDECESSOR_UUID roll_offset:2d
task CHECKPOINT_ID modify roll:PREDECESSOR_UUID roll_offset:4d roll_fixed:checkpoint
task FINISH_ID modify roll:PREDECESSOR_UUID roll_offset:2d roll_fixed:finish-line
```

After changing a predecessor's due date or completing it, run any Taskwarrior
command normally. The `on-exit` hook recalculates affected rolling tasks.

## Chain a selected list

Use `task FILTER _zshuuids`, then use `rg` to select tasks in desired chain
order. The saved text stays ordinary `UUID:description` lines; `.txt` and
`.md` both work.

```sh
task '/yv-/' _zshuuids | rg ':write yv-' > yv-chain.txt
task roll chain yv-chain.txt --start 2026-09-22 --finish 2026-10-07 --remaining 40m
```

`task roll chain` makes a flexible Roll path: every date, including the last,
can move. `task rock chain` accepts the same command but locks the last task at
`--finish` as its hard finish-line.

Chain spreads tasks across the requested weekdays, preserves normal Taskwarrior
`depends`, and writes `remaining` as estimated work left. It previews only.
`task roll chain` makes a flexible schedule; `task rock chain` makes the final
task a hard `roll_fixed:finish-line`.

`--remaining 40m` assigns one estimate to every selected task. Omit
`--remaining` to preserve each task's existing `remaining` value; Chain refuses
to apply when any selected task has no estimate and lists all missing tasks.

Several tasks may share a weekday when the list is longer than the available
weekdays. Chain keeps the first task on `--start` and the finish-line on
`--finish`. The root waits until the
start date. Existing final-task due time is preserved; absent a due time, Chain
uses `16:00Z`.

## Chain browser

`task chain` groups active Roll links into numbered root-to-leaf paths. It
shows root, task count, start, finish or leaf, deadline, capacity, and schedule
status. Rock finish-lines show capacity; flexible paths show `—`. Branches
appear as separate rows.

Run `task chain NUMBER` to show every task in one path. Use `task roll show`
only when you need the individual flexible Roll links and offsets. `task chains
view` remains the original compact summary, and `task roll view` remains an
alias for `task roll show`.
