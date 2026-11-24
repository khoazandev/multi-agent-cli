# Antigravity Herdr Workbench

Run several Google Antigravity CLI sessions side by side. Each project gets its own working directory, `agys` account profile, conversation history, and brain directory. Herdr shows up to four projects per tab in one persistent terminal window. Profiles can read skills from a shared directory.

## Install on Ubuntu WSL

Use Ubuntu on x86_64 with `curl`, `git`, `python3`, `sha256sum`, and `tar`. For the Windows launcher, install Windows Terminal and use an Ubuntu distro named `Ubuntu`.

```bash
git clone https://github.com/khoazandev/antigravity-herdr-workbench.git
cd antigravity-herdr-workbench
bash install.sh
```

For missing tools, the installer checks the Herdr and `agys` release archives against pinned SHA-256 hashes and obtains `agy` from Google's official installer. Existing binaries and a registered `agys` plugin are reused.

## Open or add projects

Double-click [Open Workbench.cmd](Open%20Workbench.cmd) from Windows, or run this from Ubuntu:

```bash
~/.local/bin/agy-herdr-projects --choose
```

- The first launch defaults to three projects. Enter a larger total to add projects, or press Enter to reopen the current layout.
- Put each Git checkout in its matching starter directory under `~/projects`, such as `~/projects/agy-a`. Sign in to the intended Google account in each pane.
- Put shared skills under `~/.local/share/agy-shared/skills/<skill-name>/SKILL.md`. Each created profile has a manifest that points here.
- To add projects to an already open window, run `~/.local/bin/agy-herdr-projects --count 8 --prepare` from a separate Ubuntu shell. Running panes stay in place.

`agys` stores Google credentials on disk. Profiles in the same Linux user account separate local state. Code with that user's filesystem permissions can still access the other profiles.

## Upstream source and licenses

This independent repository includes exact upstream source snapshots. The launcher runs the two tools as separate executables and uses the Antigravity CLI distributed by Google.

| Component | Snapshot | License |
| --- | --- | --- |
| [Herdr](https://github.com/herdrdev/herdr/tree/v0.9.5) | `v0.9.5` (`2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`) in [`upstream/herdr`](upstream/herdr) | [Apache-2.0](upstream/herdr/LICENSE), plus bundled notices |
| [`agys`](https://github.com/quaywin/agys/tree/v0.2.40) | `v0.2.40` (`0b7041420839316938e30480575a8c1c91848689`) in [`upstream/agys`](upstream/agys) | [MIT](upstream/agys/LICENSE) |

Workbench scripts and documentation are covered by the root [Apache-2.0 license](LICENSE). Each upstream component keeps its original license and notices.
