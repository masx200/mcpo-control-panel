# ================================================
# FILE: src/mcp_manager_ui/services/mcpo_service.py
# (Полная версия с обновленной get_aggregated_tools_from_mcpo)
# ================================================
import asyncio
import logging
import os
import signal
import sys
import subprocess
import json
from typing import Optional, Tuple, List, Dict, Any
import httpx # Для health check и запросов openapi
from sqlmodel import Session as SQLModelSession # Чтобы не конфликтовать с FastAPI Session

from ..models.mcpo_settings import McpoSettings
from .config_service import load_mcpo_settings, generate_mcpo_config_file, get_server_definitions
from ..db.database import engine # Импортируем engine напрямую для фоновых задач

logger = logging.getLogger(__name__)
PID_FOLDER = "mcpo_manager_data"
if not os.path.exists(PID_FOLDER):
    os.makedirs(PID_FOLDER)
PID_FILE = f"{PID_FOLDER}/mcpo_process.pid"

# --- Переменные состояния для Health Check ---
_health_check_failure_counter = 0
_mcpo_manual_restart_in_progress = False # Флаг, чтобы health checker не мешал ручному рестарту/старту/стопу

# --- Управление состоянием процесса (PID файл) ---

def _save_pid(pid: int):
    """Сохраняет PID процесса MCPO в файл."""
    try:
        with open(PID_FILE, "w") as f:
            f.write(str(pid))
        logger.info(f"PID процесса MCPO {pid} сохранен в {PID_FILE}")
    except IOError as e:
        logger.error(f"Ошибка сохранения PID {pid} в {PID_FILE}: {e}")

def _load_pid() -> Optional[int]:
    """Загружает PID процесса MCPO из файла."""
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE, "r") as f:
            pid_str = f.read().strip()
            if pid_str:
                return int(pid_str)
            return None
    except (IOError, ValueError) as e:
        logger.error(f"Ошибка загрузки PID из {PID_FILE}: {e}")
        _clear_pid() # Очищаем некорректный файл
        return None

def _clear_pid():
    """Удаляет PID файл."""
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
            logger.info(f"PID файл {PID_FILE} удален.")
        except OSError as e:
            logger.error(f"Ошибка удаления PID файла {PID_FILE}: {e}")

def _is_process_running(pid: Optional[int]) -> bool:
    """Проверяет, запущен ли процесс с заданным PID."""
    if pid is None:
        return False
    if sys.platform == "win32":
        # Проверка на Windows через tasklist
        try:
            # /nh - без заголовка, /fi - фильтр по PID
            output = subprocess.check_output(
                f'tasklist /nh /fi "PID eq {pid}"',
                stderr=subprocess.STDOUT,
                shell=True
            ).decode('utf-8', errors='ignore')
            # Если процесс найден, вывод будет содержать имя .exe
            return '.exe' in output.lower()
        except subprocess.CalledProcessError:
            # Команда завершилась с ошибкой (процесс не найден)
            return False
        except Exception as e:
            # Другие ошибки (например, проблемы с правами)
            logger.error(f"Ошибка проверки статуса процесса {pid} на Windows: {e}")
            return False # На всякий случай считаем, что не запущен
    else:
        # Проверка на Unix-like системах через kill -0
        try:
            os.kill(pid, 0) # Отправка сигнала 0 не влияет на процесс, но проверяет его существование
            return True
        except OSError:
            # Процесс не найден
            return False

# --- Запуск/Остановка/Перезапуск MCPO ---

