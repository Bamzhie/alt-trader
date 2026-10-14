fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&["get_service_token"]),
        ),
    )
    .expect("failed to build Tauri application metadata");
}
