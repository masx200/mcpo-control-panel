# ================================================
# FILE: src/mcp_manager_ui/services/mcpo_service.py
# (Full version with updated get_aggregated_tools_from_mcpo)
# ================================================
import asyncio
import logging
import os
import signal
import sys
import subprocess
import json
from typing import Optional, Tuple, List, Dict, Any
import httpx  # For health check and openapi requests
from sqlmodel import Session as SQLModelSession # To avoid conflict with FastAPI Session

from ..models.mcpo_settings import McpoSettings
from .config_service import load_mcpo_settings, generate_mcpo_config_file, get_server_definitions
from ..db.database import engine # Import engine directly for background tasks

logger = logging.getLogger(__name__)
PID_FOLDER = "mcpo_manager_data"
if not os.path.exists(PID_FOLDER):
    os.makedirs(PID_FOLDER)
PID_FILE = f"{PID_FOLDER}/mcpo_process.pid"

# --- State variables for Health Check ---
_health_check_failure_counter = 0
_mcpo_manual_restart_in_progress = False # Flag so the health checker doesn't interfere with manual restart/start/stop

# --- Process State Management (PID file) ---

def _save_pid(pid: int):
    """Saves the MCPO process PID to a file."""
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        logger.info(f"MCPO process PID {pid} saved to {PID_FILE}")
    except IOError as e:
        logger.error(f"Error saving PID {pid} to {PID_FILE}: {e}")

def _load_pid() -> Optional[int]:
    """Loads the MCPO process PID from the file."""
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, "r") as f:
            pid_str = f.read().strip()
            if pid_str:
                return int(pid_str)
            return None
    except (IOError, ValueError) as e:
        logger.error(f"Error loading PID from {PID_FILE}: {e}")
        _clear_pid() # Clear the invalid file
        return None

