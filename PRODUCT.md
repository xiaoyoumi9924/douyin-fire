# Product

## Platform

Cross-platform local desktop app for Windows, macOS, and Linux.

## Stack

- Electron desktop window serving the existing single-page control panel.
- Python sidecar packaged with PyInstaller.
- Playwright Chromium bundled with each platform installer.
- User configuration, credentials, images, and history stored in the platform's application-data directory.

## Product Purpose

Provide the existing Douyin Fire control panel as a desktop app. It should run on the user's own computer without a VPS, public web server, reverse proxy, or publicly exposed port.

## Operating Context

- The app starts a loopback-only HTTP service on a randomly assigned local port and opens it in an Electron window.
- The backend is bound to `127.0.0.1`; it is not reachable from the public internet.
- Playwright connects to Douyin from the user's device.
- Daily runs use the native scheduler: launchd on macOS, Task Scheduler on Windows, and cron on Linux.
- Login state and task data remain in the local app-data directory and are excluded from source control and release artifacts.

## Capabilities

- Manage friends, text and image messages, accounts, and login state.
- Run a Dry Run or send task and view progress and history.
- Schedule daily runs without keeping the desktop window open.
- Build Windows, macOS Intel, macOS Apple silicon, and Linux installers with GitHub Actions.

## Constraints

- Do not bind the service to a public interface or introduce a remote control endpoint.
- Do not include local `.env`, account config, Storage State, images, or runtime artifacts in installers or source commits.
- Never display Cookie or Storage State contents in the UI or logs.
- Preserve the existing upstream license and attribution.
