# ================================================
# FILE: mcpo_control_panel/services/mcpo_service.py
# (Полная версия с улучшенной обработкой процессов и логгированием)
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
import errno # For checking OS errors

from ..models.mcpo_settings import McpoSettings
from .config_service import load_mcpo_settings, generate_mcpo_config_file, get_server_definitions
from ..db.database import engine # Import engine directly for background tasks

from pathlib import Path # Added Path
import contextlib # Added for async session helper

logger = logging.getLogger(__name__)

# --- Константы и пути ---
DEFAULT_DATA_DIR_NAME_FOR_SERVICE = ".mcpo_manager_data" # Consistent default
PID_FILENAME = "mcpo_process.pid"

def _get_data_dir_path() -> Path:
    """Определяет путь к директории данных менеджера."""
    effective_data_dir_str = os.getenv("MCPO_MANAGER_DATA_DIR_EFFECTIVE")
    if effective_data_dir_str:
        data_dir = Path(effective_data_dir_str)
    else:
        # Fallback to a default directory if the env var is not set
        data_dir = Path.home() / DEFAULT_DATA_DIR_NAME_FOR_SERVICE
    data_dir.mkdir(parents=True, exist_ok=True) # Ensure the directory exists
    return data_dir

def _get_pid_file_path() -> str:
    """Определяет полный путь к PID-файлу."""
    return str(_get_data_dir_path() / PID_FILENAME)

PID_FILE = _get_pid_file_path() # Динамически установленный путь

# --- Переменные состояния для Health Check ---
_health_check_failure_counter = 0
_mcpo_manual_restart_in_progress = False # Флаг, чтобы хелсчекер не мешал ручному управлению

# --- Управление состоянием процесса (PID-файл) ---

def _save_pid(pid: int):
    """Сохраняет PID процесса MCPO в файл."""
    pid_file_path_str = _get_pid_file_path()
    try:
        # Убедимся, что директория существует прямо перед записью
        Path(pid_file_path_str).parent.mkdir(parents=True, exist_ok=True)
        with open(pid_file_path_str, "w") as f:
            f.write(str(pid))
        logger.info(f"PID процесса MCPO {pid} сохранен в {pid_file_path_str}")
    except IOError as e:
        logger.error(f"Ошибка сохранения PID {pid} в {pid_file_path_str}: {e}")
    except Exception as e:
        logger.error(f"Неожиданная ошибка при сохранении PID {pid}: {e}", exc_info=True)


def _load_pid() -> Optional[int]:
    """Загружает PID процесса MCPO из файла."""
    pid_file_path_str = _get_pid_file_path()
    if not os.path.exists(pid_file_path_str):
        # logger.debug(f"PID-файл не найден: {pid_file_path_str}")
        return None
    try:
        with open(pid_file_path_str, "r") as f:
            pid_str = f.read().strip()
            if pid_str:
                pid = int(pid_str)
                # logger.debug(f"PID {pid} загружен из {pid_file_path_str}")
                return pid
            logger.warning(f"PID-файл {pid_file_path_str} пуст.")
            return None
    except (IOError, ValueError) as e:
        logger.error(f"Ошибка загрузки PID из {pid_file_path_str}: {e}. Удаляю некорректный файл.")
        _clear_pid() # Очищаем некорректный файл
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при загрузке PID из {pid_file_path_str}: {e}", exc_info=True)
        return None

def _clear_pid():
    """Удаляет PID-файл."""
    pid_file_path_str = _get_pid_file_path()
    if os.path.exists(pid_file_path_str):
        try:
            os.remove(pid_file_path_str)
            logger.info(f"PID-файл {pid_file_path_str} удален.")
        except OSError as e:
            logger.error(f"Ошибка удаления PID-файла {pid_file_path_str}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка при удалении PID-файла {pid_file_path_str}: {e}", exc_info=True)


def _is_process_running(pid: Optional[int]) -> bool:
    """Проверяет, запущен ли процесс с заданным PID."""
    if pid is None:
        return False

    if sys.platform == "win32":
        # Проверка на Windows через tasklist
        try:
            # /nh - без заголовка, /fi - фильтр по PID
            # Используем shell=True с осторожностью, но здесь необходимо для фильтра
            result = subprocess.run(
                f'tasklist /nh /fi "PID eq {pid}"',
                shell=True, capture_output=True, text=True, check=False, timeout=5
            )
            # Процесс найден, если вывод не пустой и содержит PID
            output = result.stdout.strip()
            return output and str(pid) in output
        except subprocess.TimeoutExpired:
            logger.error(f"Таймаут при проверке процесса {pid} на Windows.")
            return False # Считаем, что не запущен, если проверка зависла
        except Exception as e:
            # Прочие ошибки (например, права доступа)
            logger.error(f"Ошибка проверки статуса процесса {pid} на Windows: {e}")
            return False # На всякий случай считаем, что не запущен

    else: # Unix-подобные системы
        # Проверка на Unix через kill -0
        try:
            os.kill(pid, 0) # Сигнал 0 не влияет на процесс, но проверяет его существование и права
            return True
        except OSError as e:
            if e.errno == errno.ESRCH:
                # ESRCH: No such process - процесс не найден
                return False
            elif e.errno == errno.EPERM:
                # EPERM: Operation not permitted - процесс существует, но у нас нет прав послать ему сигнал
                # Для проверки статуса это означает, что он ЗАПУЩЕН.
                logger.warning(f"Нет прав для проверки PID {pid} (EPERM), но процесс, вероятно, существует.")
                return True
            else:
                # Другие ошибки OSError
                logger.error(f"Неожиданная ошибка OSError при проверке PID {pid}: {e}")
                return False
        except Exception as e:
             # Другие неожиданные ошибки
            logger.error(f"Неожиданная ошибка при проверке PID {pid}: {e}", exc_info=True)
            return False