def _start_mcpo_subprocess_sync(settings: McpoSettings) -> Tuple[Optional[int], str]:
    """
    Синхронная функция для запуска процесса mcpo в отдельном потоке/процессе.
    Вызывается через asyncio.to_thread.
    """
    command = ["mcpo", "--port", str(settings.port), "--config", settings.config_file_path]
    if settings.use_api_key and settings.api_key:
        command.extend(["--api-key", settings.api_key])

    logger.info(f"[Поток/Subprocess] Запуск процесса mcpo: {' '.join(command)}")

    log_file = None
    stdout_redir = subprocess.DEVNULL # По умолчанию вывод в никуда
    stderr_redir = subprocess.DEVNULL

    try:
        # Настройка перенаправления вывода в лог-файл, если он указан
        if settings.log_file_path:
            log_dir = os.path.dirname(settings.log_file_path)
            if log_dir and not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    logger.info(f"[Поток/Subprocess] Создана директория для логов: {log_dir}")
                except OSError as e:
                    logger.error(f"[Поток/Subprocess] Не удалось создать директорию для логов '{log_dir}': {e}. Вывод будет перенаправлен в DEVNULL.")
                    # Не прерываем запуск, просто логи не будут писаться
            if not log_dir or os.path.exists(log_dir): # Только если директория существует или не нужна
                try:
                    # 'a' - дозапись, buffering=1 - построчная буферизация
                    log_file = open(settings.log_file_path, 'a', buffering=1, encoding='utf-8', errors='ignore')
                    stdout_redir = log_file
                    stderr_redir = log_file
                    logger.info(f"[Поток/Subprocess] stdout/stderr mcpo будут перенаправлены в {settings.log_file_path}")
                except IOError as e:
                    logger.error(f"[Поток/Subprocess] Не удалось открыть лог-файл '{settings.log_file_path}': {e}. Вывод будет перенаправлен в DEVNULL.")

        # Флаги для Popen (важно для корректного завершения на Windows)
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP # Позволяет убить всю группу процессов taskkill /T

        # Запускаем процесс
        process = subprocess.Popen(
            command,
            stdout=stdout_redir,
            stderr=stderr_redir,
            creationflags=creationflags
            # stdin=subprocess.DEVNULL # Можно добавить, если точно не нужен ввод
        )
        msg = f"Процесс MCPO успешно запущен с PID {process.pid}."
        logger.info(f"[Поток/Subprocess] {msg}")
        return process.pid, msg

    except FileNotFoundError:
        # Команда mcpo не найдена
        msg = f"Ошибка запуска mcpo: команда 'mcpo' не найдена. Убедитесь, что mcpo установлен и доступен в системном PATH для пользователя, от которого запущен менеджер."
        logger.error(f"[Поток/Subprocess] {msg}")
        return None, msg
    except Exception as e:
        # Другие ошибки при запуске Popen
        msg = f"Непредвиденная ошибка при запуске процесса mcpo: {e}"
        logger.error(f"[Поток/Subprocess] {msg}", exc_info=True)
        return None, msg
    finally:
        # Важно закрыть файл логов, если он был открыт в этой функции
        # (хотя Popen может держать его открытым, лучше закрыть дескриптор здесь)
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass # Игнорируем ошибки при закрытии

async def start_mcpo(settings: McpoSettings) -> Tuple[bool, str]:
    """Асинхронно запускает процесс MCPO, если он еще не запущен."""
    global _mcpo_manual_restart_in_progress, _health_check_failure_counter
    _mcpo_manual_restart_in_progress = True # Сигнализируем health checker'у о ручном действии
    try:
        current_pid = _load_pid()
        if _is_process_running(current_pid):
            msg = f"Процесс MCPO уже запущен с PID {current_pid}."
            logger.warning(msg)
            return False, msg

        # Проверяем наличие конфиг-файла перед запуском
        if not os.path.exists(settings.config_file_path):
            msg = f"Файл конфигурации MCPO не найден: {settings.config_file_path}. Запуск невозможен. Сгенерируйте его (например, через 'Применить и Перезапустить')."
            logger.error(msg)
            return False, msg

        logger.info(f"Попытка запуска mcpo с настройками: порт={settings.port}, конфиг='{settings.config_file_path}'...")

        # Запускаем синхронную функцию _start_mcpo_subprocess_sync в отдельном потоке
        pid, message = await asyncio.to_thread(_start_mcpo_subprocess_sync, settings)

        if pid:
            _save_pid(pid)
            _health_check_failure_counter = 0 # Сбрасываем счетчик неудач Health Check после успешного ручного старта
            logger.info(f"MCPO запущен. {message}")
            return True, message
        else:
            _clear_pid() # Очищаем PID файл, если запуск не удался
            logger.error(f"Не удалось запустить MCPO. {message}")
            return False, message
    finally:
        await asyncio.sleep(0.1) # Небольшая пауза перед снятием флага
        _mcpo_manual_restart_in_progress = False

