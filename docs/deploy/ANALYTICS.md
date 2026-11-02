# Setting up the analytics node

The analytics node (macOS) runs SPARQ's studies from a clone of the repository under the agent account, with `uv` installed for that account (`~/.local/bin/uv`). This guide covers what the node needs beyond the clone. Nothing here holds a Kalshi key or a database password; those settings are described where their commands are.

## The scratch settings (S1.2r)

The whole-history passes spill tens of gigabytes, and the node's internal disk is small. Every analytics command line reads `~/.config/sparq/analytics.toml` (or the file named in `SPARQ_ANALYTICS_CONFIG`) first and sends its temporary files and polars' spills to the data drive:

```toml
# ~/.config/sparq/analytics.toml (the agent account; mode 600)
scratch = "/Volumes/<data-drive>/<folder>/tmp"   # absolute, existing and writable
spill_budget_mb = 60000                             # polars' spill cap, in megabytes
```

1. Create the folder on the data drive, as the agent account: `mkdir -p "/Volumes/<data-drive>/<folder>/tmp"`.
2. Write the file above with the real path, then `chmod 600 ~/.config/sparq/analytics.toml`.
3. Check it: `uv run python -c "from sparq_analytics.scratch import configure; print(configure())"` from the clone prints the four variables it sets (`TMPDIR`, `POLARS_TEMP_DIR`, `POLARS_OOC_SPILL_DIR`, `POLARS_OOC_DISK_BUDGET_MB`).

**The file wins over the environment.** macOS sets `TMPDIR` to the internal disk in every Terminal and `launchd` session, so an inherited value is not taken as a choice; to run with other folders, point `SPARQ_ANALYTICS_CONFIG` at another file.

**What a refusal means.** A command stops with `error: ...` and exit code 1, before reading any data, when the file named in `SPARQ_ANALYTICS_CONFIG` does not exist, the file cannot be read or is not valid TOML, the scratch folder is not an absolute, existing folder the account can write, the budget is not a positive whole number, or the file holds a setting it does not know. With no file at the default path nothing changes, which is right for the workstation and wrong for the node: after a rebuild, step 2 comes before the first big run.