# --- Запуск/Остановка/Перезапуск MCPO ---

def _start_mcpo_subprocess_sync(settings: McpoSettings) -> Tuple[Optional[int], str]:
    """
    Синхронная функция для запуска процесса mcpo.
    Вызывается через asyncio.to_thread.
    """
    command = ["mcpo", "--port", str(settings.port), "--config", settings.config_file_path]
    if settings.use_api_key and settings.api_key:
        command.extend(["--api-key", settings.api_key])

    logger.info(f"[Subprocess] Попытка запуска mcpo: {' '.join(command)}")

    log_file_handle = None
    stdout_redir = subprocess.DEVNULL # По умолчанию вывод в null
    stderr_redir = subprocess.DEVNULL
    process_cwd = str(_get_data_dir_path()) # Используем директорию данных как рабочую по умолчанию

    try:
        # Настройка перенаправления вывода в лог-файл, если указан
        if settings.log_file_path:
            log_dir = os.path.dirname(settings.log_file_path)
            if log_dir: # Создаем директорию, только если путь содержит директорию
                 Path(log_dir).mkdir(parents=True, exist_ok=True)
                 logger.info(f"[Subprocess] Директория для логов MCPO создана (или уже существует): {log_dir}")

            try:
                # 'a' - дозапись, buffering=1 - построчная буферизация
                log_file_handle = open(settings.log_file_path, 'a', buffering=1, encoding='utf-8', errors='ignore')
                stdout_redir = log_file_handle
                stderr_redir = log_file_handle
                logger.info(f"[Subprocess] stdout/stderr процесса mcpo будет перенаправлен в {settings.log_file_path}")
            except IOError as e:
                logger.error(f"[Subprocess] Не удалось открыть лог-файл '{settings.log_file_path}': {e}. Вывод будет перенаправлен в DEVNULL.")
            except Exception as e:
                 logger.error(f"[Subprocess] Неожиданная ошибка при открытии лог-файла '{settings.log_file_path}': {e}. Вывод будет перенаправлен в DEVNULL.", exc_info=True)


        # --- Платформо-зависимые параметры Popen ---
        process_kwargs = {
            "stdout": stdout_redir,
            "stderr": stderr_redir,
            "stdin": subprocess.DEVNULL, # Обычно не нужен ввод
            "cwd": process_cwd, # Устанавливаем рабочую директорию
        }

        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP позволяет убить все дерево процессов через taskkill /T
            process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else: # Linux/Unix
            # Запускаем в новой сессии, чтобы процесс стал лидером группы
            # Это нужно для надежного убийства всей группы через killpg
            process_kwargs["start_new_session"] = True
            # Альтернатива: preexec_fn=os.setsid (для более старых Python), но start_new_session проще

        # --- Запуск процесса ---
        logger.info(f"[Subprocess] Запуск Popen с командой: {command} | CWD: {process_cwd} | Params: {process_kwargs}")
        process = subprocess.Popen(command, **process_kwargs)

        msg = f"Процесс MCPO успешно запущен. PID: {process.pid}."
        logger.info(f"[Subprocess] {msg}")
        return process.pid, msg

    except FileNotFoundError:
        # Команда mcpo не найдена
        msg = f"Ошибка запуска mcpo: команда 'mcpo' не найдена. Убедитесь, что mcpo установлен и доступен в системном PATH для пользователя, от которого запущен менеджер."
        logger.error(f"[Subprocess] {msg}")
        return None, msg
    except PermissionError as e:
        msg = f"Ошибка запуска mcpo: недостаточно прав для выполнения команды или доступа к рабочей директории ({process_cwd}). Ошибка: {e}"
        logger.error(f"[Subprocess] {msg}")
        return None, msg
    except Exception as e:
        # Другие ошибки при запуске Popen
        msg = f"Неожиданная ошибка при запуске процесса mcpo: {e}"
        logger.error(f"[Subprocess] {msg}", exc_info=True)
        return None, msg
    finally:
        # Важно: Закрыть дескриптор лог-файла, если он был открыт здесь.
        # Popen может удерживать его открытым, но лучше закрыть наш хендл.
        # Не закрываем stdout/stderr, если это были DEVNULL или унаследованные дескрипторы.
        if log_file_handle:
            try:
                log_file_handle.close()
            except Exception as e:
                logger.warning(f"[Subprocess] Не удалось закрыть дескриптор лог-файла: {e}")


