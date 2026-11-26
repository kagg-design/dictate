import os
import threading
from pathlib import Path
from tempfile import NamedTemporaryFile
import yaml
from src.logger import logger
from src.paths import DATA_DIR, IS_INSTALLED
from src.shortcuts import DEFAULT_INSERT_LAST_SHORTCUT, DEFAULT_SHORTCUT

DEFAULT_CONFIG_PATH = DATA_DIR / "config.yaml"
_config_lock = threading.Lock()
CUE_VOLUME_OPTIONS = (0, 25, 50, 75, 100)

DEFAULT_CONFIG = {
    "hotkey": DEFAULT_SHORTCUT,
    "hotkey_mode": "hold",
    "insert_last_shortcut": DEFAULT_INSERT_LAST_SHORTCUT,
    "escape_cancels": True,
    "live_preview": False,
    "recording_sounds": True,
    "recording_cue_volume": 100,
    "duck_background_audio": False,
    "use_cursor_context": True,
    "text_replacements": {},
    "fuzzy_replacements": [],
    "postprocess_model": "",
    "postprocess_shortcut": None,
    "recognition_model": "whisper",
    "microphone": None,
    "save_wav": False,
    "wav_retention_days": 0,
    "appearance": "System",
    "window_position": None,
    "replacements_window_position": None,
    "window_page": "History",
    "collapsed_settings_sections": [],
    "model_name": "large-v3-turbo",
    "device": "auto" if IS_INSTALLED else "cuda",
    "compute_type": "float16",
    "max_duration": 600,      # 600 seconds hard cap
    "sample_rate": 16000,    # 16 kHz mono
    "min_duration": 0.3,     # discard shorter than 300 ms
    "show_startup_notifications": True,
    "latency": 0.08,
    "ring_buffer_duration": 0.5,
    "vad_filter": True,
}

def load_config(config_path=DEFAULT_CONFIG_PATH):
    """
    Loads YAML config. If it does not exist, writes the default configuration first.
    """
    if not os.path.exists(config_path):
        logger.info(f"Configuration file {config_path} not found. Creating with defaults.")
        try:
            Path(config_path).parent.mkdir(parents=True, exist_ok=True)
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


def save_recognition_model(model, config_path=DEFAULT_CONFIG_PATH):
    save_settings({"recognition_model": model}, config_path)


def save_settings(changes, config_path=DEFAULT_CONFIG_PATH):
    """Update selected values without losing concurrent settings changes."""
    with _config_lock:
        _save_settings(changes, config_path)


def _save_settings(changes, config_path):
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    if current is None:
        current = {}
    if not isinstance(current, dict):
        raise ValueError(f"Cannot update an invalid configuration: {path}")
    current.update(changes)

    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            yaml.safe_dump(current, temp, sort_keys=False)
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
