# ================================================
# FILE: mcpo_control_panel/services/mcpo_service.py
# (Refactored: No PID files, direct process object management)
# ================================================
import asyncio
import logging
import os
import signal
import sys
import subprocess # Keep for DEVNULL etc.
from typing import Optional, Tuple, List, Dict, Any, Callable
import httpx
from sqlmodel import Session as SQLModelSession
import contextlib
from pathlib import Path

from ..models.mcpo_settings import McpoSettings
from .config_service import load_mcpo_settings, generate_mcpo_config_file, get_server_definitions
from ..db.database import engine # Import engine directly for background tasks

logger = logging.getLogger(__name__)

# --- Process Management State ---
# Holds the reference to the running asyncio.subprocess.Process object
_mcpo_process: Optional[asyncio.subprocess.Process] = None
_mcpo_log_file_handle: Optional[Any] = None # To hold the open log file handle

# --- Health Check State ---
_health_check_failure_counter = 0
_mcpo_manual_operation_in_progress = False # Flag for manual start/stop/restart

# --- Helper to get data directory ---
def _get_data_dir_path() -> Path:
    """Determines the path to the manager's data directory."""
    effective_data_dir_str = os.getenv("MCPO_MANAGER_DATA_DIR_EFFECTIVE")
    if effective_data_dir_str:
        data_dir = Path(effective_data_dir_str)
    else:
        # Fallback only if env var not set (should be set by __main__.py)
        data_dir = Path.home() / ".mcpo_manager_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir

# --- Close Log File Handle ---
def _close_log_file_handle():
    """Closes the globally held log file handle if it's open."""
    global _mcpo_log_file_handle
    if _mcpo_log_file_handle and not _mcpo_log_file_handle.closed:
        try:
            _mcpo_log_file_handle.close()
            logger.info("Closed MCPO log file handle.")
        except Exception as e:
            logger.warning(f"Failed to close MCPO log file handle: {e}")
        finally:
            _mcpo_log_file_handle = None

# --- Start/Stop/Restart MCPO ---

