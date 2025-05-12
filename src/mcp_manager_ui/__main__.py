# src/mcp_manager_ui/__main__.py

import argparse
import uvicorn
import os
from pathlib import Path

# Предположим, что ваш FastAPI app объект создается в mcp_manager_ui.main:create_app()
# или просто импортируется как mcp_manager_ui.main:app
# Измените этот импорт в соответствии с вашей структурой!
from mcp_manager_ui.main import app # Или create_app()

def main():
    parser = argparse.ArgumentParser(description="Run the MCPO Manager UI.")
    parser.add_argument(
        "--host",
        type=str,
        default=os.getenv("MCPO_MANAGER_HOST", "127.0.0.1"),
        help="Host to bind the server to.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCPO_MANAGER_PORT", "8000")),
        help="Port to bind the server to.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("MCPO_MANAGER_WORKERS", "1")),
        help="Number of Uvicorn workers.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (for development).",
    )
    parser.add_argument(
        "--config-dir", # Добавим аргумент для пути к данным, если нужно
        type=str,
        default=os.getenv("MCPO_MANAGER_DATA_DIR", str(Path.home() / ".mcpo_manager_data")),
        help="Directory for storing MCPO manager data (PID files, generated configs, settings)."
    )

    args = parser.parse_args()

    # Установка переменной окружения для config_service и mcpo_service
    # чтобы они знали, где хранить данные, если они это используют
    os.environ["MCPO_MANAGER_DATA_DIR_EFFECTIVE"] = args.config_dir
    # Создадим директорию, если ее нет
    Path(args.config_dir).mkdir(parents=True, exist_ok=True)


    # Если ваш `app` создается функцией-фабрикой:
    # current_app = create_app()
    # uvicorn.run(current_app, host=args.host, port=args.port, workers=args.workers, reload=args.reload)

    # Если `app` это просто объект:
    uvicorn.run(
        "mcp_manager_ui.main:app", # Путь к вашему FastAPI app объекту
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=args.reload,
        # reload_dirs=["src/mcp_manager_ui"] if args.reload else None, # Для корректного релоада
        # log_level="info"
    )

if __name__ == "__main__":
    main()