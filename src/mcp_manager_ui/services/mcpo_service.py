# ================================================
# FILE: src/mcp_manager_ui/services/mcpo_service.py
# (Добавляем get_aggregated_tools_from_mcpo и импорты)
# ================================================
import asyncio
import logging
import os
import signal
import sys
import subprocess
import json # Добавляем json для парсинга openapi
from typing import Optional, Tuple, List, Dict, Any # Добавляем Dict, Any
import httpx # Добавляем httpx
from sqlmodel import Session # Добавляем Session для аннотации типа

from ..models.mcpo_settings import McpoSettings
# Импортируем из config_service необходимые функции
from .config_service import load_mcpo_settings, save_mcpo_settings, get_server_definitions

# Настройка логгера
logger = logging.getLogger(__name__)

# --- Управление состоянием процесса (без изменений) ---
PID_FILE = "mcpo_process.pid"

def _save_pid(pid: int):
    try:
        with open(PID_FILE, "w") as f: f.write(str(pid))
        logger.info(f"MCPO process PID {pid} saved to {PID_FILE}")
    except IOError as e: logger.error(f"Failed to save PID {pid} to {PID_FILE}: {e}")

def _load_pid() -> Optional[int]:
    if not os.path.exists(PID_FILE): return None
    try:
        with open(PID_FILE, "r") as f:
            pid_str = f.read().strip()
            if pid_str: return int(pid_str)
            return None
    except (IOError, ValueError) as e:
        logger.error(f"Failed to load PID from {PID_FILE}: {e}")
        _clear_pid()
        return None

def _clear_pid():
    if os.path.exists(PID_FILE):
        try: os.remove(PID_FILE); logger.info(f"PID file {PID_FILE} removed.")
        except OSError as e: logger.error(f"Failed to remove PID file {PID_FILE}: {e}")

def _is_process_running(pid: Optional[int]) -> bool:
    if pid is None: return False
    if sys.platform == "win32":
        try:
            output = subprocess.check_output(f'tasklist /nh /fi "PID eq {pid}"', stderr=subprocess.STDOUT, shell=True).decode('utf-8', errors='ignore')
            return '.exe' in output.lower()
        except subprocess.CalledProcessError: return False
        except Exception as e: logger.error(f"Error checking process {pid} status on Windows: {e}"); return False
    else:
        try: os.kill(pid, 0); return True
        except OSError: return False

def _start_mcpo_subprocess_sync(settings: McpoSettings) -> Tuple[Optional[int], str]:
    command = ["mcpo", "--port", str(settings.port), "--config", settings.config_file_path]
    # Используем API ключ, только если он задан И флаг use_api_key включен
    if settings.use_api_key and settings.api_key:
        command.extend(["--api-key", settings.api_key])
        # Добавляем strict_auth, если он будет в модели настроек
        # if settings.strict_auth: command.append("--strict-auth")
    logger.info(f"[Thread] Starting mcpo process with command: {' '.join(command)}")
    log_file = None; stdout_redir = None; stderr_redir = None
    try:
        if settings.log_file_path:
            log_file = open(settings.log_file_path, 'a', buffering=1, encoding='utf-8', errors='ignore')
            stdout_redir = log_file; stderr_redir = log_file
            logger.info(f"[Thread] Redirecting mcpo stdout/stderr to {settings.log_file_path}")
        else:
            stdout_redir = subprocess.DEVNULL; stderr_redir = subprocess.DEVNULL
            logger.info("[Thread] MCPO stdout/stderr redirected to DEVNULL.")
        creationflags = 0; startupinfo = None
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(command, stdout=stdout_redir, stderr=stderr_redir, creationflags=creationflags, startupinfo=startupinfo)
        msg = f"MCPO process started successfully with PID {process.pid}."
        logger.info(f"[Thread] {msg}")
        return process.pid, msg
    except FileNotFoundError:
        # Проверяем, установлен ли mcpo в текущем окружении
        try:
            mcpo_loc = subprocess.check_output(['where' if sys.platform == 'win32' else 'which', 'mcpo'], text=True).strip()
            msg = f"Failed to start mcpo: '{command[0]}' seems available at '{mcpo_loc}', but failed to launch. Check permissions or PATH."
        except subprocess.CalledProcessError:
             msg = f"Failed to start mcpo: '{command[0]}' command not found. Is mcpo installed and in PATH for the environment running the manager?"
        logger.error(f"[Thread] {msg}")
        if log_file: 
            try: log_file.close(); 
            except Exception: pass
        return None, msg
    except Exception as e:
        msg = f"Failed to start mcpo process in thread: {e}"
        logger.error(f"[Thread] {msg}", exc_info=True)
        if log_file: 
            try: log_file.close(); 
            except Exception: pass
        return None, msg

