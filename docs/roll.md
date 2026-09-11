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

Sets the Taskwarrior duration between a predecessor's base date and this
task's due date. Use ordinary Taskwarrior duration values such as `2d`, `4d`,
or `1w`.

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