async def start_mcpo(settings: McpoSettings) -> Tuple[bool, str]:
    """Asynchronously starts the MCPO process if it's not already running."""
    global _mcpo_process, _mcpo_log_file_handle, _mcpo_manual_operation_in_progress, _health_check_failure_counter

    # if _mcpo_manual_operation_in_progress:
    #     logger.warning("Attempted to start MCPO during another management operation. Aborted.")
    #     return False, "MCPO management operation already in progress."

    _mcpo_manual_operation_in_progress = True
    try:
        # Check if process object exists and process hasn't exited
        if _mcpo_process and _mcpo_process.returncode is None:
            msg = f"MCPO process is already running with PID {_mcpo_process.pid}."
            logger.warning(msg)
            return False, msg # Not an error, just already running

        # Check config file existence before starting
        config_path = Path(settings.config_file_path)
        if not config_path.is_file():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            if not config_path.is_file():
                 msg = f"MCPO configuration file not found: {settings.config_file_path}. Cannot start."
                 logger.error(msg)
                 return False, msg

        logger.info(f"Attempting to start mcpo with settings: port={settings.port}, config='{settings.config_file_path}'...")

        command = ["mcpo", "--port", str(settings.port), "--config", settings.config_file_path]
        if settings.use_api_key and settings.api_key:
            command.extend(["--api-key", settings.api_key])

        process_cwd = str(_get_data_dir_path())
        stdout_redir = asyncio.subprocess.DEVNULL
        stderr_redir = asyncio.subprocess.DEVNULL

        # Prepare log file redirection
        _close_log_file_handle() # Ensure previous handle is closed
        if settings.log_file_path:
            try:
                log_dir = os.path.dirname(settings.log_file_path)
                if log_dir:
                    Path(log_dir).mkdir(parents=True, exist_ok=True)
                # Use 'a' mode, line buffering
                _mcpo_log_file_handle = open(settings.log_file_path, 'a', buffering=1, encoding='utf-8', errors='ignore')
                stdout_redir = _mcpo_log_file_handle
                stderr_redir = _mcpo_log_file_handle
                logger.info(f"MCPO stdout/stderr will be redirected to {settings.log_file_path}")
            except Exception as e:
                logger.error(f"Failed to open log file '{settings.log_file_path}': {e}. Output will be redirected to DEVNULL.", exc_info=True)
                _close_log_file_handle() # Close if partially opened
                stdout_redir = asyncio.subprocess.DEVNULL
                stderr_redir = asyncio.subprocess.DEVNULL

        # Start the process using asyncio
        try:
            logger.info(f"Executing asyncio.create_subprocess_exec: {command}")
            _mcpo_process = await asyncio.create_subprocess_exec(
                *command,
                stdout=stdout_redir,
                stderr=stderr_redir,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=process_cwd,
                # On Linux/macOS, start_new_session=True makes it a group leader,
                # which helps if we ever need os.killpg (though stop_mcpo now uses process object)
                start_new_session=(sys.platform != "win32"),
                 # On Windows, CREATE_NEW_PROCESS_GROUP is often needed for reliable termination
                 # if the process spawns children outside the main process tree that Python tracks easily.
                 # However, process.terminate/kill should work on the main process object.
                 # Let's rely on process.terminate/kill first.
                 # creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            )
            await asyncio.sleep(0.5) # Short delay to check if it immediately fails

            if _mcpo_process.returncode is not None:
                 # Process exited immediately
                 msg = f"MCPO process failed to start or exited immediately (return code: {_mcpo_process.returncode}). Check logs or command."
                 logger.error(msg)
                 _mcpo_process = None # Clear the reference
                 _close_log_file_handle()
                 return False, msg

            msg = f"MCPO process successfully started. PID: {_mcpo_process.pid}."
            logger.info(msg)
            _health_check_failure_counter = 0 # Reset health check failures
            return True, msg

        except FileNotFoundError:
            msg = "Error starting mcpo: 'mcpo' command not found. Ensure mcpo is installed and in PATH."
            logger.error(msg)
            _mcpo_process = None
            _close_log_file_handle()
            return None, msg
        except PermissionError as e:
            msg = f"Error starting mcpo: Permission denied executing command or accessing CWD ({process_cwd}). Error: {e}"
            logger.error(msg)
            _mcpo_process = None
            _close_log_file_handle()
            return False, msg
        except Exception as e:
            msg = f"Unexpected error starting mcpo process: {e}"
            logger.error(msg, exc_info=True)
            _mcpo_process = None
            _close_log_file_handle()
            return False, msg

    finally:
        await asyncio.sleep(0.1)
        _mcpo_manual_operation_in_progress = False

async def stop_mcpo() -> Tuple[bool, str]:
    """Asynchronously stops the MCPO process using the stored process object."""
    global _mcpo_process, _mcpo_manual_operation_in_progress

    # if _mcpo_manual_operation_in_progress:
    #     logger.warning("Attempted to stop MCPO during another management operation. Aborted.")
    #     return False, "MCPO management operation already in progress."

    _mcpo_manual_operation_in_progress = True
    process_to_stop = _mcpo_process # Local reference

    try:
        if process_to_stop is None or process_to_stop.returncode is not None:
            msg = "MCPO process is not running or reference is lost."
            logger.warning(msg)
            _mcpo_process = None # Ensure reference is cleared
            _close_log_file_handle() # Close logs if process died unexpectedly
            return True, msg # Considered success as it's not running

        pid = process_to_stop.pid
        logger.info(f"Attempting to stop mcpo process with PID {pid}...")
        stop_successful = False
        final_message = f"Failed to stop MCPO process (PID: {pid})."

        try:
            # 1. Send SIGTERM
            logger.info(f"Sending SIGTERM to process {pid}...")
            process_to_stop.terminate()

            # 2. Wait gracefully with timeout
            try:
                await asyncio.wait_for(process_to_stop.wait(), timeout=2.0)
                final_message = f"MCPO process (PID: {pid}) stopped successfully via SIGTERM (return code: {process_to_stop.returncode})."
                logger.info(final_message)
                stop_successful = True
            except asyncio.TimeoutError:
                # 3. If SIGTERM timed out, send SIGKILL
                logger.warning(f"Process {pid} did not terminate after SIGTERM. Sending SIGKILL...")
                process_to_stop.kill()
                # 4. Wait briefly after SIGKILL
                try:
                    await asyncio.wait_for(process_to_stop.wait(), timeout=1.0)
                    final_message = f"MCPO process (PID: {pid}) stopped successfully via SIGKILL (return code: {process_to_stop.returncode})."
                    logger.info(final_message)
                    stop_successful = True
                except asyncio.TimeoutError:
                    final_message = f"ERROR: Process {pid} did not terminate even after SIGKILL within timeout. System intervention may be required."
                    logger.error(final_message)
                    stop_successful = False # Indicate failure if kill doesn't work quickly
                except Exception as e_wait_kill: # Catch other wait errors after kill
                     final_message = f"Error waiting for process {pid} after SIGKILL: {e_wait_kill}"
                     logger.error(final_message, exc_info=True)
                     stop_successful = False # Assume failure if wait errors out
            except Exception as e_wait_term: # Catch other wait errors after term
                 final_message = f"Error waiting for process {pid} after SIGTERM: {e_wait_term}"
                 logger.error(final_message, exc_info=True)
                 stop_successful = False # Assume failure if wait errors out

        except ProcessLookupError:
             # Process already died before or during signaling
            final_message = f"Process with PID {pid} not found during stop attempt (already terminated)."
            logger.warning(final_message)
            stop_successful = True # Success as it's gone
        except Exception as e_signal:
            # Errors during terminate() or kill()
            final_message = f"Error sending signal to process (PID: {pid}): {e_signal}"
            logger.error(final_message, exc_info=True)
            stop_successful = False

        # Clean up reference and log handle regardless of precise success/failure after attempts
        _mcpo_process = None
        _close_log_file_handle()
        return stop_successful, final_message

    except Exception as e_main:
         logger.error(f"Critical error in stop_mcpo function: {e_main}", exc_info=True)
         _mcpo_process = None # Try to clear reference on outer error
         _close_log_file_handle()
         return False, f"Internal error in stop_mcpo function: {e_main}"
    finally:
        await asyncio.sleep(0.1)
        _mcpo_manual_operation_in_progress = False

async def restart_mcpo_process_with_new_config(db_session: SQLModelSession, settings: McpoSettings) -> Tuple[bool, str]:
    """
    Stops mcpo (using process object), generates config, starts mcpo (creating new object).
    """
    global _mcpo_manual_operation_in_progress # Use flag to prevent interference

    # if _mcpo_manual_operation_in_progress and not settings.health_check_enabled :
    #     logger.warning("Restart process already initiated, new restart request ignored.")
    #     return False, "Restart process already in progress."

    _mcpo_manual_operation_in_progress = True
    logger.info("Starting MCPO restart process...")
    final_messages = []
    restart_success = False

    try:
        # 1. Stop the current process (if running)
        stop_success, stop_msg = await stop_mcpo() # stop_mcpo now handles the process object and flag
        final_messages.append(f"Stop: {stop_msg}")

        if not stop_success and "not running" not in stop_msg.lower():
            # If stopping failed and it wasn't because it was already stopped
            message = " | ".join(final_messages) + " CRITICAL ERROR: Failed to stop current MCPO process. Restart cancelled."
            logger.error(message)
            # Flag released by stop_mcpo's finally
            return False, message

        # 2. Generate new configuration file
        logger.info("Restart: Generating new MCPO configuration file...")
        config_generated = generate_mcpo_config_file(db_session, settings)
        if not config_generated:
            message = " | ".join(final_messages) + " ERROR: Failed to generate configuration file. MCPO start cancelled."
            logger.error(message)
            _mcpo_manual_operation_in_progress = False # Release flag as we abort before start
            return False, message
        final_messages.append("Configuration file successfully generated.")

        # 3. Start MCPO with the new configuration
        logger.info("Restart: Attempting to start MCPO with the new configuration...")
        # start_mcpo manages its own flag and resets health check counter
        start_success, start_msg = await start_mcpo(settings)
        final_messages.append(f"Start: {start_msg}")
        restart_success = start_success

    except Exception as e:
        logger.error(f"Unexpected error during MCPO restart process: {e}", exc_info=True)
        final_messages.append(f"Critical restart error: {e}")
        restart_success = False
        # Ensure flag is released if error happened outside start/stop
        # if _mcpo_manual_operation_in_progress: # Avoid releasing if already released by start/stop
        #      await asyncio.sleep(0.1)
        #      _mcpo_manual_operation_in_progress = False

    return restart_success, " | ".join(final_messages)

# --- Get Status and Logs ---

def get_mcpo_status() -> str:
    """Returns the string status of the MCPO process based on the stored object."""
    global _mcpo_process
    if _mcpo_process is None:
        # No process object reference
        return "STOPPED"
    elif _mcpo_process.returncode is None:
        # Process object exists and process hasn't returned a code
        return "RUNNING"
    else:
        # Process object exists but process has exited
        logger.warning(f"MCPO Status: Process object exists but has exited (PID: {_mcpo_process.pid}, RC: {_mcpo_process.returncode}). Reporting STOPPED.")
        _mcpo_process = None # Clear the reference to the exited process
        _close_log_file_handle()
        return "STOPPED"

async def get_mcpo_logs(lines: int = 100, log_file_path: Optional[str] = None) -> List[str]:
    """Asynchronously reads the last N lines from the MCPO log file."""
    # This function remains largely the same as it reads from a file path
    settings = load_mcpo_settings()
    actual_log_path = log_file_path or settings.log_file_path

    if not actual_log_path:
        logger.warning("Attempted to read MCPO logs, but log file path is not configured.")
        return ["Error: Log file path is not configured."]
    if not os.path.exists(actual_log_path):
        logger.warning(f"Attempted to read MCPO logs, but file not found: {actual_log_path}")
        return [f"Error: Log file not found at path: {actual_log_path}"]

    try:
        from collections import deque
        last_lines = deque(maxlen=lines)

        def read_lines_sync():
            try:
                # Open in binary, decode ignoring errors
                with open(actual_log_path, 'rb') as f:
                    for line_bytes in f:
                        last_lines.append(line_bytes.decode('utf-8', errors='ignore').rstrip())
                return list(last_lines)
            except Exception as read_e:
                logger.error(f"Error during log file read {actual_log_path} in thread: {read_e}", exc_info=True)
                return [f"Error reading logs: {read_e}"]

        return await asyncio.to_thread(read_lines_sync)

    except Exception as e:
        logger.error(f"Error preparing to read log file {actual_log_path}: {e}", exc_info=True)
        return [f"Error preparing log read: {e}"]

# --- Tool Aggregation ---
# (Remains unchanged, relies on get_mcpo_status and settings)
async def get_aggregated_tools_from_mcpo(db_session: SQLModelSession) -> Dict[str, Any]:
    """
    Aggregates tools from the running MCPO instance.
    Returns a dictionary with status, a list of servers with their tools,
    and the public base URL for generating links.
    """
    logger.info("Aggregating tools from running MCPO instance...")
    mcpo_status = get_mcpo_status()
    settings = load_mcpo_settings() # Load current settings

    # Determine base URL for links in the UI
    base_url_for_links = ""
    if settings.public_base_url:
        base_url_for_links = settings.public_base_url.rstrip('/')
        logger.debug(f"Using public base URL for links: {base_url_for_links}")
    elif mcpo_status == "RUNNING": # Use local URL only if MCPO is running
        base_url_for_links = f"http://127.0.0.1:{settings.port}"
        logger.debug(f"Public base URL not set, using local for links: {base_url_for_links}")
    else:
         logger.debug("Public base URL not set, MCPO not running, links will not be generated.")

    # Initialize result
    result: Dict[str, Any] = {
        "status": mcpo_status,
        "servers": {},
        "base_url_for_links": base_url_for_links
    }

    if mcpo_status != "RUNNING":
        logger.warning(f"Cannot aggregate tools, MCPO status: {mcpo_status}")
        return result

    # Determine internal URL for API requests to MCPO (always localhost)
    mcpo_internal_api_url = f"http://127.0.0.1:{settings.port}"
    headers = {}
    if settings.use_api_key and settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # Get enabled server definitions from DB
    enabled_definitions = get_server_definitions(db_session, only_enabled=True, limit=10000)
    if not enabled_definitions:
        logger.info("No enabled server definitions found in the database.")
        return result

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
            async with httpx.AsyncClient(headers=headers, timeout=10.0, follow_redirects=True) as client:
                logger.debug(f"Requesting OpenAPI for server '{server_name}' at URL: {url}")
                resp = await client.get(url)

                if resp.status_code == 200:
                    try:
                        openapi_data = resp.json()
                        paths = openapi_data.get("paths", {})
                        found_tools = []
                        for path, methods in paths.items():
                            if post_method_details := methods.get("post"):
                                tool_info = {
                                    "path": path,
                                    "summary": post_method_details.get("summary", ""),
                                    "description": post_method_details.get("description", "")
                                }
                                found_tools.append(tool_info)
                        server_result_data["tools"] = found_tools
                        server_result_data["status"] = "OK"
                        logger.debug(f"Server '{server_name}': Found {len(found_tools)} tools.")
                    except json.JSONDecodeError as json_e:
                         server_result_data["error_message"] = f"Error parsing JSON response from MCPO: {json_e}"
                         logger.warning(f"Error parsing OpenAPI JSON for '{server_name}' (HTTP {resp.status_code}): {resp.text[:200]}...")

                else:
                    error_text = resp.text[:200]
                    server_result_data["error_message"] = f"MCPO Error (HTTP {resp.status_code}): {error_text}"
                    logger.warning(f"Error requesting OpenAPI for '{server_name}' (HTTP {resp.status_code}): {error_text}")

        except httpx.RequestError as e:
            server_result_data["error_message"] = f"Network error: {e.__class__.__name__}"
            logger.warning(f"Network error requesting OpenAPI for '{server_name}': {e}")
        except Exception as e:
            server_result_data["error_message"] = f"Internal error: {e.__class__.__name__}"
            logger.warning(f"Error processing OpenAPI for '{server_name}': {e}", exc_info=True)

        return server_name, server_result_data
    # --- End of nested fetch_openapi function ---

    # Start requests to all servers concurrently
    tasks = [fetch_openapi(d) for d in enabled_definitions]
    fetch_results = await asyncio.gather(*tasks, return_exceptions=True) # Gather results and exceptions

    # Collect results into the final dictionary
    for i, definition in enumerate(enabled_definitions):
         server_name = definition.name
         result_item = fetch_results[i]
         if isinstance(result_item, Exception):
             logger.error(f"Exception fetching OpenAPI for '{server_name}': {result_item}", exc_info=result_item)
             result["servers"][server_name] = {"status": "ERROR", "error_message": f"Exception: {result_item.__class__.__name__}", "tools": []}
         elif isinstance(result_item, tuple) and len(result_item) == 2:
             # Expected result: tuple (server_name, server_result)
             _, server_result = result_item
             result["servers"][server_name] = server_result
         else:
              logger.error(f"Unexpected result from asyncio.gather for '{server_name}': {result_item}")
              result["servers"][server_name] = {"status": "ERROR", "error_message": "Unexpected internal result", "tools": []}

    logger.info(f"Tool aggregation finished. Processed {len(enabled_definitions)} definitions.")
    return result

# --- Health Check Logic ---

@contextlib.asynccontextmanager
async def get_async_db_session(engine_to_use=engine):
    """Async context manager for getting a DB session in background tasks."""
    # This remains unchanged, it creates a session from the engine when needed
    session = None
    try:
        session = SQLModelSession(engine_to_use)
        yield session
    except Exception as e:
        logger.error(f"Error creating DB session in background task: {e}", exc_info=True)
        raise
    finally:
        if session:
            try:
                session.close()
            except Exception as e:
                logger.error(f"Error closing DB session in background task: {e}", exc_info=True)

async def run_health_check_loop_async(get_db_session_func: Callable):
    """Asynchronous loop for periodic MCPO health checks."""
    # This loop remains largely the same, but relies on the new get_mcpo_status
    global _health_check_failure_counter, _mcpo_manual_operation_in_progress
    logger.info("Starting background MCPO health check loop...")

    await asyncio.sleep(10) # Initial delay

    while True:
        try:
             settings = load_mcpo_settings()
        except Exception as e:
             logger.error(f"Health Check: CRITICAL ERROR loading settings. Loop paused. Error: {e}", exc_info=True)
             await asyncio.sleep(60)
             continue

        if not settings.health_check_enabled:
            if _health_check_failure_counter > 0:
                logger.info("Health Check: Check disabled, resetting failure counter.")
                _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds)
            continue

        # if _mcpo_manual_operation_in_progress:
        #     logger.info("Health Check: Manual MCPO management detected, skipping check.")
        #     await asyncio.sleep(max(1, settings.health_check_failure_retry_delay_seconds // 2))
        #     continue

        # Use the updated status check (no more ERROR state from PID files)
        mcpo_status = get_mcpo_status()
        if mcpo_status != "RUNNING":
            logger.warning(f"Health Check: MCPO process not running (status: {mcpo_status}). Skipping HTTP check.")
            # Only increment counter if the process is considered unhealthy by the health check itself
            # If status is STOPPED, reset the counter
            if mcpo_status == "STOPPED" and _health_check_failure_counter > 0:
                 logger.info(f"Health Check: MCPO stopped, resetting failure counter.")
                 _health_check_failure_counter = 0
            # Note: The 'ERROR' status is gone, so we don't handle it here anymore.
            # If the process reference exists but has exited (now reported as STOPPED),
            # the health check failure handler might trigger a restart if configured.

            await asyncio.sleep(settings.health_check_interval_seconds)
            continue

        # Validate internal echo server settings
        if not settings.INTERNAL_ECHO_SERVER_NAME or not settings.INTERNAL_ECHO_TOOL_PATH:
             logger.error("Health Check: INTERNAL_ECHO_SERVER_NAME or INTERNAL_ECHO_TOOL_PATH not configured. Check cannot proceed.")
             await asyncio.sleep(settings.health_check_interval_seconds * 2)
             continue

        # Perform HTTP check (this part is unchanged)
        health_check_url = f"http://127.0.0.1:{settings.port}/{settings.INTERNAL_ECHO_SERVER_NAME.strip('/')}/{settings.INTERNAL_ECHO_TOOL_PATH.strip('/')}"
        payload = settings.INTERNAL_ECHO_PAYLOAD
        headers = {}
        if settings.use_api_key and settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"

        try:
            async with httpx.AsyncClient(headers=headers, timeout=20, follow_redirects=True) as client:
                logger.debug(f"Health Check: Sending POST to {health_check_url} (timeout: {20}s)")
                response = await client.post(health_check_url, json=payload)

            if 200 <= response.status_code < 300:
                if _health_check_failure_counter > 0:
                    logger.info(f"Health Check: SUCCESS (Status: {response.status_code}). Failure counter reset.")
                else:
                     logger.debug(f"Health Check: Success (Status: {response.status_code}).")
                _health_check_failure_counter = 0
                await asyncio.sleep(settings.health_check_interval_seconds)
            else:
                logger.warning(f"Health Check: FAILURE (Status: {response.status_code}). URL: {health_check_url}. Response: {response.text[:200]}")
                _health_check_failure_counter += 1
                await handle_health_check_failure(settings, get_db_session_func)

        except httpx.ConnectError as e:
            logger.error(f"Health Check: Connection error requesting MCPO ({health_check_url}). Error: {e}")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func)
        except httpx.TimeoutException:
            logger.error(f"Health Check: Timeout (20s) requesting MCPO ({health_check_url}).")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func)
        except httpx.RequestError as e:
            logger.error(f"Health Check: Network error requesting MCPO ({health_check_url}). Error: {e.__class__.__name__}: {e}")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func)
        except Exception as e:
            logger.error(f"Health Check: Unexpected error ({health_check_url}). Error: {e.__class__.__name__}: {e}", exc_info=True)
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func)