async def start_mcpo(settings: McpoSettings) -> Tuple[bool, str]:
    current_pid = _load_pid()
    if _is_process_running(current_pid):
        msg = f"MCPO process is already running with PID {current_pid}."
        logger.warning(msg); return False, msg
    if not os.path.exists(settings.config_file_path):
        msg = f"MCPO config file not found at {settings.config_file_path}. Generate it first."
        logger.error(msg); return False, msg
    pid, message = await asyncio.to_thread(_start_mcpo_subprocess_sync, settings)
    if pid: _save_pid(pid); return True, message
    else: _clear_pid(); return False, message

async def stop_mcpo() -> Tuple[bool, str]:
    pid = _load_pid()
    if not pid: msg = "MCPO process PID not found. Is it running?"; logger.warning(msg); return False, msg
    if not _is_process_running(pid):
        msg = f"MCPO process with PID {pid} is not running. Clearing stale PID file."; logger.warning(msg); _clear_pid(); return True, msg
    logger.info(f"Attempting to stop mcpo process with PID {pid}...")
    try:
        if sys.platform == "win32":
            logger.info(f"Sending taskkill /F /T /PID {pid} on Windows...")
            result = subprocess.run(f'taskkill /F /T /PID {pid}', shell=True, capture_output=True, text=True, check=False) # Не бросать исключение при ошибке
            logger.info(f"Taskkill result (PID: {pid}): RC={result.returncode}, stdout='{result.stdout.strip()}', stderr='{result.stderr.strip()}'")
            await asyncio.sleep(0.5)
            if not _is_process_running(pid): msg = f"MCPO process {pid} stopped via taskkill."; logger.info(msg); _clear_pid(); return True, msg
            else: msg = f"Failed to stop mcpo process {pid} using taskkill."; logger.error(msg); return False, msg # Не пытаемся os.kill, если taskkill не помог
        else: # Unix-like
            logger.info(f"Sending SIGTERM to PID {pid} on Unix-like system...")
            os.kill(pid, signal.SIGTERM); await asyncio.sleep(1)
            if _is_process_running(pid):
                logger.warning(f"Process {pid} did not terminate gracefully. Sending SIGKILL."); os.kill(pid, signal.SIGKILL); await asyncio.sleep(0.5)
            if not _is_process_running(pid): msg = f"MCPO process {pid} stopped."; logger.info(msg); _clear_pid(); return True, msg
            else: msg = f"Failed to stop mcpo process {pid} even after SIGKILL."; logger.error(msg); return False, msg
    except ProcessLookupError: msg = f"Process {pid} not found while trying to stop."; logger.warning(msg); _clear_pid(); return True, msg
    except Exception as e: msg = f"Error stopping mcpo process {pid}: {e}"; logger.error(msg, exc_info=True); return False, msg

def get_mcpo_status() -> str:
    pid = _load_pid()
    if pid is None: return "STOPPED"
    if _is_process_running(pid): return "RUNNING"
    else: logger.warning(f"MCPO PID {pid} found, but process not running."); return "ERROR"

async def get_mcpo_logs(lines: int = 20, log_file_path: Optional[str] = None) -> List[str]:
    settings = load_mcpo_settings()
    actual_log_path = log_file_path or settings.log_file_path
    if not actual_log_path: return ["Log file path is not configured in settings."]
    if not os.path.exists(actual_log_path): return [f"Log file not found at: {actual_log_path}"]
    try:
        from collections import deque
        last_lines = deque(maxlen=lines)
        with open(actual_log_path, 'rb') as f:
            for line_bytes in f:
                line_str = line_bytes.decode('utf-8', errors='ignore').rstrip()
                last_lines.append(line_str)
        return list(last_lines)
    except Exception as e: logger.error(f"Error reading log file {actual_log_path}: {e}"); return [f"Error reading log file: {e}"]