def _clear_pid():
    """Deletes the PID file."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
            logger.info(f"PID file {PID_FILE} deleted.")
        except OSError as e:
            logger.error(f"Error deleting PID file {PID_FILE}: {e}")

def _is_process_running(pid: Optional[int]) -> bool:
    """Checks if a process with the given PID is running."""
    if pid is None:
        return False
    if sys.platform == "win32":
        # Check on Windows via tasklist
        try:
            # /nh - no header, /fi - filter by PID
            output = subprocess.check_output(
                f'tasklist /nh /fi "PID eq {pid}"',
                stderr=subprocess.STDOUT,
                shell=True
            ).decode('utf-8', errors='ignore')
            # If the process is found, the output will contain the .exe name
            return '.exe' in output.lower()
        except subprocess.CalledProcessError:
            # Command finished with an error (process not found)
            return False
        except Exception as e:
            # Other errors (e.g., permission issues)
            logger.error(f"Error checking process status {pid} on Windows: {e}")
            return False # Assume not running just in case
    else:
        # Check on Unix-like systems via kill -0
        try:
            os.kill(pid, 0) # Sending signal 0 doesn't affect the process, but checks its existence
            return True
        except OSError:
            # Process not found
            return False

# --- Start/Stop/Restart MCPO ---

def _start_mcpo_subprocess_sync(settings: McpoSettings) -> Tuple[Optional[int], str]:
    """
    Synchronous function to start the mcpo process in a separate thread/process.
    Called via asyncio.to_thread.
    """
    command = ["mcpo", "--port", str(settings.port), "--config", settings.config_file_path]
    if settings.use_api_key and settings.api_key:
        command.extend(["--api-key", settings.api_key])

    logger.info(f"[Thread/Subprocess] Starting mcpo process: {' '.join(command)}")

    log_file = None
    stdout_redir = subprocess.DEVNULL # Default output to null
    stderr_redir = subprocess.DEVNULL

    try:
        # Configure output redirection to a log file if specified
        if settings.log_file_path:
            log_dir = os.path.dirname(settings.log_file_path)
            if log_dir and not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    logger.info(f"[Thread/Subprocess] Log directory created: {log_dir}")
                except OSError as e:
                    logger.error(f"[Thread/Subprocess] Failed to create log directory '{log_dir}': {e}. Output will be redirected to DEVNULL.")
                    # Do not interrupt startup, just logs won't be written
            if not log_dir or os.path.exists(log_dir): # Only if directory exists or is not needed
                try:
                    # 'a' - append, buffering=1 - line buffering
                    log_file = open(settings.log_file_path, 'a', buffering=1, encoding='utf-8', errors='ignore')
                    stdout_redir = log_file
                    stderr_redir = log_file
                    logger.info(f"[Thread/Subprocess] mcpo stdout/stderr will be redirected to {settings.log_file_path}")
                except IOError as e:
                    logger.error(f"[Thread/Subprocess] Failed to open log file '{settings.log_file_path}': {e}. Output will be redirected to DEVNULL.")

        # Flags for Popen (important for correct termination on Windows)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP # Allows killing the entire process group with taskkill /T

        # Start the process
        process = subprocess.Popen(
            command,
            stdout=stdout_redir,
            stderr=stderr_redir,
            creationflags=creationflags
            # stdin=subprocess.DEVNULL # Can be added if input is definitely not needed
        )
        msg = f"MCPO process successfully started with PID {process.pid}."
        logger.info(f"[Thread/Subprocess] {msg}")
        return process.pid, msg

    except FileNotFoundError:
        # mcpo command not found
        msg = f"Error starting mcpo: 'mcpo' command not found. Ensure mcpo is installed and available in the system PATH for the user running the manager."
        logger.error(f"[Thread/Subprocess] {msg}")
        return None, msg
    except Exception as e:
        # Other errors during Popen startup
        msg = f"Unexpected error starting mcpo process: {e}"
        logger.error(f"[Thread/Subprocess] {msg}", exc_info=True)
        return None, msg
    finally:
        # Important to close the log file if it was opened in this function
        # (although Popen might keep it open, it's better to close the handle here)
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass # Ignore errors on close

async def start_mcpo(settings: McpoSettings) -> Tuple[bool, str]:
    """Asynchronously starts the MCPO process if it's not already running."""
    global _mcpo_manual_restart_in_progress, _health_check_failure_counter
    _mcpo_manual_restart_in_progress = True # Signal health checker about manual action
    try:
        current_pid = _load_pid()
        if _is_process_running(current_pid):
            msg = f"MCPO process already running with PID {current_pid}."
            logger.warning(msg)
            return False, msg

        # Check for config file existence before starting
        if not os.path.exists(settings.config_file_path):
            msg = f"MCPO configuration file not found: {settings.config_file_path}. Cannot start. Generate it (e.g., via 'Apply and Restart')."
            logger.error(msg)
            return False, msg

        logger.info(f"Attempting to start mcpo with settings: port={settings.port}, config='{settings.config_file_path}'...")

        # Run the synchronous function _start_mcpo_subprocess_sync in a separate thread
        pid, message = await asyncio.to_thread(_start_mcpo_subprocess_sync, settings)

        if pid:
            _save_pid(pid)
            _health_check_failure_counter = 0 # Reset health check failure counter after successful manual start
            logger.info(f"MCPO started. {message}")
            return True, message
        else:
            _clear_pid() # Clear PID file if startup failed
            logger.error(f"Failed to start MCPO. {message}")
            return False, message
    finally:
        await asyncio.sleep(0.1) # Short pause before releasing the flag
        _mcpo_manual_restart_in_progress = False

