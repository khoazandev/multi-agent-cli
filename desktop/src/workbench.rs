use serde::Deserialize;
use std::ffi::OsString;
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::sync::mpsc::{self, Receiver};

const DEFAULT_DISTRO: &str = "Ubuntu";
const LAUNCHER: &str = "./.local/bin/agy-herdr-projects";

#[derive(Clone)]
struct WslTarget {
    distro: String,
    user: Option<String>,
}

static WSL_TARGET: OnceLock<WslTarget> = OnceLock::new();

#[derive(Clone, Default, Deserialize)]
pub struct Snapshot {
    pub accounts: Vec<Account>,
    pub projects: Vec<Project>,
    pub server_running: bool,
    #[serde(default)]
    pub client_attached: bool,
}

#[derive(Clone, Deserialize)]
pub struct Account {
    pub name: String,
    pub signed_in: bool,
}

#[derive(Clone, Deserialize)]
pub struct Session {
    pub id: String,
    pub title: String,
    pub updated_at: String,
    #[serde(default)]
    pub current: bool,
}

#[derive(Clone, Deserialize)]
pub struct Project {
    pub id: String,
    pub name: String,
    pub repo: String,
    pub account: String,
    pub running: bool,
    #[serde(default)]
    pub storage: String,
    #[serde(default)]
    pub sessions: Vec<Session>,
}

pub enum Request {
    Refresh,
    Add {
        name: String,
        path: String,
        account: String,
    },
    Assign {
        project: String,
        account: String,
        close: bool,
    },
    Open(String),
    Resume {
        project: String,
        conversation: Option<String>,
        fresh: bool,
        close: bool,
    },
    Close(String),
    Remove {
        project: String,
        close: bool,
    },
}

impl Request {
    fn arguments(&self) -> Vec<OsString> {
        match self {
            Self::Refresh => vec!["status".into()],
            Self::Add {
                name,
                path,
                account,
            } => {
                vec![
                    "add".into(),
                    "--".into(),
                    name.into(),
                    path.into(),
                    account.into(),
                ]
            }
            Self::Assign {
                project,
                account,
                close,
            } => {
                let mut args = vec![OsString::from("assign")];
                if *close {
                    args.push("--close".into());
                }
                args.extend(["--".into(), project.into(), account.into()]);
                args
            }
            Self::Open(project) => vec!["focus".into(), "--".into(), project.into()],
            Self::Resume {
                project,
                conversation,
                fresh,
                close,
            } => {
                let mut args = vec![OsString::from("focus")];
                if let Some(id) = conversation {
                    args.extend(["--conversation".into(), id.into()]);
                }
                if *fresh {
                    args.push("--fresh".into());
                }
                if *close {
                    args.push("--close".into());
                }
                args.extend(["--".into(), project.into()]);
                args
            }
            Self::Close(project) => vec!["close".into(), "--".into(), project.into()],
            Self::Remove { project, close } => {
                let mut args = vec![OsString::from("remove")];
                if *close {
                    args.push("--close".into());
                }
                args.extend(["--".into(), project.into()]);
                args
            }
        }
    }

    pub fn send(self, context: eframe::egui::Context) -> Receiver<Result<Snapshot, String>> {
        let (send, receive) = mpsc::channel();
        std::thread::spawn(move || {
            let result = self.execute();
            let _ = send.send(result);
            context.request_repaint();
        });
        receive
    }

    fn execute(&self) -> Result<Snapshot, String> {
        let target = resolve_wsl_target();
        let mut command = wsl_command(&target);
        let output = command
            .args([LAUNCHER, "desktop"])
            .args(self.arguments())
            .output()
            .map_err(|error| format!("Không kết nối được Ubuntu WSL. {error}"))?;
        if !output.status.success() {
            let message = String::from_utf8_lossy(&output.stderr).replace('\0', "");
            let message = message.trim();
            return Err(if message.is_empty() {
                "Ubuntu WSL không trả về trạng thái. Kiểm tra bản cài Multi Agent CLI.".into()
            } else {
                message.to_owned()
            });
        }
        let snapshot: Snapshot = serde_json::from_slice(&output.stdout)
            .map_err(|error| format!("Không đọc được dữ liệu từ WSL. {error}"))?;
        if matches!(self, Self::Open(_) | Self::Resume { .. }) {
            open_terminal(snapshot.client_attached, &target)?;
        }
        Ok(snapshot)
    }
}

