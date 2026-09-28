# FAQ

**Does cubby ever delete my files?**
No. It moves files within the watched folder. The only deletion is opt-in
`dedupe`, which removes a download that is byte-for-byte identical to one cubby
already filed. Use `cubby undo` to reverse the last run.

**What happens to a file it can't classify?**
It goes to `_Unsorted` (configurable). Nothing is left messy at the root and
nothing is lost.

**Will it grab a file that is still downloading?**
No. In-progress extensions (`.crdownload`, `.part`, ...) are skipped, and a file
must sit still for `--delay` (1 minute by default) before it is moved.

**Why did a file with a weird name still get sorted correctly?**
The content stage: when the filename carries no signal, cubby reads the text
inside (PDF, docx, ...) and routes on that. Run `cubby plan` and look for the
`<- content` annotation.

**Does it work on Linux?**
Yes. The background agent uses launchd on macOS and systemd on Linux. The sorter
itself is pure Python and platform-independent.

**How do I see what the agent has been doing?**
`cubby status` shows the last run and the last lines of the log, and
`cubby log --warnings` (or `--run <id>`) reads further back. The log itself
is JSON lines, one per event, in `~/Library/Logs/cubby.log` on macOS and
`~/.local/state/cubby/cubby.log` elsewhere (with the default state folder;
`cubby doctor` prints where it is):
`jq -r 'select(.level != "INFO") | "\(.ts) \(.msg)"' ~/.local/state/cubby/cubby.log`
lists only what went wrong. Each line also names the cubby `version` that wrote it
and, during a pass, its `run` id: the same id as in `cubby history` and
`cubby undo --run`, so `jq 'select(.run == "<id>")'` shows everything one pass did.

**How do I change the categories?**
Copy `examples/personal.example.toml` to `~/.config/cubby/config.toml` and edit.
See [configuration.md](configuration.md).

**It put something in the wrong place. What now?**
`cubby undo` to revert, then add a `name_patterns` or `content_patterns` entry to
the right category (specific categories first) and re-run `cubby plan` to verify.