async def stop_mcpo() -> Tuple[bool, str]:
    """Asynchronously stops the MCPO process if it's running."""
    global _mcpo_manual_restart_in_progress
    _mcpo_manual_restart_in_progress = True # Signal health checker
    try:
        pid = _load_pid()
        if not pid:
            msg = "MCPO process PID not found (in file). It might not have been started via the manager or was stopped previously."
            logger.warning(msg)
            return False, msg # Consider it a failure as we cannot confirm stoppage

        if not _is_process_running(pid):
            msg = f"MCPO process with PID {pid} (from file) not found in the system. Clearing stale PID file."
            logger.warning(msg)
            _clear_pid()
            return True, msg # Consider it a success as the process is gone

        logger.info(f"Attempting to stop mcpo process with PID {pid}...")
        try:
            if sys.platform == "win32":
                # On Windows, use taskkill with /F (force) and /T (tree - kill child processes)
                logger.info(f"Sending command: taskkill /F /T /PID {pid}")
                # Use subprocess.run for synchronous execution of taskkill
                result = await asyncio.to_thread(
                    subprocess.run,
                    f'taskkill /F /T /PID {pid}',
                    shell=True, capture_output=True, text=True, check=False
                )
                logger.info(f"Taskkill Result (PID: {pid}): RC={result.returncode}, stdout='{result.stdout.strip()}', stderr='{result.stderr.strip()}'")
                await asyncio.sleep(0.5) # Give the process time to terminate
                if not _is_process_running(pid):
                    msg = f"MCPO process (PID: {pid}) successfully stopped via taskkill."
                    logger.info(msg)
                    _clear_pid()
                    return True, msg
                else:
                    # If taskkill didn't work (unlikely with /F)
                    msg = f"Failed to stop MCPO process (PID: {pid}) using taskkill. Check permissions."
                    logger.error(msg)
                    return False, msg
            else:
                # On Unix-like systems, first try SIGTERM, then SIGKILL
                logger.info(f"Sending SIGTERM signal to process with PID {pid}...")
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(1) # Wait for termination

                if _is_process_running(pid):
                    logger.warning(f"Process {pid} did not terminate after SIGTERM. Sending SIGKILL...")
                    os.kill(pid, signal.SIGKILL)
                    await asyncio.sleep(0.5) # Short pause after SIGKILL

                if not _is_process_running(pid):
                    msg = f"MCPO process (PID: {pid}) successfully stopped."
                    logger.info(msg)
                    _clear_pid()
                    return True, msg
                else:
                    # If even SIGKILL didn't help (very strange)
                    msg = f"Failed to stop MCPO process (PID: {pid}) even after SIGKILL."
                    logger.error(msg)
                    return False, msg

        except ProcessLookupError:
            # Process no longer exists when we tried to stop it
            msg = f"Process with PID {pid} not found during stop attempt (possibly already terminated)."
            logger.warning(msg)
            _clear_pid()
            return True, msg # Consider it a success
        except Exception as e:
            # Other errors during stop
            msg = f"Error stopping mcpo process (PID: {pid}): {e}"
            logger.error(msg, exc_info=True)
            return False, msg
    finally:
        await asyncio.sleep(0.1)
        _mcpo_manual_restart_in_progress = False

async def restart_mcpo_process_with_new_config(db_session: SQLModelSession, settings: McpoSettings) -> Tuple[bool, str]:
    """
    Stops mcpo, generates a new standard config, and starts mcpo.
    Used by the 'Apply and Restart' button and the Health Checker.
    """
    global _mcpo_manual_restart_in_progress
    # Additional check to prevent accidental recursion if called not by health_checker
    # Health Checker itself checks the flag before calling restart
    if _mcpo_manual_restart_in_progress and not settings.health_check_enabled :
        logger.warning("Restart process already initiated, new restart request ignored.")
        return False, "Restart process already in progress."

    # Set the flag at the beginning; it will be released in the finally blocks of start/stop
    _mcpo_manual_restart_in_progress = True
    logger.info("Starting MCPO restart process...")
    final_messages = []

    # 1. Stop the current process (if running)
    current_pid = _load_pid()
    if _is_process_running(current_pid):
        logger.info(f"Restart: Detected running MCPO process (PID: {current_pid}). Attempting stop...")
        stop_success, stop_msg = await stop_mcpo() # stop_mcpo manages the flag and clears PID
        final_messages.append(f"Stop: {stop_msg}")
        if not stop_success:
            # If stopping failed, abort the restart
            message = " | ".join(final_messages) + " Critical error: Failed to stop current MCPO process. Restart cancelled."
            logger.error(message)
            # Flag _mcpo_manual_restart_in_progress should be released in stop_mcpo's finally block
            return False, message
    else:
        logger.info("Restart: Running MCPO process not detected, proceeding to config generation.")
        _clear_pid() # Clear PID file just in case, if process wasn't found

    # 2. Generate new configuration file
    logger.info("Restart: Generating new MCPO configuration file...")
    # Use standard generation (without Windows adaptation) for the file mcpo will read
    if not generate_mcpo_config_file(db_session, settings):
        message = " | ".join(final_messages) + " Error: Failed to generate configuration file. MCPO start cancelled."
        logger.error(message)
        # Flag _mcpo_manual_restart_in_progress should have been released by stop_mcpo (if called)
        # or needs to be released here if stop wasn't required
        _mcpo_manual_restart_in_progress = False # Releasing flag as we are interrupting
        return False, message
    final_messages.append("Configuration file successfully generated.")

    # 3. Start MCPO with the new configuration
    logger.info("Restart: Attempting to start MCPO with the new configuration...")
    # start_mcpo itself will set and release the _mcpo_manual_restart_in_progress flag and reset the health check counter
    start_success, start_msg = await start_mcpo(settings)
    final_messages.append(f"Start: {start_msg}")

    # Return the start result and collected messages
    return start_success, " | ".join(final_messages)