async def start_mcpo(settings: McpoSettings) -> Tuple[bool, str]:
    """Асинхронно запускает процесс MCPO, если он еще не запущен."""
    global _mcpo_manual_restart_in_progress, _health_check_failure_counter
    if _mcpo_manual_restart_in_progress: # Дополнительная защита от гонок
        logger.warning("Попытка запуска MCPO во время другой операции управления. Отменено.")
        return False, "Операция управления MCPO уже выполняется."

    _mcpo_manual_restart_in_progress = True # Сигнал хелсчекеру
    try:
        current_pid = _load_pid()
        if _is_process_running(current_pid):
            msg = f"Процесс MCPO уже запущен с PID {current_pid}."
            logger.warning(msg)
            return False, msg # Не ошибка, просто уже запущен

        # Проверка наличия файла конфигурации перед запуском
        config_path = Path(settings.config_file_path)
        if not config_path.is_file():
            # Попробуем создать директорию, если ее нет
            config_path.parent.mkdir(parents=True, exist_ok=True)
            # Проверим еще раз
            if not config_path.is_file():
                 msg = f"Файл конфигурации MCPO не найден: {settings.config_file_path}. Запуск невозможен. Сгенерируйте его (например, через 'Применить и Перезапустить')."
                 logger.error(msg)
                 return False, msg

        logger.info(f"Попытка запуска mcpo с настройками: port={settings.port}, config='{settings.config_file_path}'...")

        # Запускаем синхронную функцию _start_mcpo_subprocess_sync в отдельном потоке
        pid, message = await asyncio.to_thread(_start_mcpo_subprocess_sync, settings)

        if pid:
            _save_pid(pid)
            _health_check_failure_counter = 0 # Сброс счетчика ошибок хелсчека после успешного ручного запуска
            logger.info(f"MCPO запущен. {message}")
            return True, message
        else:
            _clear_pid() # Очищаем PID-файл, если запуск не удался
            logger.error(f"Не удалось запустить MCPO. {message}")
            return False, message
    except Exception as e:
        logger.error(f"Неожиданная ошибка во время запуска MCPO: {e}", exc_info=True)
        return False, f"Внутренняя ошибка при запуске: {e}"
    finally:
        await asyncio.sleep(0.2) # Небольшая пауза перед снятием флага
        _mcpo_manual_restart_in_progress = False


async def stop_mcpo() -> Tuple[bool, str]:
    """Асинхронно останавливает процесс MCPO (и его группу на Linux), если он запущен."""
    global _mcpo_manual_restart_in_progress
    if _mcpo_manual_restart_in_progress: # Дополнительная защита
        logger.warning("Попытка остановки MCPO во время другой операции управления. Отменено.")
        return False, "Операция управления MCPO уже выполняется."

    _mcpo_manual_restart_in_progress = True # Сигнал хелсчекеру
    pid_to_stop = _load_pid() # Загружаем PID в начале

    try:
        if not pid_to_stop:
            msg = "PID процесса MCPO не найден в файле. Остановка невозможна (возможно, уже остановлен или не запускался менеджером)."
            logger.warning(msg)
            # Считаем успехом, так как останавливать нечего по данным PID-файла
            return True, msg

        # Проверяем, существует ли процесс *перед* попыткой остановки
        if not _is_process_running(pid_to_stop):
            msg = f"Процесс MCPO с PID {pid_to_stop} (из файла) не найден в системе. Очищаю устаревший PID-файл."
            logger.warning(msg)
            _clear_pid()
            return True, msg # Успех, процесс уже отсутствует

        logger.info(f"Попытка остановки процесса mcpo с PID {pid_to_stop}...")
        stop_successful = False
        final_message = f"Не удалось остановить процесс MCPO (PID: {pid_to_stop})." # Сообщение по умолчанию

        try:
            if sys.platform == "win32":
                logger.info(f"Windows: Попытка остановить дерево процессов с PID {pid_to_stop} с помощью taskkill /F /T...")
                # Используем subprocess.run в потоке для синхронного выполнения taskkill
                result = await asyncio.to_thread(
                    subprocess.run,
                    f'taskkill /F /T /PID {pid_to_stop}',
                    shell=True, capture_output=True, text=True, check=False, timeout=10 # Таймаут 10 сек
                )
                logger.info(f"Результат Taskkill (PID: {pid_to_stop}): RC={result.returncode}, stdout='{result.stdout.strip()}', stderr='{result.stderr.strip()}'")
                await asyncio.sleep(1.0) # Даем время процессу завершиться

                if not _is_process_running(pid_to_stop):
                    final_message = f"Процесс MCPO (PID: {pid_to_stop}) успешно остановлен через taskkill."
                    logger.info(final_message)
                    stop_successful = True
                else:
                    # Если taskkill не сработал (маловероятно с /F)
                    final_message = f"Не удалось остановить процесс MCPO (PID: {pid_to_stop}) с помощью taskkill (RC={result.returncode}). Проверьте права или состояние процесса."
                    logger.error(final_message + f" Stderr: {result.stderr.strip()}")
                    stop_successful = False

            else: # Linux/Unix - Используем убийство группы процессов
                pgid = -1 # Инициализация значением по умолчанию
                try:
                    # Получаем ID группы процессов (PGID), связанный с PID
                    # Это предполагает, что процесс был запущен с start_new_session=True
                    pgid = os.getpgid(pid_to_stop)
                    logger.info(f"Linux: Найден PGID {pgid} для PID {pid_to_stop}.")

                    # 1. Посылаем SIGTERM группе
                    logger.info(f"Linux: Посылаем SIGTERM группе процессов {pgid}...")
                    os.killpg(pgid, signal.SIGTERM)
                    await asyncio.sleep(1.5) # Ждем 1.5 сек для завершения

                    # Проверяем, остановился ли исходный процесс
                    if not _is_process_running(pid_to_stop):
                        final_message = f"Группа процессов MCPO {pgid} (PID: {pid_to_stop}) успешно остановлена через SIGTERM."
                        logger.info(final_message)
                        stop_successful = True
                    else:
                         # 2. Если не остановился, посылаем SIGKILL группе
                        logger.warning(f"Linux: Группа процессов {pgid} (PID: {pid_to_stop}) не завершилась после SIGTERM. Посылаем SIGKILL...")
                        os.killpg(pgid, signal.SIGKILL)
                        await asyncio.sleep(1.0) # Ждем 1 сек после SIGKILL

                        if not _is_process_running(pid_to_stop):
                            final_message = f"Группа процессов MCPO {pgid} (PID: {pid_to_stop}) успешно остановлена через SIGKILL."
                            logger.info(final_message)
                            stop_successful = True
                        else:
                            # Если ДАЖЕ SIGKILL не помог (очень странно)
                            final_message = f"КРИТИЧЕСКАЯ ОШИБКА: Не удалось остановить группу процессов {pgid} (PID: {pid_to_stop}) даже после SIGKILL! Возможные причины: процесс в состоянии 'D', проблема с ядром, недостаточные права."
                            logger.error(final_message)
                            stop_successful = False # Остановка не удалась

                except ProcessLookupError:
                    # Исходный PID исчез до того, как мы успели получить PGID или убить его
                    final_message = f"Процесс с PID {pid_to_stop} не найден во время попытки остановки (возможно, уже завершился)."
                    logger.warning(final_message)
                    stop_successful = True # Считаем успехом, так как его нет
                except PermissionError:
                     final_message = f"Ошибка прав доступа при попытке послать сигнал группе процессов {pgid} (PID: {pid_to_stop}). Убедитесь, что менеджер запущен с достаточными привилегиями."
                     logger.error(final_message)
                     stop_successful = False
                except Exception as e_inner:
                     final_message = f"Неожиданная ошибка при остановке процесса/группы (PID: {pid_to_stop}, PGID: {pgid}): {e_inner}"
                     logger.error(final_message, exc_info=True)
                     stop_successful = False

        except Exception as e_outer:
            # Другие ошибки во время остановки (например, в asyncio.to_thread для taskkill)
            final_message = f"Внешняя ошибка при попытке остановить процесс mcpo (PID: {pid_to_stop}): {e_outer}"
            logger.error(final_message, exc_info=True)
            stop_successful = False

        # Очищаем PID-файл ТОЛЬКО если остановка прошла успешно или процесс уже отсутствовал
        if stop_successful:
            _clear_pid()

        return stop_successful, final_message

    except Exception as e_main:
         logger.error(f"Критическая ошибка в функции stop_mcpo (PID: {pid_to_stop}): {e_main}", exc_info=True)
         return False, f"Внутренняя ошибка функции stop_mcpo: {e_main}"
    finally:
        await asyncio.sleep(0.2) # Небольшая пауза перед снятием флага
        _mcpo_manual_restart_in_progress = False


