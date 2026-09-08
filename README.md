# Taskwarrior Roll + Crunch

Two dependency-free Taskwarrior hooks for keeping linked work scheduled:

- **Roll** moves dependent due dates when their predecessor moves or finishes.
- **Crunch** reports scheduling pressure without changing task dates.
- Fixed checkpoints and finish-line milestones keep important dates stable.
- Project capacity settings support informational longest dotted-prefix lookup.

## Requirements

- Python 3.10+
- Taskwarrior

No Python packages are required.

## Install

Review the repository, then run:

```sh
./install.sh
```

The installer does not need administrator access. It uses `$HOME/.task` by
default and refuses to replace any existing hook with the same name. For a
custom data directory:

```sh
TASKDATA=/path/to/task-data ./install.sh
```

### Manual install

Roll and Crunch can be installed independently.

Roll only:

```sh
install -m 0755 hooks/roll.py "$HOME/.task/hooks/on-exit-roll.py"
```

Crunch only:

```sh
install -m 0755 hooks/crunch.py "$HOME/.task/hooks/on-modify.crunch.py"
ln -s on-modify.crunch.py "$HOME/.task/hooks/on-add.crunch.py"
```

Then either include the supplied configuration from `.taskrc`:

```text
include /absolute/path/to/taskwarrior-roll-crunch/config/taskrc.example
```

or copy the needed UDA settings from that file into `.taskrc`. When
Taskwarrior uses a non-default data directory, install hooks in its `hooks`
subdirectory instead of `$HOME/.task/hooks`.

## Usage

Point each rolling task at its predecessor and set the spacing with
`roll_offset`:

```sh
task TASK_ID modify roll:PREDECESSOR_UUID roll_offset:2d
task CHECKPOINT_ID modify roll:PREDECESSOR_UUID roll_offset:4d roll_fixed:checkpoint
task FINISH_ID modify roll:PREDECESSOR_UUID roll_offset:2d roll_fixed:finish-line
```

`checkpoint` preserves an intermediate date. `roll_fixed:finish-line` marks a
fixed finish-line milestone. A chain can be pictured as:

```text
A --2d--> B --4d--> C (checkpoint) --> D --> E (finish-line milestone)
```

Roll uses a completed predecessor's `end` time; otherwise it uses the
predecessor's `due` time. See [docs/roll.md](docs/roll.md) for field semantics,
fixed dates, errors, and cycle handling.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).