async def stop_mcpo() -> Tuple[bool, str]:
    """Асинхронно останавливает процесс MCPO, если он запущен."""
    global _mcpo_manual_restart_in_progress
    _mcpo_manual_restart_in_progress = True # Сигнализируем health checker'у
    try:
        pid = _load_pid()
        if not pid:
            msg = "PID процесса MCPO не найден (в файле). Возможно, он не был запущен через менеджер или остановлен ранее."
            logger.warning(msg)
            return False, msg # Считаем неудачей, т.к. не можем подтвердить остановку

        if not _is_process_running(pid):
            msg = f"Процесс MCPO с PID {pid} (из файла) не найден в системе. Очистка устаревшего PID файла."
            logger.warning(msg)
            _clear_pid()
            return True, msg # Считаем успехом, т.к. процесса нет

        logger.info(f"Попытка остановки процесса mcpo с PID {pid}...")
        try:
            if sys.platform == "win32":
                # На Windows используем taskkill с /F (force) и /T (tree - убить дочерние процессы)
                logger.info(f"Отправка команды: taskkill /F /T /PID {pid}")
                # Используем subprocess.run для синхронного выполнения taskkill
                result = await asyncio.to_thread(
                    subprocess.run,
                    f'taskkill /F /T /PID {pid}',
                    shell=True, capture_output=True, text=True, check=False
                )
                logger.info(f"Результат Taskkill (PID: {pid}): RC={result.returncode}, stdout='{result.stdout.strip()}', stderr='{result.stderr.strip()}'")
                await asyncio.sleep(0.5) # Даем время процессу завершиться
                if not _is_process_running(pid):
                    msg = f"Процесс MCPO (PID: {pid}) успешно остановлен через taskkill."
                    logger.info(msg)
                    _clear_pid()
                    return True, msg
                else:
                    # Если taskkill не сработал (маловероятно с /F)
                    msg = f"Не удалось остановить процесс MCPO (PID: {pid}) с помощью taskkill. Проверьте права доступа."
                    logger.error(msg)
                    return False, msg
            else:
                # На Unix-like системах сначала пробуем SIGTERM, потом SIGKILL
                logger.info(f"Отправка сигнала SIGTERM процессу с PID {pid}...")
                os.kill(pid, signal.SIGTERM)
                await asyncio.sleep(1) # Ждем завершения

                if _is_process_running(pid):
                    logger.warning(f"Процесс {pid} не завершился после SIGTERM. Отправка SIGKILL...")
                    os.kill(pid, signal.SIGKILL)
                    await asyncio.sleep(0.5) # Короткая пауза после SIGKILL

                if not _is_process_running(pid):
                    msg = f"Процесс MCPO (PID: {pid}) успешно остановлен."
                    logger.info(msg)
                    _clear_pid()
                    return True, msg
                else:
                    # Если даже SIGKILL не помог (очень странно)
                    msg = f"Не удалось остановить процесс MCPO (PID: {pid}) даже после SIGKILL."
                    logger.error(msg)
                    return False, msg

        except ProcessLookupError:
            # Процесс уже не существует, когда мы пытались его остановить
            msg = f"Процесс с PID {pid} не найден при попытке остановки (возможно, уже завершился)."
            logger.warning(msg)
            _clear_pid()
            return True, msg # Считаем успехом
        except Exception as e:
            # Другие ошибки при остановке
            msg = f"Ошибка при остановке процесса mcpo (PID: {pid}): {e}"
            logger.error(msg, exc_info=True)
            return False, msg
    finally:
        await asyncio.sleep(0.1)
        _mcpo_manual_restart_in_progress = False