async def restart_mcpo_process_with_new_config(db_session: SQLModelSession, settings: McpoSettings) -> Tuple[bool, str]:
    """
    Останавливает mcpo, генерирует новый конфиг и запускает mcpo.
    Используется кнопкой 'Применить и Перезапустить' и Health Checker'ом.
    """
    global _mcpo_manual_restart_in_progress
    # Дополнительная проверка, чтобы предотвратить рекурсию или гонки
    # Health Checker сам проверяет флаг перед вызовом перезапуска
    if _mcpo_manual_restart_in_progress and not settings.health_check_enabled : # Проверяем, не вызван ли вручную во время health check
        logger.warning("Процесс перезапуска уже инициирован, новый запрос на перезапуск проигнорирован.")
        return False, "Процесс перезапуска уже выполняется."

    # Устанавливаем флаг в начале; он будет снят в finally блоков start/stop
    _mcpo_manual_restart_in_progress = True
    logger.info("Начат процесс перезапуска MCPO...")
    final_messages = []
    restart_success = False

    try:
        # 1. Останавливаем текущий процесс (если запущен)
        current_pid = _load_pid()
        stop_needed = _is_process_running(current_pid) # Проверяем заранее

        if stop_needed:
            logger.info(f"Перезапуск: Обнаружен запущенный процесс MCPO (PID: {current_pid}). Попытка остановки...")
            # stop_mcpo управляет флагом _mcpo_manual_restart_in_progress и очищает PID при успехе
            # Он также вернет флаг в False в своем finally
            stop_success, stop_msg = await stop_mcpo()
            final_messages.append(f"Остановка: {stop_msg}")
            if not stop_success:
                # Если остановка не удалась, прерываем перезапуск
                message = " | ".join(final_messages) + " КРИТИЧЕСКАЯ ОШИБКА: Не удалось остановить текущий процесс MCPO. Перезапуск отменен."
                logger.error(message)
                # Флаг _mcpo_manual_restart_in_progress должен быть снят в finally блока stop_mcpo
                return False, message
        else:
            logger.info("Перезапуск: Запущенный процесс MCPO не обнаружен (или PID не найден). Пропускаем остановку.")
            _clear_pid() # Очищаем PID на всякий случай, если процесс не найден

        # 2. Генерируем новый файл конфигурации
        logger.info("Перезапуск: Генерация нового файла конфигурации MCPO...")
        # Используем стандартную генерацию
        config_generated = generate_mcpo_config_file(db_session, settings)
        if not config_generated:
            message = " | ".join(final_messages) + " ОШИБКА: Не удалось сгенерировать файл конфигурации. Запуск MCPO отменен."
            logger.error(message)
            # Флаг _mcpo_manual_restart_in_progress должен быть снят stop_mcpo (если вызывался)
            # или здесь, если остановка не требовалась
            _mcpo_manual_restart_in_progress = False # Снимаем флаг, так как прерываем
            return False, message
        final_messages.append("Файл конфигурации успешно сгенерирован.")

        # 3. Запускаем MCPO с новой конфигурацией
        logger.info("Перезапуск: Попытка запуска MCPO с новой конфигурацией...")
        # start_mcpo сам установит и снимет флаг _mcpo_manual_restart_in_progress и сбросит счетчик хелсчека
        start_success, start_msg = await start_mcpo(settings)
        final_messages.append(f"Запуск: {start_msg}")
        restart_success = start_success

    except Exception as e:
        logger.error(f"Неожиданная ошибка во время процесса перезапуска MCPO: {e}", exc_info=True)
        final_messages.append(f"Критическая ошибка перезапуска: {e}")
        restart_success = False
        # Убедимся, что флаг снят, если произошла ошибка вне start/stop
        _mcpo_manual_restart_in_progress = False

    # Возвращаем результат и собранные сообщения
    return restart_success, " | ".join(final_messages)

