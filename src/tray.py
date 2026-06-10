import queue
import threading
from PIL import Image, ImageDraw
import pystray
from src.logger import logger
from src.inserter import paste_text

class SystemTrayApp:
    def __init__(self, transcriber, recorder, hotkey_manager, on_exit_callback):
        """
        Coordinates the system tray interface, state changes, and background tasks.
        :param transcriber: WhisperTranscriber instance.
        :param recorder: AudioRecorder instance.
        :param hotkey_manager: HotkeyManager instance.
        :param on_exit_callback: Cleanup function on app shutdown.
        """
        self.transcriber = transcriber
        self.recorder = recorder
        self.hotkey_manager = hotkey_manager
        self.on_exit_callback = on_exit_callback
        
        self.state = 'idle'
        self.is_paused = False
        
        # Thread-safe queue for processing recorded audio buffers
        self.task_queue = queue.Queue()
        self.worker_thread = None
        self.running = True
        
        # Define context menu options
        self.menu = pystray.Menu(
            pystray.MenuItem(
                text=lambda item: "Resume Dictation" if self.is_paused else "Pause Dictation",
                action=self.toggle_pause
            ),
            pystray.MenuItem(
                text="Exit",
                action=self.exit_app
            )
        )
        
        # Setup the pystray icon
        self.icon = pystray.Icon(
            name="dictate",
            icon=self._generate_icon_image('idle'),
            title="Dictate Tool",
            menu=self.menu
        )

    def start(self):
        """
        Starts the worker thread and the pystray main loop.
        """
        logger.info("Initializing background transcription worker thread...")
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            name="TranscriptionWorker",
            daemon=True
        )
        self.worker_thread.start()
        logger.info("Background transcription worker started.")
        
        logger.info("Running pystray system tray loop (blocks main thread)...")
        # Run system tray icon (blocking call, runs tray event loop)
        self.icon.run()

    def set_state(self, state):
        """
        Updates the tray icon state and graphic dynamically.
        """
        self.state = state
        self.icon.icon = self._generate_icon_image(state)
        logger.info(f"System tray state changed to: '{state.upper()}'")

    def toggle_pause(self, icon=None, item=None):
        """
        Toggles dictation listening on/off.
        """
        self.is_paused = not self.is_paused
        self.hotkey_manager.set_paused(self.is_paused)
        
        if self.is_paused:
            self.set_state('paused')
            self.show_notification("Dictation Paused", "PTT hotkey has been disabled.")
        else:
            self.set_state('idle')
            self.show_notification("Dictation Resumed", "Press Ctrl+Win to begin dictating.")
            
        # Force a context menu update to reflect label change immediately
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception as e:
                logger.debug(f"Non-critical: Failed to update tray menu manually: {e}")

    def queue_audio(self, audio_data):
        """
        Pushes a recorded numpy audio buffer into the processing queue.
        """
        if audio_data is not None:
            self.task_queue.put(audio_data)

    def show_notification(self, title, message):
        """
        Sends a balloon/toast notification to the Windows system tray.
        """
        if self.icon and pystray.Icon.HAS_NOTIFICATION:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                logger.error(f"Failed to display tray notification: {e}")

    def exit_app(self, icon=None, item=None):
        """
        Stops listeners, cleanups modules, and terminates the tray loop.
        """
        logger.info("Shutdown requested. Initiating cleanup operations...")
        self.running = False
        
        # Stop keyboard hooks
        self.hotkey_manager.stop_listening()
        
        # Stop recording stream if active
        self.recorder.stop()
        
        # Stop the tray icon loop
        if self.icon:
            self.icon.stop()
            
        # Signal worker thread termination
        self.task_queue.put(None)
        
        # Invoke root main cleanup callback
        if self.on_exit_callback:
            try:
                self.on_exit_callback()
            except Exception as e:
                logger.error(f"Error executing exit callback: {e}")

    def _worker_loop(self):
        """
        Background worker thread processing transcription tasks sequentially.
        """
        while self.running:
            try:
                # Retrieve recorded buffer (blocks up to 1 second)
                audio_data = self.task_queue.get(timeout=1.0)
                if audio_data is None:
                    # Termination sentinel received
                    break
                    
                self.set_state('transcribing')
                try:
                    # Process transcription on GPU
                    text = self.transcriber.transcribe(audio_data)
                    if text:
                        # Paste text in focused window
                        paste_text(text)
                    else:
                        logger.info("Transcription yielded empty result. Skipping insertion.")
                except Exception as e:
                    logger.error(f"Error inside transcription worker loop: {e}")
                    self.show_notification(
                        "Transcription Error", 
                        f"An error occurred: {str(e)}"
                    )
                finally:
                    # Revert to standard state after execution
                    if self.is_paused:
                        self.set_state('paused')
                    else:
                        self.set_state('idle')
                        
                self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Unhandled exception in background worker loop: {e}")

    def _generate_icon_image(self, state):
        """
        Procedurally draws high-resolution pixel art microphone icons.
        """
        # Create transparent canvas
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # State styling definitions
        if state == 'idle':
            color = (240, 240, 240, 255)       # Bright white
        elif state == 'recording':
            color = (255, 60, 60, 255)         # Neon Red
        elif state == 'transcribing':
            color = (0, 191, 255, 255)         # Neon sky blue
        elif state == 'paused':
            color = (128, 128, 128, 128)       # Translucent/dim gray
        else:
            color = (240, 240, 240, 255)
            
        # Draw Microphone Shape:
        # 1. Rounded rectangle capsule (Center x: 32, y range: 12-36)
        draw.rounded_rectangle([24, 12, 40, 36], radius=8, fill=color)
        
        # 2. Stand cradle (horizontal circle arc under capsule)
        draw.arc([18, 20, 46, 44], start=0, end=180, fill=color, width=4)
        
        # 3. Support stem (vertical line connecting base and cradle)
        draw.line([32, 44, 32, 52], fill=color, width=4)
        
        # 4. Base stand (horizontal plate)
        draw.line([22, 52, 42, 52], fill=color, width=4)
        
        # Draw status dot in top-right area for active states
        if state == 'recording':
            draw.ellipse([46, 6, 56, 16], fill=(255, 0, 0, 255))
        elif state == 'transcribing':
            draw.ellipse([46, 6, 56, 16], fill=(0, 191, 255, 255))
            
        return image