async def restart_mcpo_process_with_new_config(db_session: SQLModelSession, settings: McpoSettings) -> Tuple[bool, str]:
    """
    Останавливает mcpo, генерирует новый стандартный конфиг и запускает mcpo.
    Используется кнопкой "Применить и Перезапустить" и Health Checker'ом.
    """
    global _mcpo_manual_restart_in_progress
    # Дополнительная проверка, чтобы избежать случайной рекурсии, если вызвано не health_checker'ом
    # Health Checker сам проверяет флаг перед вызовом рестарта
    if _mcpo_manual_restart_in_progress and not settings.health_check_enabled :
        logger.warning("Процесс перезапуска уже инициирован, новый запрос на перезапуск проигнорирован.")
        return False, "Процесс перезапуска уже выполняется."

    # Устанавливаем флаг в начале, он будет снят в finally блоков start/stop
    _mcpo_manual_restart_in_progress = True
    logger.info("Начало процесса перезапуска MCPO...")
    final_messages = []

    # 1. Останавливаем текущий процесс (если запущен)
    current_pid = _load_pid()
    if _is_process_running(current_pid):
        logger.info(f"Перезапуск: Обнаружен работающий процесс MCPO (PID: {current_pid}). Попытка остановки...")
        stop_success, stop_msg = await stop_mcpo() # stop_mcpo управляет флагом и очищает PID
        final_messages.append(f"Остановка: {stop_msg}")
        if not stop_success:
            # Если остановить не удалось, прерываем перезапуск
            message = " | ".join(final_messages) + " Критическая ошибка: Не удалось остановить текущий процесс MCPO. Перезапуск отменен."
            logger.error(message)
            # Флаг _mcpo_manual_restart_in_progress должен быть снят в finally stop_mcpo
            return False, message
    else:
        logger.info("Перезапуск: Работающий процесс MCPO не обнаружен, переход к генерации конфига.")
        _clear_pid() # На всякий случай чистим PID файл, если процесс не найден

    # 2. Генерируем новый конфигурационный файл
    logger.info("Перезапуск: Генерация нового файла конфигурации MCPO...")
    # Используем стандартную генерацию (без Windows адаптации) для файла, который будет читать mcpo
    if not generate_mcpo_config_file(db_session, settings):
        message = " | ".join(final_messages) + " Ошибка: Не удалось сгенерировать файл конфигурации. Запуск MCPO отменен."
        logger.error(message)
        # Флаг _mcpo_manual_restart_in_progress должен быть снят в finally stop_mcpo (если он вызывался)
        # или его нужно снять здесь, если остановка не требовалась
        _mcpo_manual_restart_in_progress = False # Снимаем флаг, т.к. прерываемся
        return False, message
    final_messages.append("Файл конфигурации успешно сгенерирован.")

    # 3. Запускаем MCPO с новой конфигурацией
    logger.info("Перезапуск: Попытка запуска MCPO с новой конфигурацией...")
    # start_mcpo сама установит и снимет флаг _mcpo_manual_restart_in_progress и сбросит счетчик health check
    start_success, start_msg = await start_mcpo(settings)
    final_messages.append(f"Запуск: {start_msg}")

    # Возвращаем результат запуска и собранные сообщения
    return start_success, " | ".join(final_messages)

# --- Получение статуса и логов ---

def get_mcpo_status() -> str:
    """Возвращает строковый статус процесса MCPO: RUNNING, STOPPED, ERROR."""
    pid = _load_pid()
    if pid is None:
        return "STOPPED" # PID файл не найден

    if _is_process_running(pid):
        return "RUNNING" # Процесс с PID из файла найден и работает
    else:
        # PID файл есть, но процесс не найден - это ошибка
        logger.warning(f"Статус MCPO: PID {pid} найден в файле, но соответствующий процесс не выполняется. Статус: ERROR")
        return "ERROR"

async def get_mcpo_logs(lines: int = 100, log_file_path: Optional[str] = None) -> List[str]:
    """Асинхронно читает последние N строк из лог-файла MCPO."""
    # Загружаем настройки, чтобы получить путь к логу, если он не передан явно
    settings = load_mcpo_settings()
    actual_log_path = log_file_path or settings.log_file_path

    if not actual_log_path:
        return ["Ошибка: Путь к файлу логов не настроен."]
    if not os.path.exists(actual_log_path):
        return [f"Ошибка: Файл логов не найден по пути: {actual_log_path}"]

    try:
        # Используем deque для эффективного хранения последних строк
        from collections import deque
        last_lines = deque(maxlen=lines)

        # Читаем файл построчно в бинарном режиме и декодируем с игнорированием ошибок
        # Это более надежно для потенциально поврежденных логов
        def read_lines_sync():
            with open(actual_log_path, 'rb') as f:
                for line_bytes in f:
                    last_lines.append(line_bytes.decode('utf-8', errors='ignore').rstrip())
            return list(last_lines)

        # Выполняем синхронное чтение в отдельном потоке
        return await asyncio.to_thread(read_lines_sync)

    except Exception as e:
        logger.error(f"Ошибка чтения файла логов {actual_log_path}: {e}", exc_info=True)
        return [f"Ошибка чтения логов: {e}"]

