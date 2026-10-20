// Prevents an additional console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! HOPS desktop app: a native window around the HOPS web interface.
//!
//! The science runs in Python (`python -m hops.web`); this shell starts that backend on a free
//! local port, shows a loading page until it answers, then points the window at it. It also gives
//! the web interface native dialogs and opens external links in the system browser.

use std::fs::{self, File};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

/// How long the Python backend may take to import astropy, photutils etc.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(180);

struct Backend(Mutex<Option<Child>>);

/// Where the Python backend comes from.
struct Engine {
    python: PathBuf,
    /// put on PYTHONPATH (the folder that contains the `hops` package)
    source: PathBuf,
    /// working directory of the backend
    cwd: PathBuf,
}

/// The engine shipped inside the app (desktop/scripts/bundle_python.py), if there is one.
fn bundled_engine(app: &AppHandle) -> Option<Engine> {
    let engine = app.path().resource_dir().ok()?.join("engine");
    let python = if cfg!(windows) {
        engine.join("python").join("python.exe")
    } else {
        engine.join("python").join("bin").join("python3")
    };
    if !python.exists() {
        return None;
    }
    let cwd = app.path().home_dir().unwrap_or_else(|_| engine.clone());
    Some(Engine { python, source: engine.join("app"), cwd })
}

/// Development fallback: the checkout the app was built from and its `.venv`.
/// `HOPS_ROOT` / `HOPS_PYTHON` override both (they also take precedence over the bundled engine).
fn development_engine() -> Engine {
    let root = std::env::var_os("HOPS_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("HOPS_DEFAULT_ROOT")));
    let python = std::env::var_os("HOPS_PYTHON").map(PathBuf::from).unwrap_or_else(|| {
        if cfg!(windows) {
            root.join(".venv").join("Scripts").join("python.exe")
        } else {
            root.join(".venv").join("bin").join("python")
        }
    });
    Engine { python, source: root.clone(), cwd: root }
}

fn engine(app: &AppHandle) -> Engine {
    let overridden = std::env::var_os("HOPS_ROOT").is_some() || std::env::var_os("HOPS_PYTHON").is_some();
    match bundled_engine(app) {
        Some(engine) if !overridden => engine,
        _ => development_engine(),
    }
}

fn free_port() -> std::io::Result<u16> {
    Ok(TcpListener::bind("127.0.0.1:0")?.local_addr()?.port())
}

fn start_backend(app: &AppHandle, port: u16) -> Result<(Child, PathBuf), String> {
    let Engine { python, source, cwd } = engine(app);
    if !python.exists() {
        return Err(format!(
            "Python was not found at\n{}\n\nBuild the bundled engine with `npm --prefix desktop run engine`, \
             or set HOPS_PYTHON to a Python that has HOPS' requirements installed.",
            python.display()
        ));
    }

    let log_dir = app.path().app_log_dir().map_err(|e| e.to_string())?;
    fs::create_dir_all(&log_dir).map_err(|e| e.to_string())?;
    let log_path = log_dir.join("backend.log");
    let log = File::create(&log_path).map_err(|e| e.to_string())?;
    let log_err = log.try_clone().map_err(|e| e.to_string())?;

    let child = Command::new(&python)
        .args(["-m", "hops.web", "--no-browser", "--port", &port.to_string()])
        .current_dir(&cwd)
        // run these HOPS sources, not an older `pip install`ed copy
        .env("PYTHONPATH", &source)
        // ignore packages from the user's own Python setup
        .env("PYTHONNOUSERSITE", "1")
        .env("MPLBACKEND", "Agg")
        .env("PYTHONUNBUFFERED", "1")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log))
        .stderr(Stdio::from(log_err))
        .spawn()
        .map_err(|e| format!("Could not start {}: {}", python.display(), e))?;

    Ok((child, log_path))
}

/// Wait until the backend accepts connections, then load it in the window.
fn wait_for_backend(app: AppHandle, port: u16, log_path: PathBuf) {
    thread::spawn(move || {
        let address: SocketAddr = ([127, 0, 0, 1], port).into();
        let started = Instant::now();
        loop {
            if TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok() {
                if let Some(window) = app.get_webview_window("main") {
                    let url = Url::parse(&format!("http://127.0.0.1:{port}/")).expect("valid url");
                    let _ = window.navigate(url);
                }
                return;
            }

            let exited = {
                let state = app.state::<Backend>();
                let mut guard = state.0.lock().unwrap();
                match guard.as_mut().map(|child| child.try_wait()) {
                    Some(Ok(Some(status))) => Some(status.to_string()),
                    _ => None,
                }
            };

            if exited.is_some() || started.elapsed() > STARTUP_TIMEOUT {
                let tail = fs::read_to_string(&log_path)
                    .map(|log| log.lines().rev().take(15).collect::<Vec<_>>().into_iter().rev().collect::<Vec<_>>().join("\n"))
                    .unwrap_or_default();
                let reason = match exited {
                    Some(status) => format!("The Python backend stopped ({status})."),
                    None => "The Python backend did not answer in time.".to_string(),
                };
                show_error(&app, &format!("{reason}\n\nLog: {}\n\n{tail}", log_path.display()));
                return;
            }

            thread::sleep(Duration::from_millis(300));
        }
    });
}

fn show_error(app: &AppHandle, message: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let text = serde_json_string(message);
        let _ = window.eval(&format!("window.hopsStartFailed && window.hopsStartFailed({text})"));
    }
}

/// Minimal JSON string encoding for passing a message to the loading page.
fn serde_json_string(text: &str) -> String {
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => {}
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn stop_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<Backend>() {
        if let Some(mut child) = state.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

// commands used by the web interface (see hops/web/frontend/src/native.ts)

#[tauri::command]
async fn pick_folder(app: AppHandle, start: Option<String>) -> Option<String> {
    let mut dialog = app.dialog().file().set_title("Choose the data directory");
    if let Some(start) = start.filter(|s| !s.is_empty()) {
        dialog = dialog.set_directory(start);
    }
    dialog.blocking_pick_folder().and_then(|p| p.into_path().ok()).map(|p| p.display().to_string())
}

#[tauri::command]
async fn pick_save_file(app: AppHandle, file_name: String) -> Option<String> {
    app.dialog()
        .file()
        .set_file_name(file_name)
        .blocking_save_file()
        .and_then(|p| p.into_path().ok())
        .map(|p| p.display().to_string())
}

#[tauri::command]
fn open_external(app: AppHandle, url: String) -> Result<(), String> {
    app.opener().open_url(url, None::<&str>).map_err(|e| e.to_string())
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Backend(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![pick_folder, pick_save_file, open_external])
        .setup(|app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("HOPS")
                .inner_size(1440.0, 900.0)
                .min_inner_size(800.0, 600.0)
                .build()?;

            let handle = app.handle().clone();
            match free_port().map_err(|e| e.to_string()).and_then(|port| start_backend(&handle, port).map(|r| (port, r))) {
                Ok((port, (child, log_path))) => {
                    *app.state::<Backend>().0.lock().unwrap() = Some(child);
                    wait_for_backend(handle, port, log_path);
                }
                Err(message) => {
                    // give the loading page a moment to define hopsStartFailed
                    thread::spawn(move || {
                        thread::sleep(Duration::from_millis(800));
                        show_error(&handle, &message);
                    });
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building the HOPS app");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            stop_backend(handle);
        }
    });
}