fn hidden_command(program: &str) -> Command {
    let mut command = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    command
}

fn wsl_command(target: &WslTarget) -> Command {
    let mut command = hidden_command("wsl.exe");
    command.args(["--distribution", &target.distro]);
    if let Some(user) = &target.user {
        command.args(["-u", user]);
    }
    command.args(["--cd", "~", "--exec"]);
    command
}

fn decode_wsl_output(bytes: &[u8]) -> String {
    if bytes.len() >= 2 && bytes.len() % 2 == 0 && (bytes.starts_with(&[0xFF, 0xFE]) || bytes.contains(&0)) {
        let units: Vec<u16> = bytes
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect();
        return String::from_utf16_lossy(&units)
            .trim_start_matches('\u{FEFF}')
            .to_owned();
    }
    String::from_utf8_lossy(bytes).into_owned()
}

fn env_override(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn resolve_wsl_target() -> WslTarget {
    if let Some(target) = WSL_TARGET.get() {
        return target.clone();
    }
    let explicit_distro = env_override("MULTI_AGENT_CLI_WSL_DISTRO");
    let explicit_user = env_override("MULTI_AGENT_CLI_WSL_USER");
    if let (Some(distro), Some(user)) = (&explicit_distro, &explicit_user) {
        let target = WslTarget {
            distro: distro.clone(),
            user: Some(user.clone()),
        };
        let _ = WSL_TARGET.set(target.clone());
        return target;
    }

    let mut distros = Vec::new();
    if let Some(distro) = &explicit_distro {
        distros.push(distro.clone());
    } else {
        distros.push(DEFAULT_DISTRO.to_owned());
        if let Ok(output) = hidden_command("wsl.exe").args(["-l", "-q"]).output() {
            for line in decode_wsl_output(&output.stdout).lines() {
                let candidate = line.replace('\0', "").trim().to_owned();
                if candidate.to_ascii_lowercase().starts_with("ubuntu") && !distros.contains(&candidate) {
                    distros.push(candidate);
                }
            }
        }
    }

    let users = match &explicit_user {
        Some(user) => vec![Some(user.clone())],
        None => vec![None, Some("agycli".to_owned())],
    };

    for distro in &distros {
        for user in &users {
            let candidate = WslTarget {
                distro: distro.clone(),
                user: user.clone(),
            };
            if wsl_command(&candidate)
                .args(["test", "-x", LAUNCHER])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .is_ok_and(|status| status.success())
            {
                let _ = WSL_TARGET.set(candidate.clone());
                return candidate;
            }
        }
    }

    WslTarget {
        distro: explicit_distro.unwrap_or_else(|| DEFAULT_DISTRO.to_owned()),
        user: explicit_user,
    }
}

fn open_terminal(client_attached: bool, target: &WslTarget) -> Result<(), String> {
    if client_attached {
        let _ = hidden_command("wt.exe")
            .args(["-w", "multi-agent-cli", "focus-tab", "-t", "0"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
        #[cfg(windows)]
        let _ = focus_terminal_window(true);
        return Ok(());
    }
    let mut command = Command::new("wt.exe");
    command.args([
        "-w",
        "multi-agent-cli",
        "new-tab",
        "--title",
        "Multi Agent CLI",
        "wsl.exe",
        "--distribution",
        &target.distro,
    ]);
    if let Some(user) = &target.user {
        command.args(["-u", user]);
    }
    command
        .args(["--cd", "~", "--exec", LAUNCHER, "--attach"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("Không mở được Windows Terminal. {error}"))?;
    Ok(())
}

#[cfg(windows)]
fn focus_terminal_window(allow_any_wt: bool) -> bool {
    use windows_sys::Win32::Foundation::{HWND, LPARAM};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetClassNameW, GetWindowTextW, IsIconic, IsWindowVisible, SW_RESTORE,
        SetForegroundWindow, ShowWindow,
    };
    use windows_sys::core::BOOL;

    struct Search {
        preferred: HWND,
        fallback: HWND,
    }

    unsafe extern "system" fn enumerate(hwnd: HWND, context: LPARAM) -> BOOL {
        let mut class = [0u16; 128];
        let mut title = [0u16; 256];
        // EnumWindows invokes this synchronously with a valid pointer to Search.
        unsafe {
            if IsWindowVisible(hwnd) == 0 {
                return 1;
            }
            let class_len = GetClassNameW(hwnd, class.as_mut_ptr(), class.len() as i32);
            let title_len = GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32);
            let is_wt =
                String::from_utf16_lossy(&class[..class_len.max(0) as usize]).contains("CASCADIA");
            if !is_wt {
                return 1;
            }
            let search = &mut *(context as *mut Search);
            let window_title = String::from_utf16_lossy(&title[..title_len.max(0) as usize]);
            let lower_title = window_title.to_ascii_lowercase();
            if window_title.contains("Multi Agent CLI")
                || window_title.contains("Antigravity Projects")
                || lower_title.contains("herdr")
            {
                search.preferred = hwnd;
                return 0;
            }
            if search.fallback.is_null() {
                search.fallback = hwnd;
            }
        }
        1
    }

    let mut search = Search {
        preferred: std::ptr::null_mut(),
        fallback: std::ptr::null_mut(),
    };
    unsafe {
        EnumWindows(Some(enumerate), (&mut search as *mut Search) as LPARAM);
        let terminal = if !search.preferred.is_null() {
            search.preferred
        } else if allow_any_wt {
            search.fallback
        } else {
            std::ptr::null_mut()
        };
        if terminal.is_null() {
            return false;
        }
        if IsIconic(terminal) != 0 {
            ShowWindow(terminal, SW_RESTORE);
        }
        SetForegroundWindow(terminal);
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dropped_windows_path_is_one_argument() {
        let request = Request::Add {
            name: "My repo".into(),
            path: r"C:\Projects\repo & notes".into(),
            account: "acc-a".into(),
        };
        let args = request.arguments();
        assert_eq!(args.len(), 5);
        assert_eq!(args[3], r"C:\Projects\repo & notes");
    }

    #[test]
    fn close_and_remove_arguments_preserve_project_id_and_flags() {
        assert_eq!(
            Request::Close("project-a".into()).arguments(),
            vec![OsString::from("close"), "--".into(), "project-a".into()]
        );
        assert_eq!(
            Request::Remove {
                project: "project-a".into(),
                close: true,
            }
            .arguments(),
            vec![
                OsString::from("remove"),
                "--close".into(),
                "--".into(),
                "project-a".into(),
            ]
        );
        assert_eq!(
            Request::Assign {
                project: "project-a".into(),
                account: "acc-b".into(),
                close: false,
            }
            .arguments(),
            vec![
                OsString::from("assign"),
                "--".into(),
                "project-a".into(),
                "acc-b".into(),
            ]
        );
    }

    #[test]
    fn decode_wsl_output_handles_utf16_le_with_bom() {
        let utf16: Vec<u8> = std::iter::once(0xFEFFu16)
            .chain("Ubuntu\r\nUbuntu-24.04\r\n".encode_utf16())
            .flat_map(|unit| unit.to_le_bytes())
            .collect();
        let decoded = decode_wsl_output(&utf16);
        assert!(decoded.contains("Ubuntu-24.04"));
        assert!(!decoded.contains('\u{FEFF}'));
    }

    #[test]
    fn resume_arguments_encode_conversation_fresh_and_close() {
        assert_eq!(
            Request::Resume {
                project: "project-a".into(),
                conversation: Some("conv-123".into()),
                fresh: false,
                close: true,
            }
            .arguments(),
            vec![
                OsString::from("focus"),
                "--conversation".into(),
                "conv-123".into(),
                "--close".into(),
                "--".into(),
                "project-a".into(),
            ]
        );
        assert_eq!(
            Request::Resume {
                project: "project-a".into(),
                conversation: None,
                fresh: true,
                close: false,
            }
            .arguments(),
            vec![
                OsString::from("focus"),
                "--fresh".into(),
                "--".into(),
                "project-a".into(),
            ]
        );
    }
}
