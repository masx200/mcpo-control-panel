# mcpo_control_panel/__main__.py

import argparse
import os
from pathlib import Path
import sys
import logging

# Configure basic logger to see outputs before uvicorn setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def setup_environment_and_parse_args():
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
        default=int(os.getenv("MCPO_MANAGER_PORT", "8083")),
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
        "--config-dir",
        type=str,
        default=os.getenv("MCPO_MANAGER_DATA_DIR", str(Path.home() / ".mcpo_manager_data")),
        help="Directory for storing MCPO manager data (PID files, generated configs, settings)."
    )
    
    args = parser.parse_args()
    logger.info(f"Parsed arguments (args): {args}")
    logger.info(f"Value of args.config_dir before resolving: '{args.config_dir}'")

    # Convert config_dir to absolute path for consistency
    try:
        path_to_resolve = args.config_dir
        if not path_to_resolve:
             logger.warning("args.config_dir is empty or None, falling back to default logic for absolute_config_dir")
             path_to_resolve = os.getenv("MCPO_MANAGER_DATA_DIR", str(Path.home() / ".mcpo_manager_data"))
        absolute_config_dir = str(Path(path_to_resolve).resolve())
        logger.info(f"Resolved absolute_config_dir: '{absolute_config_dir}'")
    except Exception as e:
        logger.error(f"Error resolving config_dir '{args.config_dir}': {e}", exc_info=True)
        logger.warning("Falling back to default data directory due to resolution error.")
        absolute_config_dir = str((Path.home() / ".mcpo_manager_data").resolve())
        logger.info(f"Fallback absolute_config_dir: '{absolute_config_dir}'")

    os.environ["MCPO_MANAGER_DATA_DIR_EFFECTIVE"] = absolute_config_dir
    logger.info(f"Set MCPO_MANAGER_DATA_DIR_EFFECTIVE to: '{absolute_config_dir}'")
    
    try:
        Path(absolute_config_dir).mkdir(parents=True, exist_ok=True)
        logger.info(f"Ensured directory exists: {absolute_config_dir}")
    except Exception as e:
        logger.error(f"Failed to create directory {absolute_config_dir}: {e}", exc_info=True)
    
    return args # Return args for use in main

def main():
    """Main function to run the application."""
    cli_args = setup_environment_and_parse_args()

    # Now that environment variables are set, import uvicorn and app
    import uvicorn
    from .main import app # Import FastAPI app object

    logger.info(f"Starting Uvicorn with host={cli_args.host}, port={cli_args.port}...")
    uvicorn.run(
        app, # Pass the app object
        host=cli_args.host,
        port=cli_args.port,
        workers=cli_args.workers,
        reload=cli_args.reload,
        # log_level="info" # Can configure uvicorn log level separately
    )

if __name__ == "__main__":
    main()