#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

mod workbench;

use eframe::egui::{
    self, Align, Color32, FontData, FontDefinitions, FontFamily, Frame, Layout, RichText, Stroke,
};
use std::path::PathBuf;
use std::sync::mpsc::{Receiver, TryRecvError};
use std::time::{Duration, Instant};
use workbench::{Project, Request, Snapshot};

const PAPER: Color32 = Color32::from_rgb(244, 247, 249);
const INK: Color32 = Color32::from_rgb(20, 35, 45);
const MUTED: Color32 = Color32::from_rgb(75, 94, 106);
const PANEL: Color32 = Color32::from_rgb(15, 29, 40);
const LIGHT: Color32 = Color32::from_rgb(238, 245, 249);
const SUBTLE: Color32 = Color32::from_rgb(176, 194, 207);
const ACCENT: Color32 = Color32::from_rgb(0, 110, 103);
const ACTIVE: Color32 = Color32::from_rgb(116, 223, 182);
const BORDER: Color32 = Color32::from_rgb(211, 221, 226);

fn main() -> eframe::Result {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([1080.0, 700.0])
            .with_min_inner_size([820.0, 540.0])
            .with_drag_and_drop(true),
        ..Default::default()
    };
    eframe::run_native(
        "Multi Agent CLI",
        options,
        Box::new(|cc| {
            configure(&cc.egui_ctx);
            Ok(Box::new(Desktop::new(&cc.egui_ctx)))
        }),
    )
}

fn configure(ctx: &egui::Context) {
    let mut visuals = egui::Visuals::light();
    visuals.selection.bg_fill = Color32::from_rgb(191, 231, 224);
    visuals.widgets.active.fg_stroke.color = INK;
    visuals.override_text_color = Some(INK);
    ctx.set_visuals(visuals);
    let mut style = (*ctx.style()).clone();
    style.spacing.item_spacing = egui::vec2(8.0, 8.0);
    style.spacing.button_padding = egui::vec2(11.0, 6.0);
    ctx.set_style(style);

    #[cfg(windows)]
    if let Some(windows) = std::env::var_os("WINDIR") {
        let path = PathBuf::from(windows).join("Fonts").join("segoeui.ttf");
        if let Ok(data) = std::fs::read(path) {
            let mut fonts = FontDefinitions::default();
            fonts
                .font_data
                .insert("Segoe UI".into(), FontData::from_owned(data).into());
            fonts
                .families
                .entry(FontFamily::Proportional)
                .or_default()
                .insert(0, "Segoe UI".into());
            ctx.set_fonts(fonts);
        }
    }
}

struct Desktop {
    data: Snapshot,
    selected: Option<String>,
    pending: Option<Receiver<Result<Snapshot, String>>>,
    next_refresh: Instant,
    error: Option<String>,
    dialog: Option<Dialog>,
    loaded: bool,
}

enum Dialog {
    Add {
        path: String,
        name: String,
        account: String,
    },
    Shift {
        project: Project,
        account: String,
    },
    Remove {
        project: Project,
    },
    History {
        project: Project,
    },
}

impl Desktop {
    fn new(ctx: &egui::Context) -> Self {
        Self {
            data: Snapshot::default(),
            selected: None,
            pending: Some(Request::Refresh.send(ctx.clone())),
            next_refresh: Instant::now(),
            error: None,
            dialog: None,
            loaded: false,
        }
    }

    fn send(&mut self, request: Request, ctx: &egui::Context) {
        if self.pending.is_none() {
            self.error = None;
            self.pending = Some(request.send(ctx.clone()));
        }
    }

    fn poll(&mut self, ctx: &egui::Context) {
        if let Some(receiver) = &self.pending {
            let result = match receiver.try_recv() {
                Ok(result) => Some(result),
                Err(TryRecvError::Empty) => None,
                Err(TryRecvError::Disconnected) => Some(Err("Mất kết nối với WSL.".into())),
            };
            if let Some(result) = result {
                self.pending = None;
                self.next_refresh = Instant::now() + Duration::from_secs(5);
                match result {
                    Ok(snapshot) => {
                        self.data = snapshot;
                        self.loaded = true;
                    }
                    Err(error) => self.error = Some(error),
                }
            }
        }
        if self.pending.is_none() && Instant::now() >= self.next_refresh && self.dialog.is_none() {
            self.pending = Some(Request::Refresh.send(ctx.clone()));
            self.next_refresh = Instant::now() + Duration::from_secs(5);
        }
        if self.pending.is_none() && self.dialog.is_none() {
            let wait = self.next_refresh.saturating_duration_since(Instant::now());
            ctx.request_repaint_after(wait.max(Duration::from_millis(250)));
        }
    }