# --- НОВОЕ: Получение агрегированного списка инструментов ---
async def get_aggregated_tools_from_mcpo(db: Session) -> Dict[str, Any]:
    """
    Запрашивает openapi.json у запущенного mcpo для каждого включенного сервера
    и возвращает агрегированный список инструментов или информацию об ошибках.

    Возвращает словарь:
    {
        "status": "RUNNING" | "STOPPED" | "ERROR",
        "servers": {
            "server_name": {
                "status": "OK" | "ERROR",
                "error_message": Optional[str],
                "tools": [
                    {"path": "/tool_path", "summary": "...", "description": "..."}
                ]
            },
            ...
        }
    }
    """
    logger.info("Aggregating tools from running MCPO instance...")
    mcpo_status = get_mcpo_status()
    result: Dict[str, Any] = {"status": mcpo_status, "servers": {}}

    if mcpo_status != "RUNNING":
        logger.warning(f"Cannot aggregate tools, MCPO status is {mcpo_status}")
        return result

    settings = load_mcpo_settings()
    mcpo_base_url = f"http://127.0.0.1:{settings.port}"
    headers = {}
    # Добавляем заголовок авторизации, если ключ используется
    if settings.use_api_key and settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"
        logger.info("Using API Key for fetching OpenAPI specs.")

    # Получаем только включенные определения
    enabled_definitions = get_server_definitions(db, only_enabled=True, limit=1000)
    if not enabled_definitions:
        logger.info("No enabled server definitions found in DB.")
        return result # Возвращаем статус RUNNING, но пустой список серверов

    async def fetch_openapi(definition):
        server_name = definition.name
        url = f"{mcpo_base_url}/{server_name}/openapi.json"
        server_result = {"status": "ERROR", "error_message": None, "tools": []}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
                logger.debug(f"Fetching OpenAPI spec for '{server_name}' from {url}")
                response = await client.get(url)
                if response.status_code == 200:
                    openapi_data = response.json()
                    paths = openapi_data.get("paths", {})
                    for path, methods in paths.items():
                        # Обычно MCP инструменты используют POST
                        method_details = methods.get("post")
                        if method_details:
                            server_result["tools"].append({
                                "path": path, # Относительный путь, например, /get_time
                                "summary": method_details.get("summary", "No summary"),
                                "description": method_details.get("description", "No description"),
                            })
                    server_result["status"] = "OK"
                    logger.info(f"Successfully fetched and parsed {len(server_result['tools'])} tools for '{server_name}'.")
                else:
                     error_msg = f"HTTP {response.status_code}: {response.text[:200]}" # Ограничим длину ошибки
                     server_result["error_message"] = error_msg
                     logger.error(f"Failed to fetch OpenAPI for '{server_name}': {error_msg}")

        except httpx.RequestError as e:
             error_msg = f"Request error: {e.__class__.__name__}"
             server_result["error_message"] = error_msg
             logger.error(f"Request error fetching OpenAPI for '{server_name}': {error_msg}")
        except json.JSONDecodeError:
             error_msg = "Invalid JSON in OpenAPI response"
             server_result["error_message"] = error_msg
             logger.error(f"Invalid JSON received for '{server_name}' OpenAPI spec.")
        except Exception as e:
             error_msg = f"Unexpected error: {e.__class__.__name__}"
             server_result["error_message"] = error_msg
             logger.error(f"Unexpected error processing '{server_name}': {e}", exc_info=True)

        return server_name, server_result

    # Запускаем запросы параллельно
    tasks = [fetch_openapi(definition) for definition in enabled_definitions]
    results = await asyncio.gather(*tasks)

    # Собираем результаты в итоговый словарь
    for server_name, server_result in results:
        result["servers"][server_name] = server_result

    logger.info("Finished aggregating tools.")
    return result
# --- КОНЕЦ НОВОГО БЛОКА ---