# --- Получение статуса и логов ---

def get_mcpo_status() -> str:
    """Возвращает строковый статус процесса MCPO: RUNNING, STOPPED, ERROR."""
    pid = _load_pid()
    if pid is None:
        # logger.debug("Статус MCPO: PID не найден в файле -> STOPPED")
        return "STOPPED" # PID-файл не найден

    if _is_process_running(pid):
        # logger.debug(f"Статус MCPO: PID {pid} найден и процесс запущен -> RUNNING")
        return "RUNNING" # Процесс с PID из файла найден и запущен
    else:
        # PID-файл существует, но процесс не найден - это ошибка или устаревший файл
        logger.warning(f"Статус MCPO: PID {pid} найден в файле, но соответствующий процесс НЕ запущен -> ERROR")
        return "ERROR"

async def get_mcpo_logs(lines: int = 100, log_file_path: Optional[str] = None) -> List[str]:
    """Асинхронно читает последние N строк из лог-файла MCPO."""
    # Загружаем настройки, чтобы получить путь к логу, если он не передан явно
    settings = load_mcpo_settings()
    actual_log_path = log_file_path or settings.log_file_path

    if not actual_log_path:
        logger.warning("Попытка чтения логов MCPO, но путь к файлу не настроен.")
        return ["Ошибка: Путь к лог-файлу не настроен."]
    if not os.path.exists(actual_log_path):
        logger.warning(f"Попытка чтения логов MCPO, но файл не найден: {actual_log_path}")
        return [f"Ошибка: Лог-файл не найден по пути: {actual_log_path}"]

    try:
        # Используем deque для эффективного хранения последних строк
        from collections import deque
        last_lines = deque(maxlen=lines)

        # Читаем файл построчно в бинарном режиме и декодируем, игнорируя ошибки
        # Это надежнее для потенциально поврежденных логов
        def read_lines_sync():
            try:
                with open(actual_log_path, 'rb') as f:
                    # Перемещаемся в конец файла для оптимизации чтения больших логов (если возможно)
                    try:
                        f.seek(0, os.SEEK_END)
                        # Оцениваем примерный размер N строк и читаем с конца (это эвристика)
                        # Можно улучшить, читая блоками с конца
                        # Пока читаем весь файл для простоты и надежности deque
                        f.seek(0, os.SEEK_SET)
                    except (IOError, OSError):
                        pass # seek может не работать для некоторых потоков, читаем с начала

                    for line_bytes in f:
                        last_lines.append(line_bytes.decode('utf-8', errors='ignore').rstrip())
                return list(last_lines)
            except Exception as read_e:
                logger.error(f"Ошибка во время чтения файла логов {actual_log_path} в потоке: {read_e}", exc_info=True)
                # Возвращаем ошибку как строку лога
                return [f"Ошибка чтения логов: {read_e}"]


        # Выполняем синхронное чтение в отдельном потоке
        return await asyncio.to_thread(read_lines_sync)

    except Exception as e:
        logger.error(f"Ошибка при подготовке к чтению лог-файла {actual_log_path}: {e}", exc_info=True)
        return [f"Ошибка подготовки чтения логов: {e}"]

