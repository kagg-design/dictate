import sys
import os

# Redirect standard streams to null when running under pythonw to avoid crashes from warning prints
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

import threading
from src.logger import logger
from src.config import load_config
from src.recorder import AudioRecorder
from src.transcriber import WhisperTranscriber
from src.hotkey import HotkeyManager
from src.tray import SystemTrayApp
from src.overlay import RecordingOverlay

import ctypes

# Keep mutex reference alive for the lifetime of the process
_app_mutex = None

def check_single_instance():
    global _app_mutex
    # Unique mutex name for this user application session
    mutex_name = "Global\\DictateLocalPTTAppMutex_igerg"
    
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    
    try:
        _app_mutex = kernel32.CreateMutexW(None, True, mutex_name)
        last_error = kernel32.GetLastError()
        
        if last_error == ERROR_ALREADY_EXISTS:
            if _app_mutex:
                kernel32.CloseHandle(_app_mutex)
                _app_mutex = None
            return False
    except Exception as e:
        # Fallback to true if mutex creation fails for permission reasons
        return True
    return True

def show_already_running_message():
    try:
        # MB_OK = 0x00000000 | MB_ICONINFORMATION = 0x00000040
        ctypes.windll.user32.MessageBoxW(
            0, 
            "Another instance of Dictate is already running. Check your system tray.", 
            "Dictate Already Running", 
            0x00000040
        )
    except Exception:
        pass

def create_start_menu_shortcut():
    try:
        import subprocess
        import os
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return
            
        shortcut_path = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Dictate.lnk")
        project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        target_path = sys.executable
        arguments = "-m src.main"
        icon_path = os.path.join(project_dir, "icon.ico")
        
        # We always verify/overwrite to keep paths fresh
        ps_script = f"""
        $shortcutPath = '{shortcut_path}'
        $targetPath = '{target_path}'
        $arguments = '{arguments}'
        $workDir = '{project_dir}'
        $iconPath = '{icon_path}'
        $appId = "DictateApp"

        $source = '
        using System;
        using System.Runtime.InteropServices;
        using System.Runtime.InteropServices.ComTypes;

        namespace ShortcutHelper {{
            [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
            public class ShellLink {{}}

            [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
            public interface IShellLinkW {{
                void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cchMaxPath, out IntPtr pfd, int fFlags);
                void GetIDList(out IntPtr ppidl);
                void SetIDList(IntPtr pidl);
                void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cchMaxName);
                void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
                void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cchMaxPath);
                void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
                void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cchMaxPath);
                void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
                void GetHotkey(out short pwHotkey);
                void SetHotkey(short wHotkey);
                void GetShowCmd(out int piShowCmd);
                void SetShowCmd(int iShowCmd);
                void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cchMaxPath, out int piIcon);
                void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
                void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
                void Resolve(IntPtr hwnd, int fFlags);
                void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
            }}

            [StructLayout(LayoutKind.Sequential, Pack = 4)]
            public struct PropertyKey {{
                public Guid fmtid;
                public uint pid;
                public PropertyKey(Guid guid, uint id) {{
                    fmtid = guid;
                    pid = id;
                }}
            }}

            [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")]
            public interface IPropertyStore {{
                void GetCount(out uint cProps);
                void GetAt(uint iProp, out PropertyKey pkey);
                void GetValue(ref PropertyKey pkey, [Out] PropVariant pv);
                void SetValue(ref PropertyKey pkey, PropVariant pv);
                void Commit();
            }}

            [StructLayout(LayoutKind.Explicit)]
            public class PropVariant {{
                [FieldOffset(0)] public ushort vt;
                [FieldOffset(8)] public IntPtr pointerVal;
                
                public static PropVariant FromString(string value) {{
                    var pv = new PropVariant();
                    pv.vt = 31; // VT_LPWSTR
                    pv.pointerVal = Marshal.StringToCoTaskMemUni(value);
                    return pv;
                }}
            }}

            public class Creator {{
                public static void Create(string shortcutPath, string targetPath, string workDir, string iconPath, string appId, string arguments) {{
                    var link = (IShellLinkW)new ShellLink();
                    link.SetPath(targetPath);
                    link.SetWorkingDirectory(workDir);
                    link.SetArguments(arguments);
                    link.SetIconLocation(iconPath, 0);
                    
                    var store = (IPropertyStore)link;
                    var appIdKey = new PropertyKey(new Guid("9F4C6855-37D7-4F77-8032-D58D506DB13B"), 5);
                    store.SetValue(ref appIdKey, PropVariant.FromString(appId));
                    store.Commit();
                    
                    var file = (IPersistFile)link;
                    file.Save(shortcutPath, true);
                }}
            }}
        }}
        '

        Add-Type -TypeDefinition $source
        [ShortcutHelper.Creator]::Create($shortcutPath, $targetPath, $workDir, $iconPath, $appId, $arguments)
        """
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            logger.error(f"PowerShell shortcut creation failed: {result.stderr}")
        else:
            logger.info(f"Start Menu shortcut with AppUserModelID created/verified at: {shortcut_path}")
    except Exception as e:
        logger.error(f"Failed to create Start Menu shortcut: {e}")

