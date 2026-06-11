import queue
import threading
from PIL import Image, ImageDraw
import pystray
from src.logger import logger
from src.inserter import paste_text

class SystemTrayApp:
    def __init__(self, transcriber, recorder, hotkey_manager, on_exit_callback, show_startup_notifications=True):
        """
        Coordinates the system tray interface, state changes, and background tasks.
        :param transcriber: WhisperTranscriber instance.
        :param recorder: AudioRecorder instance.
        :param hotkey_manager: HotkeyManager instance.
        :param on_exit_callback: Cleanup function on app shutdown.
        :param show_startup_notifications: Boolean flag to enable/disable tray startup balloon notifications.
        """
        self.transcriber = transcriber
        self.recorder = recorder
        self.hotkey_manager = hotkey_manager
        self.on_exit_callback = on_exit_callback
        self.show_startup_notifications = show_startup_notifications
        
        self.state = 'idle'
        self.is_paused = False
        
        # Thread-safe queue for processing recorded audio buffers
        self.task_queue = queue.Queue()
        self.worker_thread = None
        
        # Thread-safe queue and thread for sequential UI icon updates (fixes Win32 thread-affinity bugs)
        self.ui_queue = queue.Queue()
        self.ui_thread = None
        
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
    def start(self, setup_callback=None):
        """
        Starts the worker thread, UI thread, and the pystray main loop.
        """
        logger.info("Initializing background transcription worker thread...")
        self.worker_thread = threading.Thread(
            target=self._worker_loop,
            name="TranscriptionWorker",
            daemon=True
        )
        self.worker_thread.start()
        logger.info("Background transcription worker started.")
        
        logger.info("Initializing background UI update thread...")
        self.ui_thread = threading.Thread(
            target=self._ui_update_loop,
            name="UIUpdateWorker",
            daemon=True
        )
        self.ui_thread.start()
        logger.info("Background UI update thread started.")
        
        logger.info("Running pystray system tray loop (blocks main thread)...")
        # Run system tray icon (blocking call, runs tray event loop)
        if setup_callback:
            def setup_wrapper(icon):
                logger.info("Setting tray icon visibility to True in setup wrapper...")
                icon.visible = True
                setup_callback(icon)
            self.icon.run(setup=setup_wrapper)
        else:
            self.icon.visible = True
            self.icon.run()

    def set_state(self, state):
        """
        Queues the tray icon state change for the UI thread to execute.
        """
        self.state = state
        self.ui_queue.put(state)
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
        if not self.show_startup_notifications:
            return
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
            
        # Signal worker and UI threads termination
        self.task_queue.put(None)
        self.ui_queue.put(None)
        
        # Invoke root main cleanup callback
        if self.on_exit_callback:
            try:
                self.on_exit_callback()
            except Exception as e:
                logger.error(f"Error executing exit callback: {e}")

    def _ui_update_loop(self):
        """
        Background loop running on a single dedicated thread to execute all 
        tray icon state changes. This ensures all HICON resources are created 
        and destroyed on the same thread, preventing Win32 cursor handle errors.
        """
        while self.running:
            try:
                state = self.ui_queue.get(timeout=1.0)
                if state is None:
                    # Termination sentinel received
                    break
                try:
                    self.icon.icon = self._generate_icon_image(state)
                except Exception as e:
                    logger.debug(f"UI thread failed to update icon handle: {e}")
                self.ui_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Error in UI update loop: {e}")

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
        Procedurally draws high-contrast rounded square microphone icons (32x32).
        """
        # Create RGBA canvas for smooth circle corners
        image = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # State styling definitions (background rounded square badge + foreground microphone)
        if state == 'idle':
            bg_color = (51, 58, 77, 255)        # Dark bluish-gray matching icon.png
            fg_color = (172, 255, 255, 255)    # Light cyan matching icon.png
        elif state == 'recording':
            bg_color = (180, 10, 10, 255)       # Red square
            fg_color = (255, 220, 220, 255)    # Pinkish-white microphone
        elif state == 'transcribing':
            bg_color = (10, 100, 180, 255)     # Sky blue square
            fg_color = (220, 240, 255, 255)    # Light blue microphone
        elif state == 'paused':
            bg_color = (30, 30, 30, 180)        # Translucent dark square
            fg_color = (128, 128, 128, 255)    # Gray microphone
        else:
            bg_color = (51, 58, 77, 255)
            fg_color = (172, 255, 255, 255)
            
        # Draw background badge rounded rectangle (centered, 28x28 pixels)
        draw.rounded_rectangle([2, 2, 29, 29], radius=6, fill=bg_color)
        
        # Draw Microphone Shape:
        # 1. Elongated rounded rectangle capsule (Center x: 15.5, y range: 4-15)
        draw.rounded_rectangle([13, 4, 18, 15], radius=3, fill=fg_color)
        
        # 2. Stand cradle (horizontal circle arc under capsule, sides hugging higher)
        draw.arc([10, 9, 21, 20], start=-20, end=200, fill=fg_color, width=2)
        
        # 3. Support stem (vertical line connecting base and cradle)
        draw.line([16, 20, 16, 25], fill=fg_color, width=2)
        
        # 4. Base stand (horizontal plate)
        draw.line([12, 25, 19, 25], fill=fg_color, width=2)
        
        # Draw status dot in top-right area for active states
        if state == 'recording':
            draw.ellipse([22, 5, 27, 10], fill=(255, 60, 60, 255))
        elif state == 'transcribing':
            draw.ellipse([22, 5, 27, 10], fill=(0, 191, 255, 255))
            
        return image