# --- Агрегация инструментов ---
# (Код get_aggregated_tools_from_mcpo остается без изменений, он зависит от статуса и настроек)
async def get_aggregated_tools_from_mcpo(db_session: SQLModelSession) -> Dict[str, Any]:
    """
    Агрегирует инструменты из запущенного экземпляра MCPO.
    Возвращает словарь со статусом, списком серверов с их инструментами,
    и публичным базовым URL для генерации ссылок.
    """
    logger.info("Агрегация инструментов из запущенного экземпляра MCPO...")
    mcpo_status = get_mcpo_status()
    settings = load_mcpo_settings() # Загружаем текущие настройки

    # Определяем базовый URL для генерации ссылок в UI
    base_url_for_links = ""
    if settings.public_base_url:
        base_url_for_links = settings.public_base_url.rstrip('/')
        logger.debug(f"Используется публичный базовый URL для ссылок: {base_url_for_links}")
    elif mcpo_status == "RUNNING": # Используем локальный, только если MCPO запущен
        base_url_for_links = f"http://127.0.0.1:{settings.port}"
        logger.debug(f"Публичный базовый URL не задан, используется локальный для ссылок: {base_url_for_links}")
    else:
         logger.debug("Публичный базовый URL не задан, MCPO не запущен, ссылки не будут сгенерированы.")


    # Инициализируем результат, добавляя статус и URL сразу
    result: Dict[str, Any] = {
        "status": mcpo_status,
        "servers": {},
        "base_url_for_links": base_url_for_links # Этот URL будет использоваться в шаблоне tools.html
    }

    # Если MCPO не запущен, нет смысла продолжать
    if mcpo_status != "RUNNING":
        logger.warning(f"Невозможно агрегировать инструменты, статус MCPO: {mcpo_status}")
        return result # Возвращаем результат с текущим статусом и URL

    # Определяем внутренний URL для запросов к самому API MCPO (всегда localhost)
    mcpo_internal_api_url = f"http://127.0.0.1:{settings.port}"
    headers = {}
    if settings.use_api_key and settings.api_key:
        headers["Authorization"] = f"Bearer {settings.api_key}"

    # Получаем список включенных определений серверов из БД
    enabled_definitions = get_server_definitions(db_session, only_enabled=True, limit=10000) # Получаем все включенные
    if not enabled_definitions:
        logger.info("В базе данных не найдено включенных определений серверов.")
        return result # Возвращаем результат с текущим статусом, URL и пустым списком серверов

    # --- Вложенная async функция для получения OpenAPI спеки одного сервера ---
    async def fetch_openapi(definition):
        server_name = definition.name
        # Пропускаем запрос для внутреннего Health Check эхо-сервера
        if server_name == settings.INTERNAL_ECHO_SERVER_NAME and settings.health_check_enabled:
            # logger.debug(f"Пропуск запроса OpenAPI для внутреннего эхо-сервера '{server_name}'.")
            return server_name, {"status": "SKIPPED", "error_message": "Внутренний эхо-сервер (пропущено).", "tools": []}

        # Формируем URL для запроса openapi.json к MCPO
        url = f"{mcpo_internal_api_url}/{server_name}/openapi.json"
        server_result_data = {"status": "ERROR", "error_message": None, "tools": []}
        try:
            async with httpx.AsyncClient(headers=headers, timeout=10.0, follow_redirects=True) as client:
                logger.debug(f"Запрос OpenAPI для сервера '{server_name}' по URL: {url}")
                resp = await client.get(url)

                if resp.status_code == 200:
                    try:
                        openapi_data = resp.json()
                        paths = openapi_data.get("paths", {})
                        found_tools = []
                        for path, methods in paths.items():
                            # Ищем только методы POST (основной метод вызова в MCP)
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
                    except json.JSONDecodeError as json_e:
                         server_result_data["error_message"] = f"Ошибка парсинга JSON ответа от MCPO: {json_e}"
                         logger.warning(f"Ошибка парсинга JSON OpenAPI для '{server_name}' (HTTP {resp.status_code}): {resp.text[:200]}...")

                else:
                    # Ошибка во время запроса к MCPO
                    error_text = resp.text[:200] # Ограничиваем длину текста ошибки
                    server_result_data["error_message"] = f"Ошибка MCPO (HTTP {resp.status_code}): {error_text}"
                    logger.warning(f"Ошибка запроса OpenAPI для '{server_name}' (HTTP {resp.status_code}): {error_text}")

        except httpx.RequestError as e:
            # Сетевая ошибка во время запроса к MCPO
            server_result_data["error_message"] = f"Сетевая ошибка: {e.__class__.__name__}"
            logger.warning(f"Сетевая ошибка при запросе OpenAPI для '{server_name}': {e}")
        except Exception as e:
            # Другие ошибки (например, при обработке)
            server_result_data["error_message"] = f"Внутренняя ошибка: {e.__class__.__name__}"
            logger.warning(f"Ошибка обработки OpenAPI для '{server_name}': {e}", exc_info=True)

        return server_name, server_result_data
    # --- Конец вложенной функции fetch_openapi ---

    # Запускаем запросы ко всем серверам параллельно
    tasks = [fetch_openapi(d) for d in enabled_definitions]
    fetch_results = await asyncio.gather(*tasks, return_exceptions=True) # Собираем и исключения

    # Собираем результаты в итоговый словарь
    for i, definition in enumerate(enabled_definitions):
         server_name = definition.name
         result_item = fetch_results[i]
         if isinstance(result_item, Exception):
             logger.error(f"Исключение при получении OpenAPI для '{server_name}': {result_item}", exc_info=result_item)
             result["servers"][server_name] = {"status": "ERROR", "error_message": f"Исключение: {result_item.__class__.__name__}", "tools": []}
         elif isinstance(result_item, tuple) and len(result_item) == 2:
             # Ожидаемый результат - кортеж (server_name, server_result)
             _, server_result = result_item
             result["servers"][server_name] = server_result
         else:
             # Неожиданный результат от gather
              logger.error(f"Неожиданный результат от asyncio.gather для '{server_name}': {result_item}")
              result["servers"][server_name] = {"status": "ERROR", "error_message": "Неожиданный внутренний результат", "tools": []}


    logger.info(f"Агрегация инструментов завершена. Обработано {len(enabled_definitions)} определений.")
    return result