# --- Агрегация инструментов ---

async def get_aggregated_tools_from_mcpo(db_session: SQLModelSession) -> Dict[str, Any]:
    """
    Агрегирует инструменты из запущенного экземпляра MCPO.
    Возвращает словарь с статусом, списком серверов с их инструментами
    и публичным базовым URL для генерации ссылок.
    """
    logger.info("Агрегация инструментов из запущенного экземпляра MCPO...")
    mcpo_status = get_mcpo_status()
    settings = load_mcpo_settings() # Загружаем актуальные настройки

    # Определяем базовый URL, который будет использоваться для генерации ссылок в UI
    if settings.public_base_url:
        # Используем заданный публичный URL, убираем слэш в конце
        base_url_for_links = settings.public_base_url.rstrip('/')
        logger.debug(f"Используется публичный базовый URL для ссылок: {base_url_for_links}")
    else:
        # Если публичный URL не задан, используем локальный адрес и порт
        base_url_for_links = f"http://127.0.0.1:{settings.port}"
        logger.debug(f"Публичный базовый URL не задан, для ссылок используется локальный: {base_url_for_links}")

    # Инициализируем результат, сразу добавляя статус и базовый URL
    result: Dict[str, Any] = {
        "status": mcpo_status,
        "servers": {},
        "base_url_for_links": base_url_for_links # Этот URL будет использоваться в шаблоне tools.html
    }

    # Если MCPO не запущен, нет смысла идти дальше
    if mcpo_status != "RUNNING":
        logger.warning(f"Невозможно агрегировать инструменты, статус MCPO: {mcpo_status}")
        return result # Возвращаем результат с текущим статусом и URL

    # Определяем внутренний URL для запросов к API самого MCPO (всегда localhost)
    mcpo_internal_api_url = f"http://127.0.0.1:{settings.port}"
    headers = {}
    if settings.use_api_key and settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # Получаем список включенных определений серверов из БД
    enabled_definitions = get_server_definitions(db_session, only_enabled=True, limit=10000) # Берем все включенные
    if not enabled_definitions:
        logger.info("Не найдено включенных определений серверов в базе данных.")
        return result # Возвращаем результат с текущим статусом, URL и пустым списком серверов

    # --- Вложенная асинхронная функция для получения OpenAPI спеки одного сервера ---
    async def fetch_openapi(definition):
        server_name = definition.name
        # Пропускаем запрос для внутреннего эхо-сервера Health Check
        if server_name == settings.INTERNAL_ECHO_SERVER_NAME and settings.health_check_enabled:
            return server_name, {"status": "SKIPPED", "error_message": "Внутренний эхо-сервер (пропущен).", "tools": []}

        # Формируем URL для запроса openapi.json к MCPO
        url = f"{mcpo_internal_api_url}/{server_name}/openapi.json"
        server_result_data = {"status": "ERROR", "error_message": None, "tools": []}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10.0) as client:
                logger.debug(f"Запрос OpenAPI для сервера '{server_name}' по URL: {url}")
                resp = await client.get(url)

                if resp.status_code == 200:
                    openapi_data = resp.json()
                    paths = openapi_data.get("paths", {})
                    found_tools = []
                    for path, methods in paths.items():
                        # Ищем только POST методы (основной способ вызова в MCP)
                        if post_method_details := methods.get("post"):
                            tool_info = {
                                "path": path, # Путь к инструменту (напр., "/calculate")
                                "summary": post_method_details.get("summary", ""),
                                "description": post_method_details.get("description", "")
                            }
                            found_tools.append(tool_info)
                    server_result_data["tools"] = found_tools
                    server_result_data["status"] = "OK"
                    logger.debug(f"Сервер '{server_name}': Найдено {len(found_tools)} инструментов.")
                else:
                    # Ошибка при запросе к MCPO
                    error_text = resp.text[:200] # Ограничиваем длину текста ошибки
                    server_result_data["error_message"] = f"Ошибка MCPO (HTTP {resp.status_code}): {error_text}"
                    logger.warning(f"Ошибка при запросе OpenAPI для '{server_name}' (HTTP {resp.status_code}): {error_text}")

        except httpx.RequestError as e:
            # Ошибка сети при запросе к MCPO
            server_result_data["error_message"] = f"Ошибка сети: {e.__class__.__name__}"
            logger.warning(f"Ошибка сети при запросе OpenAPI для '{server_name}': {e}")
        except Exception as e:
            # Другие ошибки (например, JSONDecodeError)
            server_result_data["error_message"] = f"Внутренняя ошибка: {e.__class__.__name__}"
            logger.warning(f"Ошибка обработки OpenAPI для '{server_name}': {e}", exc_info=True)

        return server_name, server_result_data
    # --- Конец вложенной функции fetch_openapi ---

    # Запускаем запросы ко всем серверам параллельно
    tasks = [fetch_openapi(d) for d in enabled_definitions]
    fetch_results = await asyncio.gather(*tasks)

    # Собираем результаты в итоговый словарь
    for server_name, server_result in fetch_results:
        result["servers"][server_name] = server_result

    logger.info(f"Агрегация инструментов завершена. Обработано {len(enabled_definitions)} определений.")
    return result