# --- Get Status and Logs ---

def get_mcpo_status() -> str:
    """Returns the string status of the MCPO process: RUNNING, STOPPED, ERROR."""
    pid = _load_pid()
    if pid is None:
        return "STOPPED" # PID file not found

    if _is_process_running(pid):
        return "RUNNING" # Process with PID from file found and running
    else:
        # PID file exists, but process not found - this is an error
        logger.warning(f"MCPO Status: PID {pid} found in file, but the corresponding process is not running. Status: ERROR")
        return "ERROR"

async def get_mcpo_logs(lines: int = 100, log_file_path: Optional[str] = None) -> List[str]:
    """Asynchronously reads the last N lines from the MCPO log file."""
    # Load settings to get the log path if not explicitly provided
    settings = load_mcpo_settings()
    actual_log_path = log_file_path or settings.log_file_path

    if not actual_log_path:
        return ["Error: Log file path is not configured."]
    if not os.path.exists(actual_log_path):
        return [f"Error: Log file not found at path: {actual_log_path}"]

    try:
        # Use deque for efficient storage of the last lines
        from collections import deque
        last_lines = deque(maxlen=lines)

        # Read the file line by line in binary mode and decode ignoring errors
        # This is more reliable for potentially corrupt logs
        def read_lines_sync():
            with open(actual_log_path, 'rb') as f:
                for line_bytes in f:
                    last_lines.append(line_bytes.decode('utf-8', errors='ignore').rstrip())
            return list(last_lines)

        # Perform synchronous read in a separate thread
        return await asyncio.to_thread(read_lines_sync)

    except Exception as e:
        logger.error(f"Error reading log file {actual_log_path}: {e}", exc_info=True)
        return [f"Error reading logs: {e}"]

# --- Tool Aggregation ---