# --- Логика Health Check ---

@contextlib.asynccontextmanager
async def get_async_db_session(engine_to_use = engine) -> SQLModelSession:
    """
    Асинхронный контекстный менеджер для получения сессии БД в фоновых задачах.
    Использует глобальный engine.
    """
    session = None
    try:
        # Создаем новую сессию напрямую из engine для этой операции
        session = SQLModelSession(engine_to_use)
        # logger.debug("Async DB session created for background task.")
        yield session
    except Exception as e:
        logger.error(f"Ошибка создания сессии БД в фоновой задаче: {e}", exc_info=True)
        raise # Перевыбрасываем ошибку, чтобы вызывающий код мог ее обработать
    finally:
        if session:
            try:
                session.close()
                # logger.debug("Async DB session closed for background task.")
            except Exception as e:
                logger.error(f"Ошибка закрытия сессии БД в фоновой задаче: {e}", exc_info=True)


async def run_health_check_loop_async():
    """Асинхронный цикл для периодических проверок состояния MCPO."""
    global _health_check_failure_counter, _mcpo_manual_restart_in_progress
    logger.info("Запуск фонового цикла проверки состояния (Health Check) MCPO...")

    # Небольшая пауза перед первой проверкой
    await asyncio.sleep(10) # Даем больше времени на первый запуск

    while True:
        # Загружаем актуальные настройки на каждой итерации
        try:
             settings = load_mcpo_settings()
        except Exception as e:
             logger.error(f"Health Check: КРИТИЧЕСКАЯ ОШИБКА загрузки настроек. Цикл прерван. Ошибка: {e}", exc_info=True)
             await asyncio.sleep(60) # Ждем минуту перед повторной попыткой
             continue # Пропускаем итерацию

        if not settings.health_check_enabled:
            # logger.debug("Health Check: Проверка отключена в настройках.")
            # Сбрасываем счетчик, если проверка отключена
            if _health_check_failure_counter > 0:
                logger.info("Health Check: Проверка отключена, сбрасываем счетчик ошибок.")
                _health_check_failure_counter = 0
            # Ждем обычный интервал перед проверкой настроек снова
            await asyncio.sleep(settings.health_check_interval_seconds)
            continue

        # Пропускаем проверку, если идет ручное управление
        if _mcpo_manual_restart_in_progress:
            logger.info("Health Check: Обнаружено ручное управление MCPO, пропускаем проверку.")
            # Короткая пауза, пока идет ручное управление
            await asyncio.sleep(max(1, settings.health_check_failure_retry_delay_seconds // 2))
            continue

        # Проверяем статус процесса перед отправкой HTTP-запроса
        mcpo_status = get_mcpo_status()
        if mcpo_status != "RUNNING":
            logger.warning(f"Health Check: Процесс MCPO не запущен (статус: {mcpo_status}). Пропускаем HTTP-проверку.")
            # Сбрасываем счетчик, чтобы не накапливать ошибки, пока он остановлен
            # Но только если он действительно был остановлен, а не в состоянии ERROR
            if mcpo_status == "STOPPED" and _health_check_failure_counter > 0:
                 logger.info(f"Health Check: MCPO остановлен (статус: {mcpo_status}), сбрасываем счетчик ошибок.")
                 _health_check_failure_counter = 0
            elif mcpo_status == "ERROR":
                 # Если статус ERROR, это уже проблема, увеличиваем счетчик
                 logger.warning(f"Health Check: Статус MCPO - ERROR. Увеличиваем счетчик ошибок.")
                 _health_check_failure_counter += 1
                 await handle_health_check_failure(settings) # Обрабатываем ошибку

            await asyncio.sleep(settings.health_check_interval_seconds) # Ждем обычный интервал
            continue

        # Формируем URL и payload для запроса к внутреннему эхо-серверу через MCPO
        # Убедимся, что имена не пустые
        if not settings.INTERNAL_ECHO_SERVER_NAME or not settings.INTERNAL_ECHO_TOOL_PATH:
             logger.error("Health Check: Не настроены INTERNAL_ECHO_SERVER_NAME или INTERNAL_ECHO_TOOL_PATH. Проверка невозможна.")
             await asyncio.sleep(settings.health_check_interval_seconds * 2) # Ждем дольше
             continue

        health_check_url = f"http://127.0.0.1:{settings.port}/{settings.INTERNAL_ECHO_SERVER_NAME.strip('/')}{settings.INTERNAL_ECHO_TOOL_PATH.strip('/')}"
        payload = settings.INTERNAL_ECHO_PAYLOAD
        headers = {}
        if settings.use_api_key and settings.api_key:
            headers["Authorization"] = f"Bearer {settings.api_key}"

        # --- Выполняем HTTP-запрос ---
        try:
            async with httpx.AsyncClient(headers=headers, timeout=settings.health_check_timeout_seconds, follow_redirects=True) as client:
                logger.debug(f"Health Check: Отправка POST запроса на {health_check_url} с таймаутом {settings.health_check_timeout_seconds}s")
                response = await client.post(health_check_url, json=payload)

            if 200 <= response.status_code < 300:
                # Проверка успешна
                if _health_check_failure_counter > 0:
                    logger.info(f"Health Check: УСПЕХ (Статус: {response.status_code}). Счетчик ошибок сброшен.")
                else:
                     logger.debug(f"Health Check: Успех (Статус: {response.status_code}).")
                _health_check_failure_counter = 0
                await asyncio.sleep(settings.health_check_interval_seconds) # Ждем нормальный интервал до следующей проверки
            else:
                # Проверка не удалась (не 2xx ответ)
                logger.warning(f"Health Check: НЕУДАЧА (Статус: {response.status_code}). URL: {health_check_url}. Ответ: {response.text[:200]}")
                _health_check_failure_counter += 1
                await handle_health_check_failure(settings) # Обрабатываем неудачу

        except httpx.ConnectError as e:
            # Ошибка соединения (MCPO не отвечает на порту)
            logger.error(f"Health Check: Ошибка соединения при запросе к MCPO ({health_check_url}). Ошибка: {e}")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings) # Обрабатываем неудачу
        except httpx.TimeoutException:
             # Таймаут запроса
            logger.error(f"Health Check: Таймаут ({settings.health_check_timeout_seconds}s) при запросе к MCPO ({health_check_url}).")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings) # Обрабатываем неудачу
        except httpx.RequestError as e:
            # Другие ошибки httpx
            logger.error(f"Health Check: Ошибка сети при запросе к MCPO ({health_check_url}). Ошибка: {e.__class__.__name__}: {e}")
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings) # Обрабатываем неудачу
        except Exception as e:
            # Другие неожиданные ошибки
            logger.error(f"Health Check: Неожиданная ошибка ({health_check_url}). Ошибка: {e.__class__.__name__}: {e}", exc_info=True)
            _health_check_failure_counter += 1
            await handle_health_check_failure(settings) # Обрабатываем неудачу