# --- Логика Health Check ---

async def run_health_check_loop_async(get_db_session_func: callable):
    """Асинхронный цикл для периодической проверки работоспособности MCPO."""
    global _health_check_failure_counter, _mcpo_manual_restart_in_progress
    logger.info("Запуск фонового цикла проверки работоспособности MCPO...")

    await asyncio.sleep(5) # Небольшая пауза перед первой проверкой

    while True:
        settings = load_mcpo_settings() # Загружаем актуальные настройки на каждой итерации

        if not settings.health_check_enabled:
            #logger.debug("Health Check: Проверка отключена в настройках.")
            # Сбрасываем счетчик, если проверка выключена
            if _health_check_failure_counter > 0:
                logger.info("Health Check: Проверка отключена, сброс счетчика неудач.")
                _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds) # Ждем обычный интервал перед следующей проверкой настроек
            continue

        if _mcpo_manual_restart_in_progress:
            logger.info("Health Check: Обнаружено ручное управление MCPO, проверка пропускается.")
            await asyncio.sleep(settings.health_check_failure_retry_delay_seconds) # Короткая пауза
            continue

        mcpo_status = get_mcpo_status()
        if mcpo_status != "RUNNING":
            #logger.warning(f"Health Check: MCPO не запущен (статус: {mcpo_status}). Проверка пропускается.")
            # Сбрасываем счетчик, если mcpo не работает (чтобы не накапливались ошибки, пока он остановлен)
            if _health_check_failure_counter > 0:
                 logger.info(f"Health Check: MCPO не запущен (статус: {mcpo_status}), сброс счетчика неудач.")
                 _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds) # Ждем обычный интервал
            continue

        # Формируем URL и payload для запроса к внутреннему эхо-серверу через MCPO
        health_check_url = f"http://127.0.0.1:{settings.port}/{settings.INTERNAL_ECHO_SERVER_NAME}{settings.INTERNAL_ECHO_TOOL_PATH}"
        payload = settings.INTERNAL_ECHO_PAYLOAD
        headers = {}
        if settings.use_api_key and settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"

        try:
            async with httpx.AsyncClient(headers=headers, timeout=5.0) as client: # Таймаут для health check запроса
                logger.debug(f"Health Check: Отправка POST запроса на {health_check_url}")
                response = await client.post(health_check_url, json=payload)

            if 200 <= response.status_code < 300:
                # Проверка успешна
                if _health_check_failure_counter > 0:
                    logger.info(f"Health Check: Успешно (Статус: {response.status_code}). Счетчик неудач сброшен.")
                else:
                     logger.debug(f"Health Check: Успешно (Статус: {response.status_code}).")
                _health_check_failure_counter = 0
                await asyncio.sleep(settings.health_check_interval_seconds) # Ждем обычный интервал до следующей проверки
            else:
                # Проверка не удалась (не 2xx ответ)
                logger.warning(f"Health Check: НЕУДАЧА (Статус: {response.status_code}). Ответ: {response.text[:200]}")
                _health_check_failure_counter += 1
                await handle_health_check_failure(settings, get_db_session_func) # Обрабатываем неудачу

        except httpx.RequestError as e:
            # Ошибка сети при запросе
            logger.error(f"Health Check: Ошибка сети при запросе к MCPO ({e.__class__.__name__}: {e}).")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func) # Обрабатываем неудачу
        except Exception as e:
            # Другие непредвиденные ошибки
            logger.error(f"Health Check: Непредвиденная ошибка ({e.__class__.__name__}: {e}).", exc_info=True)
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings, get_db_session_func) # Обрабатываем неудачу