async def get_aggregated_tools_from_mcpo(db_session: SQLModelSession) -> Dict[str, Any]:
    """
    Aggregates tools from the running MCPO instance.
    Returns a dictionary with status, a list of servers with their tools,
    and the public base URL for generating links.
    """
    logger.info("Aggregating tools from running MCPO instance...")
    mcpo_status = get_mcpo_status()
    settings = load_mcpo_settings() # Load current settings

    # Determine the base URL to be used for generating links in the UI
    if settings.public_base_url:
        # Use the configured public URL, removing trailing slash
        base_url_for_links = settings.public_base_url.rstrip('/')
        logger.debug(f"Using public base URL for links: {base_url_for_links}")
    else:
        # If public URL is not set, use the local address and port
        base_url_for_links = f"http://127.0.0.1:{settings.port}"
        logger.debug(f"Public base URL not set, using local for links: {base_url_for_links}")

    # Initialize the result, adding status and base URL immediately
    result: Dict[str, Any] = {
        "status": mcpo_status,
        "servers": {},
        "base_url_for_links": base_url_for_links # This URL will be used in the tools.html template
    }

    # If MCPO is not running, no point in proceeding
    if mcpo_status != "RUNNING":
        logger.warning(f"Cannot aggregate tools, MCPO status: {mcpo_status}")
        return result # Return result with current status and URL

    # Determine the internal URL for requests to the MCPO API itself (always localhost)
    mcpo_internal_api_url = f"http://127.0.0.1:{settings.port}"
    headers = {}
    if settings.use_api_key and settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # Get the list of enabled server definitions from the DB
    enabled_definitions = get_server_definitions(db_session, only_enabled=True, limit=10000) # Get all enabled
    if not enabled_definitions:
        logger.info("No enabled server definitions found in the database.")
        return result # Return result with current status, URL, and empty server list

    # --- Nested async function to fetch OpenAPI spec for one server ---
    async def fetch_openapi(definition):
        server_name = definition.name
        # Skip request for internal Health Check echo server
        if server_name == settings.INTERNAL_ECHO_SERVER_NAME and settings.health_check_enabled:
            return server_name, {"status": "SKIPPED", "error_message": "Internal echo server (skipped).", "tools": []}

        # Format URL for openapi.json request to MCPO
        url = f"{mcpo_internal_api_url}/{server_name}/openapi.json"
        server_result_data = {"status": "ERROR", "error_message": None, "tools": []}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
                logger.debug(f"Requesting OpenAPI for server '{server_name}' at URL: {url}")
                resp = await client.get(url)

                if resp.status_code == 200:
                    openapi_data = resp.json()
                    paths = openapi_data.get("paths", {})
                    found_tools = []
                    for path, methods in paths.items():
                        # Look only for POST methods (primary invocation method in MCP)
                        if post_method_details := methods.get("post"):
                            tool_info = {
                                "path": path, # Path to the tool (e.g., "/calculate")
                                "summary": post_method_details.get("summary", ""),
                                "description": post_method_details.get("description", "")
                            }
                            found_tools.append(tool_info)
                    server_result_data["tools"] = found_tools
                    server_result_data["status"] = "OK"
                    logger.debug(f"Server '{server_name}': Found {len(found_tools)} tools.")
                else:
                    # Error during request to MCPO
                    error_text = resp.text[:200] # Limit the length of the error text
                    server_result_data["error_message"] = f"MCPO Error (HTTP {resp.status_code}): {error_text}"
                    logger.warning(f"Error requesting OpenAPI for '{server_name}' (HTTP {resp.status_code}): {error_text}")

        except httpx.RequestError as e:
            # Network error during request to MCPO
            server_result_data["error_message"] = f"Network error: {e.__class__.__name__}"
            logger.warning(f"Network error requesting OpenAPI for '{server_name}': {e}")
        except Exception as e:
            # Other errors (e.g., JSONDecodeError)
            server_result_data["error_message"] = f"Internal error: {e.__class__.__name__}"
            logger.warning(f"Error processing OpenAPI for '{server_name}': {e}", exc_info=True)

        return server_name, server_result_data
    # --- End of nested fetch_openapi function ---

    # Start requests to all servers in parallel
    tasks = [fetch_openapi(d) for d in enabled_definitions]
    fetch_results = await asyncio.gather(*tasks)

    # Collect results into the final dictionary
    for server_name, server_result in fetch_results:
        result["servers"][server_name] = server_result

    logger.info(f"Tool aggregation finished. Processed {len(enabled_definitions)} definitions.")
    return result

# --- Health Check Logic ---

async def run_health_check_loop_async(get_db_session_func: callable):
    """Asynchronous loop for periodic MCPO health checks."""
    global _health_check_failure_counter, _mcpo_manual_restart_in_progress
    logger.info("Starting background MCPO health check loop...")

    await asyncio.sleep(5) # Short pause before the first check

    while True:
        settings = load_mcpo_settings() # Load current settings on each iteration

        if not settings.health_check_enabled:
            #logger.debug("Health Check: Check disabled in settings.")
            # Reset counter if check is disabled
            if _health_check_failure_counter > 0:
                logger.info("Health Check: Check disabled, resetting failure counter.")
                _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds) # Wait normal interval before checking settings again
            continue

        if _mcpo_manual_restart_in_progress:
            logger.info("Health Check: Manual MCPO management detected, skipping check.")
            await asyncio.sleep(settings.health_check_failure_retry_delay_seconds) # Short pause
            continue

        mcpo_status = get_mcpo_status()
        if mcpo_status != "RUNNING":
            #logger.warning(f"Health Check: MCPO not running (status: {mcpo_status}). Skipping check.")
            # Reset counter if mcpo is not running (to avoid accumulating errors while it's stopped)
            if _health_check_failure_counter > 0:
                 logger.info(f"Health Check: MCPO not running (status: {mcpo_status}), resetting failure counter.")
                 _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds) # Wait normal interval
            continue

        # Format URL and payload for the request to the internal echo server via MCPO
        health_check_url = f"http://127.0.0.1:{settings.port}/{settings.INTERNAL_ECHO_SERVER_NAME}{settings.INTERNAL_ECHO_TOOL_PATH}"
        payload = settings.INTERNAL_ECHO_PAYLOAD
        headers = {}
        if settings.use_api_key and settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"

        try:
            async with httpx.AsyncClient(headers=headers, timeout=5.0) as client: # Timeout for health check request
                logger.debug(f"Health Check: Sending POST request to {health_check_url}")
                response = await client.post(health_check_url, json=payload)

            if 200 <= response.status_code < 300:
                # Check successful
                if _health_check_failure_counter > 0:
                    logger.info(f"Health Check: Success (Status: {response.status_code}). Failure counter reset.")
                else:
                     logger.debug(f"Health Check: Success (Status: {response.status_code}).")
                _health_check_failure_counter = 0
                await asyncio.sleep(settings.health_check_interval_seconds) # Wait normal interval until next check
            else:
                # Check failed (non-2xx response)
                logger.warning(f"Health Check: FAILURE (Status: {response.status_code}). Response: {response.text[:200]}")
                _health_check_failure_counter += 1
                await handle_health_check_failure(settings, get_db_session_func) # Handle failure

        except httpx.RequestError as e:
            # Network error during request
            logger.error(f"Health Check: Network error requesting MCPO ({e.__class__.__name__}: {e}).")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func) # Handle failure
        except Exception as e:
            # Other unexpected errors
            logger.error(f"Health Check: Unexpected error ({e.__class__.__name__}: {e}).", exc_info=True)
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func) # Handle failure

