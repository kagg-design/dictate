import os
import yaml
from src.logger import logger

DEFAULT_CONFIG_PATH = "config.yaml"

DEFAULT_CONFIG = {
    "hotkey": "ctrl+win",
    "model_name": "large-v3-turbo",
    "device": "cuda",
    "compute_type": "float16",
    "max_duration": 60,      # 60 seconds hard cap
    "sample_rate": 16000,    # 16 kHz mono
    "min_duration": 0.3,     # discard shorter than 300 ms
    "show_notifications": True
}

def load_config(config_path=DEFAULT_CONFIG_PATH):
    """
    Loads YAML config. If it does not exist, writes the default configuration first.
    """
    if not os.path.exists(config_path):
        logger.info(f"Configuration file {config_path} not found. Creating with defaults.")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(DEFAULT_CONFIG, f, default_flow_style=False)
            logger.info(f"Successfully wrote default configuration to {config_path}")
        except Exception as e:
            logger.error(f"Failed to write default configuration: {e}")
            return DEFAULT_CONFIG.copy()

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # Merge with default config to ensure all keys are present
        loaded_config = DEFAULT_CONFIG.copy()
        if isinstance(config, dict):
            loaded_config.update(config)
            logger.info(f"Configuration loaded successfully from {config_path}")
        else:
            logger.warning(f"Invalid format in config file {config_path}. Using default configuration.")
            
        logger.debug(f"Resolved config: {loaded_config}")
        return loaded_config
    except Exception as e:
        logger.error(f"Failed to read configuration from {config_path}: {e}. Using default configuration.")
        return DEFAULT_CONFIG.copy()