async def handle_health_check_failure(settings: McpoSettings):
    """Обрабатывает неудачную проверку состояния, решает, нужен ли перезапуск."""
    global _health_check_failure_counter, _mcpo_manual_restart_in_progress

    logger.info(f"Health Check: Попытка неудачи {_health_check_failure_counter} из {settings.health_check_failure_attempts}.")

    if _health_check_failure_counter >= settings.health_check_failure_attempts:
        logger.warning(f"Health Check: Достигнуто максимальное количество ({settings.health_check_failure_attempts}) неудачных попыток проверки.")

        if settings.auto_restart_on_failure:
            logger.info("Health Check: Включен автоматический перезапуск. Попытка перезапуска MCPO...")

            # Получаем сессию БД асинхронно для генерации конфига внутри перезапуска
            restart_success = False
            restart_message = "Не удалось получить сессию БД для перезапуска."
            try:
                async with get_async_db_session() as db_session:
                    if db_session:
                        # Вызываем функцию перезапуска, она управляет флагом _mcpo_manual_restart_in_progress
                        restart_success, restart_message = await restart_mcpo_process_with_new_config(db_session, settings)
                    else:
                         logger.error("Health Check: Не удалось получить сессию БД для перезапуска. Автоматический перезапуск отменен.")
            except Exception as e_db:
                 logger.error(f"Health Check: Ошибка при получении сессии БД для перезапуска: {e_db}", exc_info=True)
                 restart_message = f"Ошибка получения сессии БД: {e_db}"


            if restart_success:
                logger.info(f"Health Check: MCPO успешно перезапущен после сбоев. Сообщение: {restart_message}")
                _health_check_failure_counter = 0 # Сброс счетчика после успешного перезапуска
                await asyncio.sleep(settings.health_check_interval_seconds) # Ждем нормальный интервал
            else:
                logger.error(f"Health Check: Автоматический перезапуск MCPO НЕ УДАЛСЯ после сбоев. Сообщение: {restart_message}")
                # После неудачного перезапуска (особенно если он не удался из-за невозможности остановить старый процесс),
                # значительно увеличиваем паузу перед следующей попыткой проверки/перезапуска, чтобы не долбить систему.
                failed_restart_pause = settings.health_check_interval_seconds * 5
                logger.warning(f"Health Check: Увеличена пауза до {failed_restart_pause} сек из-за неудачного авто-перезапуска.")
                _health_check_failure_counter = 0 # Сбрасываем счетчик, чтобы не запускать перезапуск каждую секунду, но проблема остается
                await asyncio.sleep(failed_restart_pause)

        else: # auto_restart_on_failure is False
            logger.info("Health Check: Автоматический перезапуск отключен. Требуется ручное вмешательство для восстановления MCPO.")
            # Сбрасываем счетчик, чтобы не спамить лог "Max attempts" каждую секунду
            _health_check_failure_counter = 0
            # Ждем обычный интервал до следующей проверки (которая, вероятно, тоже не удастся)
            await asyncio.sleep(settings.health_check_interval_seconds)
    else:
        # Если максимальное количество попыток еще не достигнуто
        logger.info(f"Health Check: Ожидание {settings.health_check_failure_retry_delay_seconds} сек перед следующей попыткой проверки...")
        await asyncio.sleep(settings.health_check_failure_retry_delay_seconds)