async def handle_health_check_failure(settings: McpoSettings, get_db_session_func: callable):
    """Handles a failed health check, decides if a restart is needed."""
    global _health_check_failure_counter, _mcpo_manual_restart_in_progress

    logger.info(f"Health Check: Failure attempt {_health_check_failure_counter} of {settings.health_check_failure_attempts}.")

    if _health_check_failure_counter >= settings.health_check_failure_attempts:
        logger.warning(f"Health Check: Reached maximum number ({settings.health_check_failure_attempts}) of failed check attempts.")

        if settings.auto_restart_on_failure:
            logger.info("Health Check: Auto-restart enabled. Attempting MCPO restart...")

            # Get DB session asynchronously for config generation inside restart
            async with get_async_db_session(get_db_session_func) as db_session:
                if db_session:
                    # Call the restart function, it manages the _mcpo_manual_restart_in_progress flag
                    success, message = await restart_mcpo_process_with_new_config(db_session, settings)
                    if success:
                        logger.info(f"Health Check: MCPO successfully restarted after failures. Message: {message}")
                        _health_check_failure_counter = 0 # Reset counter after successful restart
                        await asyncio.sleep(settings.health_check_interval_seconds) # Wait normal interval
                    else:
                        logger.error(f"Health Check: Automatic MCPO restart FAILED after failures. Message: {message}")
                        # After failed restart, might increase pause or take other actions
                        # For now, just reset the counter and wait longer before the next check
                        _health_check_failure_counter = 0
                        await asyncio.sleep(settings.health_check_interval_seconds * 3) # Triple pause
                else:
                    logger.error("Health Check: Failed to get DB session for restart. Auto-restart cancelled.")
                    _health_check_failure_counter = 0 # Resetting counter
                    await asyncio.sleep(settings.health_check_interval_seconds * 3) # Wait longer

        else: # auto_restart_on_failure is False
            logger.info("Health Check: Auto-restart disabled. Manual intervention required to restore MCPO.")
            # Reset counter to avoid log spam about "Max attempts" every second
            _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds) # Wait normal interval until next check (which will likely fail too)
    else:
        # If max attempts not yet reached
        logger.info(f"Health Check: Waiting {settings.health_check_failure_retry_delay_seconds} sec before next check attempt...")
        await asyncio.sleep(settings.health_check_failure_retry_delay_seconds)

# Helper function/context manager for asynchronously getting a DB session in a background task
import contextlib

@contextlib.asynccontextmanager
async def get_async_db_session(get_db_session_func: callable = None) -> SQLModelSession:
    """
    Async context manager for getting a DB session in background tasks.
    Uses the global engine.
    """
    session = None
    try:
        # Create a new session directly from the engine for this operation
        # This is the simplest way for background tasks not tied to an HTTP request
        session = SQLModelSession(engine)
        yield session
    except Exception as e:
        logger.error(f"Error creating DB session in background task: {e}", exc_info=True)
        # Re-raise the exception if needed, or return None/empty session
        # In this case, if session wasn't created, yield won't happen, and with will exit
        raise # Re-raise error so the calling code can handle it
    finally:
        if session:
            try:
                session.close()
            except Exception as e:
                logger.error(f"Error closing DB session in background task: {e}", exc_info=True)