    fn add_dialog(&mut self, path: Option<PathBuf>) {
        let name = path
            .as_ref()
            .and_then(|path| path.file_name())
            .map(|part| part.to_string_lossy().into_owned())
            .unwrap_or_default();
        self.dialog = Some(Dialog::Add {
            path: path
                .map(|path| path.to_string_lossy().into_owned())
                .unwrap_or_default(),
            name,
            account: self.selected.clone().unwrap_or_default(),
        });
    }

    fn shift_dialog(&mut self, project: &Project, account: Option<String>) {
        if account.as_deref() != Some(project.account.as_str()) {
            self.dialog = Some(Dialog::Shift {
                account: account.unwrap_or_else(|| project.account.clone()),
                project: project.clone(),
            });
        }
    }

    fn remove_dialog(&mut self, project: &Project) {
        self.dialog = Some(Dialog::Remove {
            project: project.clone(),
        });
    }

    fn history_dialog(&mut self, project: &Project) {
        self.dialog = Some(Dialog::History {
            project: project.clone(),
        });
    }

    fn sidebar(&mut self, ctx: &egui::Context) {
        let connected = self
            .data
            .accounts
            .iter()
            .filter(|account| account.signed_in)
            .count();
        let mut dropped = None;
        egui::SidePanel::left("accounts")
            .resizable(false)
            .exact_width(258.0)
            .frame(Frame::new().fill(PANEL).inner_margin(18.0))
            .show(ctx, |ui| {
                ui.add_space(5.0);
                ui.label(
                    RichText::new("Multi Agent CLI")
                        .color(LIGHT)
                        .size(24.0)
                        .strong(),
                );
                ui.add_space(21.0);
                ui.label(RichText::new("Tài khoản").color(SUBTLE).size(15.0));
                ui.horizontal(|ui| {
                    ui.label(
                        RichText::new(if self.loaded {
                            connected.to_string()
                        } else {
                            "—".into()
                        })
                        .color(ACTIVE)
                        .size(41.0)
                        .strong(),
                    );
                    ui.label(
                        RichText::new(format!("/ {} đã lưu đăng nhập", self.data.accounts.len()))
                            .color(LIGHT)
                            .size(15.0),
                    );
                });
                ui.add_space(11.0);
                if ui
                    .add(
                        egui::Button::new(RichText::new("Tất cả dự án").color(LIGHT))
                            .fill(if self.selected.is_none() {
                                ACCENT
                            } else {
                                Color32::from_rgb(27, 47, 61)
                            })
                            .min_size(egui::vec2(ui.available_width(), 34.0)),
                    )
                    .clicked()
                {
                    self.selected = None;
                }
                ui.add_space(7.0);
                egui::ScrollArea::vertical().show(ui, |ui| {
                    for account in &self.data.accounts {
                        let selected = self.selected.as_deref() == Some(&account.name);
                        let fill = if selected {
                            ACCENT
                        } else {
                            Color32::from_rgb(27, 47, 61)
                        };
                        let frame = Frame::new().fill(fill).inner_margin(8.0).corner_radius(7.0);
                        let (zone, payload) = ui.dnd_drop_zone::<String, _>(frame, |ui| {
                            ui.set_min_width(ui.available_width());
                            let response = ui.add(
                                egui::Label::new(
                                    RichText::new(&account.name)
                                        .color(LIGHT)
                                        .size(16.0)
                                        .strong(),
                                )
                                .sense(egui::Sense::click()),
                            );
                            if response.clicked() {
                                self.selected = Some(account.name.clone());
                            }
                            let (label, tint) = if account.signed_in {
                                ("●  Đã lưu đăng nhập", ACTIVE)
                            } else {
                                ("○  Chưa lưu đăng nhập", SUBTLE)
                            };
                            ui.label(RichText::new(label).color(tint).size(13.0));
                        });
                        if zone.response.clicked() {
                            self.selected = Some(account.name.clone());
                        }
                        if let Some(project) = payload {
                            dropped = Some((project.as_ref().clone(), account.name.clone()));
                        }
                    }
                });
            });
        if let Some((id, account)) = dropped {
            if let Some(project) = self
                .data
                .projects
                .iter()
                .find(|project| project.id == id)
                .cloned()
            {
                self.shift_dialog(&project, Some(account));
            }
        }
    }

