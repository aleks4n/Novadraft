// Tauri desktop shell for the Research Ideation web app. The same Vite build the
// web app serves is loaded into a native webview; it talks to the Python backend
// (app/backend/main.py) on :8787 — start that first (see app/README.md).
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
