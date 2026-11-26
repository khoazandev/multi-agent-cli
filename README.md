# Multi Agent CLI

Run several Google Antigravity CLI sessions side by side. The **Accounts & Projects** tab groups repositories by account. The same account can run multiple projects at once. Every project and account pair keeps its own conversation history and brain directory. Herdr shows up to four projects per tab in one terminal window. Profiles can read skills from a shared directory.

## Windows desktop app

The native Rust app shows the number of configured accounts with a locally saved login, plus the running state of each project. Drag a folder from Explorer to register a repo. Drag a project name onto an account to change its assignment. Changing the account of a running project requires confirmation before closing its pane. **Mở CLI** focuses the project's pane in Windows Terminal.

Install or update the Ubuntu WSL workbench first. The desktop app uses its existing projects and Herdr session.

```powershell
cargo build --release --locked --manifest-path desktop/Cargo.toml
.\desktop\target\release\multi-agent-cli-desktop.exe
```

Build on Windows with a Rust toolchain and a compatible Windows linker. **Open Workbench.cmd** uses the desktop app when the compiled executable is present. The login count shows locally saved sign-ins and refreshes automatically while the app is open.

## Install on Ubuntu WSL

Use Ubuntu on x86_64 with `curl`, `git`, `python3`, `sha256sum`, and `tar`. For the Windows launcher, install Windows Terminal and use an Ubuntu distro named `Ubuntu`.

```bash
git clone https://github.com/khoazandev/multi-agent-cli.git
cd multi-agent-cli
bash install.sh
```

The installer checks Herdr against a pinned SHA-256 hash and builds the workbench copy of `agys` from source. If needed it downloads a checksum-verified Go toolchain and obtains `agy` from Google's official installer. Workbench binaries are installed under `~/.local/lib/multi-agent-cli`.

## Upgrade an existing workbench

Run `git pull --ff-only` and then `bash install.sh`. The installer keeps existing projects and profile directories. If the original Herdr session is still running, the launcher attaches to it. Save your work and exit its agents before stopping it with `herdr --session agy-projects server stop`. Open the launcher again to start the upgraded session. The new session imports the previous project count, uses its own Herdr configuration, and reuses project and account directories.

## Open or add projects

Double-click [Open Workbench.cmd](Open%20Workbench.cmd) from Windows, or run this from Ubuntu:

```bash
~/.local/bin/agy-herdr-projects --manage
```

- The first launch defaults to three projects. Use the **Accounts & Projects** tab to add existing repositories, assign accounts, and open their panes. The `--choose` option still opens the project-count prompt.
- To start with a different number, run `~/.local/bin/agy-herdr-projects --count 2` on an initial setup.
- Put each Git checkout in its matching starter directory under `~/projects`, such as `~/projects/agy-a`. Sign in to the intended Google account in each pane.
- Put shared skills under `~/.local/share/agy-shared/skills/<skill-name>/SKILL.md`. The workbench adds that path to each profile's skills manifest and keeps its other entries.
- To add projects to an already open window, run `~/.local/bin/agy-herdr-projects --count 8 --prepare` from a separate Ubuntu shell. New projects open in new tabs so running panes stay in place.

To give every new CLI session an additional workspace folder, run `agy-herdr-projects --share-dir /mnt/c` in Ubuntu. Use `--shared-dirs` to list configured folders and `--unshare-dir /mnt/c` to remove that entry. For an already open CLI, `/add-dir /mnt/c` adds the folder to that session.

## Register existing repositories and switch accounts

Sign in to Google in the first project's CLI pane. Then add a second repository using the same account name in the manager, or run:

```bash
agy-herdr-projects --add api --repo ~/src/api --profile acc-a --prepare
```

This example shares `acc-a` with Project A. When a project opens with the same account and its own profile has no credentials yet, the workbench initializes it using the saved local Antigravity CLI OAuth login. Each project keeps its own brain, settings, and history. If both panes were open at the login prompt before you signed in, close and reopen the second pane from the manager. A session that Google expires or revokes may require signing in again.

```bash
agy-herdr-projects --add payments --name Payments --repo ~/src/payments \
  --profile payments-main --profile payments-backup --prepare
agy-herdr-projects --list
```

The first profile is active by default. To add another allowed account to an existing project, run `agy-herdr-projects --allow payments payments-spare --prepare`. To switch, exit the CLI or close the pane for that project, then run `agy-herdr-projects --use payments payments-backup --prepare`. Use `--open payments --prepare`, `--close payments`, or `--remove payments --close-current-pane` to manage individual project panes, and `--attach` to attach a Herdr client without reopening closed panes. Each project and account pair keeps its conversation history. Use one account name per Google login and reuse it only for projects that should share that login. Existing entries are stored in `~/.local/state/agy-herdr-projects/projects.json`.

The workbench automatically resumes the conversation last reported by its hook for the selected project and account. Use `agy-herdr-projects --history <id>` (or the **Lịch sử** button in the desktop app) to list all saved conversations for a project and reopen any previous conversation with `agy-herdr-projects --open <id> --conversation <conv-id> --close-current-pane`. Workbench resume checks account ownership and the exact repository, and starts new conversations for profiles with no saved pointer. If a saved conversation was deleted or lacks usable metadata, run `agy-herdr-projects --fresh --prepare` to start a new conversation in the missing pane. Use `agy-herdr-projects --stop` after saving your work if you want to stop the upgraded server.

`agys` stores Google credentials on disk. Multi Agent CLI launches sessions with Always Accept (`--dangerously-skip-permissions`) enabled by default and trusts each project's repository (plus configured shared folders) inside its isolated profile `settings.json`; set `MULTI_AGENT_CLI_ASK_PERMISSIONS=1` or pass `--sandbox` to require interactive permission prompts. The workbench keeps new trust changes within each profile and turns off background updates for its copy of `agys`. Trust entries from an earlier installation are preserved for review. Profiles in the same Linux user account separate local state. Code with that user's filesystem permissions can still access the other profiles and edit shared skills.

## Upstream source and licenses

This independent repository vendors Herdr and a patched copy of `agys`. The launcher runs them as separate executables and uses the Antigravity CLI distributed by Google.

| Component | Snapshot | License |
| --- | --- | --- |
| [Herdr](https://github.com/herdrdev/herdr/tree/v0.9.5) | `v0.9.5` (`2ecf19a92fc3a7fff75b6ce3655ef04f4625ec01`) in [`upstream/herdr`](upstream/herdr) | [Apache-2.0](upstream/herdr/LICENSE), plus bundled notices |
| [`agys`](https://github.com/quaywin/agys/tree/v0.2.40) | Based on `v0.2.40` (`0b7041420839316938e30480575a8c1c91848689`), with workbench fixes in [`upstream/agys`](upstream/agys) | [MIT](upstream/agys/LICENSE) |

Workbench scripts and documentation are covered by the root [Apache-2.0 license](LICENSE). Each upstream component keeps its original license and notices.