    fn projects(&mut self, ctx: &egui::Context) {
        let mut open = None;
        let mut close_pane = None;
        let mut history = None;
        let mut shift = None;
        let mut remove = None;
        let mut add = false;
        let mut refresh = false;
        egui::CentralPanel::default().frame(Frame::new().fill(PAPER).inner_margin(22.0)).show(ctx, |ui| {
            ui.horizontal(|ui| {
                ui.vertical(|ui| {
                    ui.label(RichText::new("Dự án").size(29.0).strong());
                    let running = self.data.projects.iter().filter(|project| project.running).count();
                    let status = if !self.loaded {
                        "Đang kết nối Ubuntu WSL".into()
                    } else if self.data.server_running {
                        format!("{running} đang chạy · {} dự án", self.data.projects.len())
                    } else {
                        format!("Phiên CLI đang tắt · {} dự án", self.data.projects.len())
                    };
                    ui.label(RichText::new(status).color(MUTED).size(14.0));
                });
                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                    add = ui.button("＋ Thêm dự án").clicked();
                    refresh = ui.add_enabled(self.pending.is_none(), egui::Button::new("Làm mới")).clicked();
                });
            });
            ui.add_space(12.0);
            let hovering = ctx.input(|input| !input.raw.hovered_files.is_empty());
            Frame::new().fill(if hovering { Color32::from_rgb(210, 242, 233) } else { Color32::WHITE })
                .stroke(Stroke::new(1.0_f32, if hovering { ACCENT } else { BORDER }))
                .corner_radius(10.0).inner_margin(15.0).show(ui, |ui| {
                    ui.set_min_width(ui.available_width());
                    ui.label(RichText::new("Thả thư mục vào đây").color(ACCENT).strong().size(16.0));
                    ui.label(RichText::new("Kéo từ Explorer để thêm repo. Kéo tên dự án sang tài khoản bên trái để đổi.").color(MUTED).size(13.0));
                });
            if let Some(error) = &self.error {
                ui.label(RichText::new(error).color(Color32::from_rgb(160, 44, 32)));
            }
            ui.add_space(12.0);
            egui::ScrollArea::vertical().show(ui, |ui| {
                let mut count = 0;
                for project in &self.data.projects {
                    if self.selected.as_ref().is_some_and(|account| account != &project.account) {
                        continue;
                    }
                    count += 1;
                    Frame::new().fill(Color32::WHITE).stroke(Stroke::new(1.0_f32, BORDER))
                        .corner_radius(9.0).inner_margin(12.0).show(ui, |ui| {
                            ui.set_min_width(ui.available_width());
                            ui.horizontal(|ui| {
                                ui.dnd_drag_source(egui::Id::new(("project", &project.id)), project.id.clone(), |ui| {
                                    ui.label(RichText::new(format!("⠿  {}", project.name)).size(17.0).strong());
                                });
                                ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                    if ui.add_enabled(self.pending.is_none(), egui::Button::new("Mở CLI")).clicked() {
                                        open = Some(project.id.clone());
                                    }
                                    if project.running
                                        && ui.add_enabled(self.pending.is_none(), egui::Button::new("Đóng CLI")).clicked()
                                    {
                                        close_pane = Some(project.id.clone());
                                    }
                                    let history_label = if project.sessions.is_empty() {
                                        "Lịch sử".into()
                                    } else {
                                        format!("Lịch sử ({})", project.sessions.len())
                                    };
                                    if ui.button(history_label).clicked() {
                                        history = Some(project.clone());
                                    }
                                    if ui.button("Đổi tài khoản").clicked() {
                                        shift = Some(project.clone());
                                    }
                                    if ui.button("Gỡ").clicked() {
                                        remove = Some(project.clone());
                                    }
                                });
                            });
                            ui.label(RichText::new(&project.repo).color(MUTED).size(12.0));
                            if let Some(latest) = project.sessions.iter().find(|s| s.current).or_else(|| project.sessions.first()) {
                                let stamp = if latest.updated_at.is_empty() {
                                    String::new()
                                } else {
                                    format!(" · {}", latest.updated_at)
                                };
                                ui.label(
                                    RichText::new(format!("Gần nhất: {}{}", latest.title, stamp))
                                        .color(MUTED)
                                        .size(12.5),
                                );
                            }
                            ui.horizontal(|ui| {
                                ui.label(RichText::new(&project.account).color(ACCENT).strong());
                                ui.label(RichText::new(if project.running { "● Đang chạy" } else { "○ Đang đóng" })
                                    .color(if project.running { ACCENT } else { MUTED }).size(13.0));
                            });
                        });
                }
                if count == 0 && self.loaded {
                    ui.label(RichText::new("Chưa có dự án trong danh sách này.").color(MUTED));
                }
            });
        });
        if add {
            self.add_dialog(None);
        }
        if refresh {
            self.send(Request::Refresh, ctx);
        }
        if let Some(project) = open {
            self.send(Request::Open(project), ctx);
        }
        if let Some(project) = close_pane {
            self.send(Request::Close(project), ctx);
        }
        if let Some(project) = history {
            self.history_dialog(&project);
        }
        if let Some(project) = shift {
            self.shift_dialog(&project, None);
        }
        if let Some(project) = remove {
            self.remove_dialog(&project);
        }
    }

    fn show_dialog(&mut self, ctx: &egui::Context) {
        let Some(mut dialog) = self.dialog.take() else {
            return;
        };
        let (title, width) = match &dialog {
            Dialog::Add { .. } => ("Thêm dự án", 440.0),
            Dialog::Shift { .. } => ("Đổi tài khoản", 440.0),
            Dialog::Remove { .. } => ("Gỡ dự án", 440.0),
            Dialog::History { .. } => ("Lịch sử hội thoại", 560.0),
        };
        let mut shown = true;
        let mut close = false;
        let mut request = None;
        egui::Window::new(title)
            .resizable(false)
            .collapsible(false)
            .open(&mut shown)
            .anchor(egui::Align2::CENTER_CENTER, egui::Vec2::ZERO)
            .default_width(width)
            .show(ctx, |ui| match &mut dialog {
                Dialog::Add {
                    path,
                    name,
                    account,
                } => {
                    ui.label("Thư mục repo");
                    ui.horizontal(|ui| {
                        ui.add(
                            egui::TextEdit::singleline(path)
                                .desired_width(330.0)
                                .hint_text("C:\\… hoặc /home/…"),
                        );
                        if ui.button("Chọn…").clicked() {
                            if let Some(folder) = rfd::FileDialog::new().pick_folder() {
                                *path = folder.to_string_lossy().into_owned();
                                if name.is_empty() {
                                    *name = folder
                                        .file_name()
                                        .unwrap_or_default()
                                        .to_string_lossy()
                                        .into_owned();
                                }
                            }
                        }
                    });
                    ui.label("Tên hiển thị");
                    ui.add(egui::TextEdit::singleline(name).desired_width(330.0));
                    ui.label("Tài khoản");
                    ui.horizontal(|ui| {
                        ui.add(
                            egui::TextEdit::singleline(account)
                                .desired_width(215.0)
                                .hint_text("Ví dụ acc-a"),
                        );
                        self.account_picker(ui, "add-accounts", account, "Chọn tài khoản");
                    });
                    ui.add_space(7.0);
                    ui.horizontal(|ui| {
                        let enabled = !path.trim().is_empty()
                            && !name.trim().is_empty()
                            && !account.trim().is_empty()
                            && self.pending.is_none();
                        if ui
                            .add_enabled(enabled, egui::Button::new("Thêm dự án"))
                            .clicked()
                        {
                            request = Some(Request::Add {
                                path: path.trim().into(),
                                name: name.trim().into(),
                                account: account.trim().into(),
                            });
                        }
                        close = ui.button("Hủy").clicked();
                    });
                }
                Dialog::Shift { project, account } => {
                    ui.label(RichText::new(&project.name).strong().size(18.0));
                    ui.label("Tài khoản");
                    ui.horizontal(|ui| {
                        ui.add(
                            egui::TextEdit::singleline(account)
                                .desired_width(215.0)
                                .hint_text("Ví dụ acc-b"),
                        );
                        self.account_picker(ui, "shift-accounts", account, "Chọn tài khoản");
                    });
                    if project.running {
                        ui.label(
                            "Lưu công việc trước khi đổi. Pane của dự án này sẽ đóng rồi mở lại.",
                        );
                    }
                    ui.add_space(7.0);
                    ui.horizontal(|ui| {
                        let title = if project.running {
                            "Đóng pane và đổi"
                        } else {
                            "Đổi tài khoản"
                        };
                        let next = account.trim();
                        let enabled =
                            !next.is_empty() && next != project.account && self.pending.is_none();
                        if ui.add_enabled(enabled, egui::Button::new(title)).clicked() {
                            request = Some(Request::Assign {
                                project: project.id.clone(),
                                account: next.to_owned(),
                                close: project.running,
                            });
                        }
                        close = ui.button("Hủy").clicked();
                    });
                }
                Dialog::Remove { project } => {
                    ui.label(RichText::new(&project.name).strong().size(18.0));
                    ui.label("Gỡ dự án này khỏi danh sách quản lý? Thư mục code và dữ liệu tài khoản vẫn được giữ nguyên.");
                    if project.running {
                        ui.label("Pane CLI đang chạy của dự án này sẽ được đóng trước khi gỡ.");
                    }
                    ui.add_space(7.0);
                    ui.horizontal(|ui| {
                        let enabled = self.pending.is_none();
                        if ui.add_enabled(enabled, egui::Button::new("Gỡ dự án")).clicked() {
                            request = Some(Request::Remove {
                                project: project.id.clone(),
                                close: project.running,
                            });
                        }
                        close = ui.button("Hủy").clicked();
                    });
                }
                Dialog::History { project } => {
                    ui.label(
                        RichText::new(format!("{} · {}", project.name, project.account))
                            .strong()
                            .size(18.0),
                    );
                    if !project.storage.is_empty() {
                        ui.label(
                            RichText::new(format!("Lưu tại WSL: {}", project.storage))
                                .color(MUTED)
                                .size(12.0),
                        );
                    }
                    if project.running {
                        ui.label(
                            RichText::new("Mở lại hoặc tạo chat mới sẽ đóng pane đang chạy rồi mở lại.")
                                .color(MUTED)
                                .size(12.5),
                        );
                    }
                    ui.add_space(6.0);
                    ui.horizontal(|ui| {
                        let enabled = self.pending.is_none();
                        if ui
                            .add_enabled(enabled, egui::Button::new("＋ Mở đoạn chat mới"))
                            .clicked()
                        {
                            request = Some(Request::Resume {
                                project: project.id.clone(),
                                conversation: None,
                                fresh: true,
                                close: project.running,
                            });
                        }
                        close = ui.button("Đóng").clicked();
                    });
                    ui.add_space(6.0);
                    egui::ScrollArea::vertical().max_height(300.0).show(ui, |ui| {
                        if project.sessions.is_empty() {
                            ui.label(
                                RichText::new("Chưa có lịch sử hội thoại nào được lưu cho dự án và tài khoản này.")
                                    .color(MUTED),
                            );
                        } else {
                            for session in &project.sessions {
                                Frame::new()
                                    .fill(Color32::WHITE)
                                    .stroke(Stroke::new(1.0_f32, BORDER))
                                    .corner_radius(7.0)
                                    .inner_margin(9.0)
                                    .show(ui, |ui| {
                                        ui.set_min_width(ui.available_width());
                                        ui.horizontal(|ui| {
                                            ui.vertical(|ui| {
                                                ui.label(RichText::new(&session.title).strong().size(14.0));
                                                let meta = format!(
                                                    "{} · ID: {}{}",
                                                    session.updated_at,
                                                    session.id,
                                                    if session.current { " · Gần nhất" } else { "" }
                                                );
                                                ui.label(RichText::new(meta).color(MUTED).size(11.5));
                                            });
                                            ui.with_layout(Layout::right_to_left(Align::Center), |ui| {
                                                if ui
                                                    .add_enabled(self.pending.is_none(), egui::Button::new("Mở lại"))
                                                    .clicked()
                                                {
                                                    request = Some(Request::Resume {
                                                        project: project.id.clone(),
                                                        conversation: Some(session.id.clone()),
                                                        fresh: false,
                                                        close: project.running,
                                                    });
                                                }
                                            });
                                        });
                                    });
                            }
                        }
                    });
                }
            });
        if let Some(request) = request {
            self.send(request, ctx);
        } else if shown && !close {
            self.dialog = Some(dialog);
        }
    }

    fn account_picker(&self, ui: &mut egui::Ui, id: &str, account: &mut String, placeholder: &str) {
        let displayed = if account.is_empty() {
            placeholder
        } else {
            account.as_str()
        };
        egui::ComboBox::from_id_salt(id)
            .selected_text(displayed)
            .show_ui(ui, |ui| {
                for item in &self.data.accounts {
                    ui.selectable_value(account, item.name.clone(), &item.name);
                }
            });
    }
}

impl eframe::App for Desktop {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll(ctx);
        if let Some(path) = ctx.input(|input| {
            input
                .raw
                .dropped_files
                .iter()
                .find_map(|file| file.path.clone())
        }) {
            self.add_dialog(Some(path));
        }
        self.sidebar(ctx);
        self.projects(ctx);
        self.show_dialog(ctx);
    }
}