def register_app_user_model_id_registry():
    if sys.platform == 'win32':
        try:
            import winreg
            project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            icon_path = os.path.join(project_dir, "icon.ico")
            
            key_path = r"Software\Classes\AppUserModelId\DictateApp"
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Dictate")
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
                winreg.SetValueEx(key, "ShowInSettings", 0, winreg.REG_DWORD, 1)
            logger.info("Successfully registered AppUserModelID 'DictateApp' in Registry.")
        except Exception as e:
            logger.error(f"Failed to register AppUserModelID in registry: {e}")

def main():
    # Set explicit App User Model ID so Windows associates notifications with "DictateApp" instead of "Python"
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DictateApp")
    except Exception:
        pass

    # Register in registry to ensure notification matching and custom icon display
    register_app_user_model_id_registry()

    # Ensure shortcut exists in the Start Menu so Windows can fetch the Dictate app icon
    create_start_menu_shortcut()


    if not check_single_instance():
        show_already_running_message()
        sys.exit(0)

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
            "max_duration": 600,
            "sample_rate": 16000,
            "min_duration": 0.3
        }

    # 2. Instantiate core components
    transcriber = WhisperTranscriber(
        model_name=config.get("model_name", "large-v3-turbo"),
        device=config.get("device", "cuda"),
        compute_type=config.get("compute_type", "float16"),
        vad_filter=config.get("vad_filter", True)
    )
    
    recorder = AudioRecorder(
        sample_rate=config.get("sample_rate", 16000),
        max_duration=config.get("max_duration", 600),
        min_duration=config.get("min_duration", 0.3),
        latency=config.get("latency", 0.08),
        ring_buffer_duration=config.get("ring_buffer_duration", 0.5)
    )

    overlay = RecordingOverlay(
        show_overlay_indicator=config.get("show_overlay_indicator", True)
    )
    overlay.start()

    # State flag to block recording until model is loaded
    model_loaded = False
    model_loading_error = False

    # 3. Define hotkey trigger callbacks
    def on_audio_start():
        logger.info("First audio block received. Activating recording indicators.")
        tray_app.set_state('recording')
        overlay.show()

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
            # Start recording and define safety timeout and audio start callbacks
            recorder.start(
                on_limit_reached=on_limit_reached,
                on_audio_start=on_audio_start
            )
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

        # Hide visual overlay
        overlay.hide()

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
        
        # Hide visual overlay
        overlay.hide()
        
        # Stop audio capture and queue transcription
        audio = recorder.stop()
        if audio is not None:
            tray_app.queue_audio(audio)
            tray_app.set_state('transcribing')
        else:
            tray_app.set_state('idle')
            
        # Reset the hotkey state and unblock Win even though the user may still
        # be holding the chord when the safety limit fires.
        hotkey_manager.cancel_active()

    # 4. Instantiate hotkey manager
    hotkey_manager = HotkeyManager(on_trigger_start, on_trigger_stop)

    # 5. Define app cleanup callback
    def on_exit():
        logger.info("Performing final application shutdown cleanup...")
        hotkey_manager.stop_listening()
        try:
            recorder.cleanup()
        except Exception:
            pass
        try:
            overlay.destroy()
        except Exception:
            pass
        logger.info("Application cleanup completed.")

    # 6. Instantiate System Tray Application
    tray_app = SystemTrayApp(
        transcriber, 
        recorder, 
        hotkey_manager, 
        on_exit,
        show_startup_notifications=config.get("show_startup_notifications", True)
    )

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