async def handle_health_check_failure(settings: McpoSettings, get_db_session_func: Callable):
    """Handles a failed health check, deciding if a restart is needed."""
    # This function remains the same internally, relying on the updated restart logic
    global _health_check_failure_counter, _mcpo_manual_operation_in_progress

    logger.info(f"Health Check: Failure attempt {_health_check_failure_counter} of {settings.health_check_failure_attempts}.")

    if _health_check_failure_counter >= settings.health_check_failure_attempts:
        logger.warning(f"Health Check: Reached maximum ({settings.health_check_failure_attempts}) failed check attempts.")

        if settings.auto_restart_on_failure:
            logger.info("Health Check: Auto-restart enabled. Attempting MCPO restart...")

            restart_success = False
            restart_message = "Failed to get DB session for restart."
            try:
                # Get session using the async context manager
                async with get_async_db_session() as db_session:
                    if db_session:
                        restart_success, restart_message = await restart_mcpo_process_with_new_config(db_session, settings)
                    else:
                         logger.error("Health Check: Failed to get DB session for restart. Auto-restart cancelled.")
            except Exception as e_db:
                 logger.error(f"Health Check: Error getting DB session for restart: {e_db}", exc_info=True)
                 restart_message = f"DB Session Error: {e_db}"

            if restart_success:
                logger.info(f"Health Check: MCPO successfully restarted after failures. Message: {restart_message}")
                _health_check_failure_counter = 0
                await asyncio.sleep(settings.health_check_interval_seconds)
            else:
                logger.error(f"Health Check: Automatic MCPO restart FAILED after failures. Message: {restart_message}")
                failed_restart_pause = settings.health_check_interval_seconds * 5
                logger.warning(f"Health Check: Increased pause to {failed_restart_pause}s due to failed auto-restart.")
                _health_check_failure_counter = 0
                await asyncio.sleep(failed_restart_pause)

        else: # auto_restart_on_failure is False
            logger.info("Health Check: Auto-restart disabled. Manual intervention required to restore MCPO.")
            _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds)
    else:
        # Max attempts not yet reached
        logger.info(f"Health Check: Waiting {settings.health_check_failure_retry_delay_seconds}s before next check attempt...")
        await asyncio.sleep(settings.health_check_failure_retry_delay_seconds)