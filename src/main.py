import sys
import threading
from src.logger import logger
from src.config import load_config
from src.recorder import AudioRecorder
from src.transcriber import WhisperTranscriber
from src.hotkey import HotkeyManager
from src.tray import SystemTrayApp

def main():
    logger.info("Starting Dictate application...")
    
    # 1. Load configuration
    try:
        config = load_config()
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}. Using runtime defaults.")
        config = {
            "hotkey": "ctrl+win",
            "model_name": "large-v3-turbo",
            "device": "cuda",
            "compute_type": "float16",
            "max_duration": 60,
            "sample_rate": 16000,
            "min_duration": 0.3
        }

    # 2. Instantiate core components
    transcriber = WhisperTranscriber(
        model_name=config.get("model_name", "large-v3-turbo"),
        device=config.get("device", "cuda"),
        compute_type=config.get("compute_type", "float16")
    )
    
    recorder = AudioRecorder(
        sample_rate=config.get("sample_rate", 16000),
        max_duration=config.get("max_duration", 60),
        min_duration=config.get("min_duration", 0.3)
    )

    # State flag to block recording until model is loaded
    model_loaded = False
    model_loading_error = False

    # 3. Define hotkey trigger callbacks
    def on_trigger_start():
        nonlocal model_loaded, model_loading_error
        if model_loading_error:
            logger.warning("Hotkey pressed but Whisper model failed to load.")
            tray_app.show_notification(
                "Dictation Unavailable", 
                "Model failed to load. Check logs for details."
            )
            return
            
        if not model_loaded:
            logger.warning("Hotkey pressed but Whisper model is still loading.")
            tray_app.show_notification(
                "Please Wait", 
                "Whisper model is still loading into GPU VRAM..."
            )
            return

        try:
            # Set state to recording
            tray_app.set_state('recording')
            
            # Start recording and define safety timeout callback
            recorder.start(on_limit_reached=on_limit_reached)
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            tray_app.set_state('idle')
            tray_app.show_notification(
                "Recording Error", 
                f"Failed to start audio stream: {str(e)}"
            )

    def on_trigger_stop():
        nonlocal model_loaded
        if not model_loaded or not recorder.is_recording:
            return

        # Stop recording and fetch audio buffer
        audio = recorder.stop()
        if audio is not None:
            # Queue the audio for background transcription thread
            tray_app.queue_audio(audio)
            tray_app.set_state('transcribing')
        else:
            # Audio was discarded (e.g. too short)
            tray_app.set_state('idle')

    def on_limit_reached():
        """
        Triggered when recording reaches the 60-second safety limit.
        """
        logger.info("Recording safety hard cap reached. Forcing stop.")
        # Stop audio capture and queue transcription
        audio = recorder.stop()
        if audio is not None:
            tray_app.queue_audio(audio)
            tray_app.set_state('transcribing')
        else:
            tray_app.set_state('idle')
            
        # Reset the hotkey manager state so releasing the hotkey afterwards is ignored
        hotkey_manager.is_active = False

    # 4. Instantiate hotkey manager
    hotkey_manager = HotkeyManager(on_trigger_start, on_trigger_stop)

    # 5. Define app cleanup callback
    def on_exit():
        logger.info("Performing final application shutdown cleanup...")
        hotkey_manager.stop_listening()
        logger.info("Application cleanup completed.")

    # 6. Instantiate System Tray Application
    tray_app = SystemTrayApp(transcriber, recorder, hotkey_manager, on_exit)

    # 7. Define asynchronous setup task for loading the model and starting listeners
    def async_init(icon):
        nonlocal model_loaded, model_loading_error
        
        # Start global keyboard listener
        try:
            hotkey_manager.start_listening()
        except Exception as e:
            logger.critical(f"Failed to start hotkey manager: {e}")
            tray_app.show_notification(
                "Initialization Error", 
                "Failed to register hotkeys. Run as Administrator."
            )
            # Disable app interaction
            tray_app.is_paused = True
            tray_app.set_state('paused')
            return

        # Load Whisper model into GPU
        tray_app.show_notification(
            "Dictate Tool", 
            "Pre-loading Whisper model (large-v3-turbo) into GPU VRAM..."
        )
        
        try:
            transcriber.load_model()
            model_loaded = True
            logger.info("Application initialization complete.")
            tray_app.show_notification(
                "Dictate Ready", 
                "Model loaded on CUDA. Hold Ctrl+Win to dictate."
            )
        except Exception as e:
            model_loading_error = True
            logger.critical(f"Failed to initialize Whisper model: {e}")
            tray_app.show_notification(
                "CUDA Whisper Error", 
                f"Failed to load Whisper model on GPU: {str(e)}"
            )
            # Pause dictation hotkeys
            tray_app.is_paused = True
            hotkey_manager.set_paused(True)
            tray_app.set_state('paused')

    # 8. Start system tray (blocks main thread until exited)
    # The async_init runs on a background thread automatically once the tray is ready
    try:
        tray_app.start(setup_callback=async_init)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Shutting down.")
        tray_app.exit_app()
    except Exception as e:
        logger.critical(f"Unhandled exception in main tray thread: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