async def handle_health_check_failure(settings: McpoSettings, get_db_session_func: callable):
    """Обрабатывает неудачную проверку работоспособности, решает, нужно ли перезапускать."""
    global _health_check_failure_counter, _mcpo_manual_restart_in_progress

    logger.info(f"Health Check: Попытка неудачи {_health_check_failure_counter} из {settings.health_check_failure_attempts}.")

    if _health_check_failure_counter >= settings.health_check_failure_attempts:
        logger.warning(f"Health Check: Достигнуто максимальное количество ({settings.health_check_failure_attempts}) неудачных попыток проверки.")

        if settings.auto_restart_on_failure:
            logger.info("Health Check: Включен автоматический перезапуск. Попытка перезапуска MCPO...")

            # Получаем сессию БД асинхронно для генерации конфига внутри рестарта
            async with get_async_db_session(get_db_session_func) as db_session:
                if db_session:
                    # Вызываем функцию рестарта, она управляет флагом _mcpo_manual_restart_in_progress
                    success, message = await restart_mcpo_process_with_new_config(db_session, settings)
                    if success:
                        logger.info(f"Health Check: MCPO успешно перезапущен после серии неудач. Сообщение: {message}")
                        _health_check_failure_counter = 0 # Сброс счетчика после успешного рестарта
                        await asyncio.sleep(settings.health_check_interval_seconds) # Ждем обычный интервал
                    else:
                        logger.error(f"Health Check: Автоматический перезапуск MCPO НЕ УДАЛСЯ после серии неудач. Сообщение: {message}")
                        # После неудачного рестарта, можно увеличить паузу или предпринять другие действия
                        # Пока просто сбрасываем счетчик и ждем дольше перед следующей проверкой
                        _health_check_failure_counter = 0
                        await asyncio.sleep(settings.health_check_interval_seconds * 3) # Утроенная пауза
                else:
                    logger.error("Health Check: Не удалось получить сессию БД для перезапуска. Автоматический перезапуск отменен.")
                    _health_check_failure_counter = 0 # Сбрасываем счетчик
                    await asyncio.sleep(settings.health_check_interval_seconds * 3) # Ждем дольше

        else: # auto_restart_on_failure == False
            logger.info("Health Check: Автоматический перезапуск отключен. Требуется ручное вмешательство для восстановления MCPO.")
            # Сбрасываем счетчик, чтобы не спамить лог о "Max attempts" каждую секунду
            _health_check_failure_counter = 0
            await asyncio.sleep(settings.health_check_interval_seconds) # Ждем обычный интервал до следующей проверки (которая, вероятно, тоже упадет)
    else:
        # Если максимальное количество попыток еще не достигнуто
        logger.info(f"Health Check: Ожидание {settings.health_check_failure_retry_delay_seconds} сек перед следующей попыткой проверки...")
        await asyncio.sleep(settings.health_check_failure_retry_delay_seconds)



# Вспомогательная функция-контекстный менеджер для асинхронного получения сессии БД в фоновой задаче
import contextlib

@contextlib.asynccontextmanager
async def get_async_db_session(get_db_session_func: callable = None) -> SQLModelSession:
    """
    Асинхронный контекстный менеджер для получения сессии БД в фоновых задачах.
    Использует глобальный engine.
    """
    session = None
    try:
        # Создаем новую сессию напрямую из engine для этой операции
        # Это самый простой способ для фоновых задач, не привязанных к HTTP-запросу
        session = SQLModelSession(engine)
        yield session
    except Exception as e:
        logger.error(f"Ошибка при создании сессии БД в фоновой задаче: {e}", exc_info=True)
        # Пробрасываем исключение дальше, если нужно, или возвращаем None/пустую сессию
        # В данном случае, если сессия не создалась, yield не сработает, и with завершится
        raise # Перевыбрасываем ошибку, чтобы вызывающий код мог ее обработать
    finally:
        if session:
            try:
                session.close()
            except Exception as e:
                logger.error(f"Ошибка при закрытии сессии БД в фоновой задаче: {e}", exc_info=True)