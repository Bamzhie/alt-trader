#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{env, fs, path::PathBuf};

#[tauri::command]
fn get_service_token() -> Result<String, String> {
    let home = env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or_else(|| "HOME is not set for this desktop session".to_string())?;
    let token_path = home.join(".config/altradar/service.token");
    let metadata = fs::symlink_metadata(&token_path).map_err(|error| {
        format!("cannot read {}: {error}", token_path.display())
    })?;

    if !metadata.file_type().is_file() {
        return Err("service token must be a regular file".to_string());
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err("service token permissions must be 0600 or stricter".to_string());
        }
    }

    let token = fs::read_to_string(&token_path)
        .map_err(|error| format!("cannot read service token: {error}"))?;
    let token = token.trim();
    if token.is_empty() || token.len() > 512 || token.chars().any(char::is_whitespace) {
        return Err("service token file is empty or malformed".to_string());
    }

    Ok(token.to_string())
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![get_service_token])
        .run(tauri::generate_context!())
        .expect("error while running ALT RADAR desktop app");
}
