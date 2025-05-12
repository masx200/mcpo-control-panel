# MCPO Control Panel

[![Python Version](https://img.shields.io/pypi/pyversions/mcpo-control-panel.svg)](https://pypi.org/project/mcpo-control-panel/)
[![PyPI version](https://badge.fury.io/py/mcpo-control-panel.svg)](https://badge.fury.io/py/mcpo-control-panel)
<!-- Add other badges if you have them, e.g., license, build status -->

A web-based control panel designed to simplify the management of [MCP Orchestrator (`mcpo`)](https://github.com/daswer123/mcpo) instances and their server configurations. It provides a user-friendly UI and an API for interacting with `mcpo`.

## Key Features

*   **Server Definition Management:**
    *   Create, Read, Update, and Delete server definitions (for `stdio`, `sse`, `streamable_http` types).
    *   Easily toggle servers as enabled/disabled for inclusion in the `mcpo` configuration.
    *   Intuitive forms for specifying commands, arguments, environment variables, and URLs.
*   **Bulk Server Operations:**
    *   Add multiple server definitions at once by pasting JSON content.
    *   Supports various JSON formats (object with `mcpServers`, direct name-to-config map, list of server objects).
    *   Two-step bulk add process:
        1.  **Analyze:** Preview valid new servers, existing ones, and invalid entries from your JSON.
        2.  **Confirm:** Add only the validated new servers to the database.
    *   Automatic de-adaptation of Windows-specific commands (e.g., `cmd /c npx ...` -> `npx ...`).
*   **MCPO Process Control:**
    *   Start, stop, and restart the `mcpo` process directly from the UI.
    *   View the real-time status of the `mcpo` process (Running, Stopped, Error).
    *   "Apply and Restart" functionality: Generates the `mcpo` configuration from current server definitions and then restarts `mcpo`.
*   **Log Viewing:**
    *   Display the latest logs from the `mcpo` process log file.
    *   Configurable auto-refresh for logs.
    *   Manual refresh and auto-scroll options.
*   **MCPO Settings Configuration:**
    *   Manage `mcpo` startup parameters (port, API key usage, config file path, log file path).
    *   Configure UI behavior (log refresh interval).
    *   Setup and manage health check parameters for `mcpo`.
*   **Tool Aggregation & Viewing:**
    *   Dynamically fetches and displays a list of available tools (server paths and summaries) from a running `mcpo` instance by querying its OpenAPI specifications.
    *   Provides the base URL for constructing tool invocation links.
*   **Health Checks & Auto-Restart:**
    *   Background health monitoring of the `mcpo` process using a built-in echo server.
    *   Configurable check interval, failure attempts, and retry delays.
    *   Optional automatic restart of `mcpo` if health checks fail consecutively.
*   **Dynamic Configuration Generation:**
    *   Generates the `mcp_generated_config.json` file for `mcpo` based on enabled server definitions.
    *   Provides options to download the standard configuration or a Windows-adapted version (which wraps commands like `npx`, `uvx`, `docker` with `cmd /c ...`).
*   **Modern Web UI:**
    *   Built with FastAPI, Jinja2 templates, and HTMX for a responsive and dynamic user experience.
    *   Utilizes Materialize CSS for styling.
    *   Persistent storage using an SQLite database (via SQLModel).

## Installation

### Prerequisites

*   Python 3.11 or higher.
*   `mcpo` (MCP Orchestrator) installed and available in your PATH or Python environment. The control panel will attempt to run the `mcpo` command.

### Method 1: From PyPI (Recommended)

```bash
pip install mcpo-control-panel
```

### Method 2: From Source (for development)

1.  Clone the repository:
    ```bash
    git clone https://github.com/daswer123/mcpo-control-panel.git
    cd mcpo-control-panel
    ```
2.  Install in editable mode (this will also install dependencies):
    ```bash
    pip install -e .
    ```

## Running the Application

Once installed, you can run the MCPO Control Panel using the following command:

```bash
python -m mcpo_control_panel [OPTIONS]
```

Or, if you have the scripts directory of your Python environment in your PATH:

```bash
mcpo-control-panel [OPTIONS]
```

**Common Options:**

*   `--host TEXT`: Host to bind the server to. (Default: `127.0.0.1`)
    *   Environment variable: `MCPO_MANAGER_HOST`
*   `--port INTEGER`: Port to bind the server to. (Default: `8083`)
    *   Environment variable: `MCPO_MANAGER_PORT`
*   `--workers INTEGER`: Number of Uvicorn workers. (Default: `1`)
    *   Environment variable: `MCPO_MANAGER_WORKERS`
*   `--reload`: Enable auto-reload (for development).
*   `--config-dir TEXT`: Directory for storing MCPO manager data (SQLite database, PID files, generated configs, settings).
    *   Default: `~/.mcpo_manager_data` (e.g., `C:\Users\YourUser\.mcpo_manager_data` on Windows or `/home/youruser/.mcpo_manager_data` on Linux).
    *   Environment variable: `MCPO_MANAGER_DATA_DIR`

**Example:**

```bash
python -m mcpo_control_panel --port 8083 --reload --config-dir "/path/to/my/mcpo-data"
```

Or using environment variables:

```bash
export MCPO_MANAGER_PORT=8083
export MCPO_MANAGER_DATA_DIR="/path/to/my/mcpo-data"
python -m mcpo_control_panel --reload
```

The application will create the specified `--config-dir` if it doesn't exist. All application data, including the SQLite database (`mcp_manager_data.db`), settings file (`mcpo_manager_settings.json`), generated MCPO configuration (`mcp_generated_config.json`), and the `mcpo` process PID file, will be stored in this directory.

### Accessing the UI

Once the server is running, open your web browser and navigate to:

`http://<host>:<port>/ui`

For example, if running with default settings: `http://127.0.0.1:8083/ui`

## Configuration

Most configurations for the `mcpo` process itself (like its port, whether it uses an API key, log file paths) and for the UI (like log auto-refresh settings, health check parameters) are managed through the web interface on the "MCPO Settings" page.

These settings are saved to `mcpo_manager_settings.json` within the `MCPO_MANAGER_DATA_DIR_EFFECTIVE` (the directory specified by `--config-dir` or its default).

## License

This project is licensed under the MIT License 