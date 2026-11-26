import sys
import os

# Enable crash reporting before Tk, PortAudio, CUDA or other native imports.
from src.diagnostics import RuntimeDiagnostics
diagnostics = RuntimeDiagnostics().install()

# pythonw has no stdout. stderr is retained in logs/crash.log by diagnostics.
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')

from src.logger import logger
from src.config import load_config
from src.recorder import AudioRecorder
from src.transcriber import WhisperTranscriber
from src.fluid_mini import FluidMiniTranscriber
from src.hotkey import HotkeyManager
from src.tray import SystemTrayApp
from src.desktop import DesktopApp
from src.paths import IS_INSTALLED

import ctypes

# Keep mutex reference alive for the lifetime of the process
_app_mutex = None
_installed_app_mutex = None

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
        arguments = "-m src.main --show"
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
    global _installed_app_mutex
    desktop = DesktopApp()
    if not check_single_instance():
        if not desktop.show_running_instance():
            show_already_running_message()
        desktop.close()
        diagnostics.mark_clean_shutdown("opened running Dictate")
        return

    if IS_INSTALLED:
        _installed_app_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, r"Local\KAGG.Dictate.Installed")

    # Match the appropriate Start menu shortcut for Windows notifications.
    try:
        app_id = "KAGG.Dictate" if IS_INSTALLED else "DictateApp"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass

    if not IS_INSTALLED:
        register_app_user_model_id_registry()
        create_start_menu_shortcut()


    logger.info("Starting Dictate application...")
    
    config = load_config()
    models = {
        "whisper": (
            f"Whisper ({config['model_name']})",
            lambda: WhisperTranscriber(
                model_name=config["model_name"],
                device=config["device"],
                compute_type=config["compute_type"],
                vad_filter=config["vad_filter"],
            ),
        ),
        "fluid-speech-mini": (
            "Fluid Speech Mini",
            lambda: FluidMiniTranscriber(
                sample_rate=config["sample_rate"],
                model_path=config.get("fluid_mini_model"),
                dll_path=config.get("fluid_mini_dll"),
            ),
        ),
    }
    initial_model = config["recognition_model"]
    if not isinstance(initial_model, str) or initial_model not in models:
        logger.warning("Unknown speech model %r. Using Whisper.", initial_model)
        initial_model = "whisper"

    recorder = AudioRecorder(
        sample_rate=config.get("sample_rate", 16000),
        max_duration=config.get("max_duration", 600),
        min_duration=config.get("min_duration", 0.3),
        latency=config.get("latency", 0.08),
        ring_buffer_duration=config.get("ring_buffer_duration", 0.5),
        microphone=config["microphone"] if isinstance(config["microphone"], str) else None,
    )

    # 3. Define hotkey trigger callbacks
    def on_audio_start():
        logger.info("First audio block received. Activating recording indicators.")
        tray_app.set_state('recording')
        desktop.set_recording(True)

    def on_trigger_start():
        try:
            return tray_app.start_capture(
                on_limit_reached=on_limit_reached,
                on_audio_start=on_audio_start,
            )
        except Exception as e:
            logger.error(f"Failed to start recording: {e}")
            tray_app.set_state('idle')
            tray_app.show_notification(
                "Recording Error", 
                f"Failed to start audio stream: {str(e)}"
            )
            return False

    def on_trigger_stop():
        tray_app.finish_capture()
        desktop.set_recording(False)

    def on_trigger_cancel(keep_in_history):
        tray_app.finish_capture(insert=False, keep_in_history=keep_in_history)
        desktop.set_recording(False)

    def on_limit_reached():
        logger.info("Recording safety hard cap reached. Forcing stop.")
        desktop.set_recording(False)
        # Disarm the recording while tracking any keys still physically held.
        hotkey_manager.cancel_active()

    # 4. Instantiate hotkey manager
    hotkey_manager = HotkeyManager(
        on_trigger_start, on_trigger_stop, on_cancel=on_trigger_cancel,
        on_copy=lambda: tray_app.copy_latest(),
        shortcut=config["hotkey"], mode=config["hotkey_mode"], escape_cancels=config["escape_cancels"],
    )

    # 5. Define app cleanup callback
    def on_exit():
        logger.info("Performing final application shutdown cleanup...")
        hotkey_manager.stop_listening()
        try:
            recorder.cleanup()
        except Exception:
            pass
        try:
            desktop.close()
        except Exception:
            pass
        logger.info("Application cleanup completed.")
        diagnostics.mark_clean_shutdown("tray Exit requested and cleanup completed")

    # 6. Instantiate System Tray Application
    tray_app = SystemTrayApp(
        None,
        recorder, 
        hotkey_manager, 
        on_exit,
        show_startup_notifications=config.get("show_startup_notifications", True),
        models=models,
        open_window=desktop.show,
        save_wav=config["save_wav"] is True,
        wav_retention_days=config["wav_retention_days"],
        live_preview=config["live_preview"] is True,
        use_cursor_context=config["use_cursor_context"] is not False,
        text_replacements=config["text_replacements"],
        fuzzy_replacements=config["fuzzy_replacements"],
    )
    desktop.start(tray_app, appearance=config["appearance"])

    def diagnostic_state():
        # Read Python-owned state only; do not call Tk or the audio/GPU drivers
        # from the diagnostic thread, especially while Windows is suspending.
        state = recorder.diagnostic_state()
        engine = tray_app.transcriber
        state.update({
            "tray_state": tray_app.state,
            "tray_running": tray_app.running,
            "tray_visible": tray_app.icon.visible,
            "paused": tray_app.is_paused,
            "model_loaded": engine is not None,
            "recognition_model": tray_app.model_name,
            "backend": engine.device if engine else None,
            "worker_alive": bool(tray_app.worker_thread and tray_app.worker_thread.is_alive()),
            "tray_ui_alive": bool(tray_app.ui_thread and tray_app.ui_thread.is_alive()),
            "desktop_alive": bool(desktop.thread and desktop.thread.is_alive()),
            "queued_transcriptions": tray_app.task_queue.qsize(),
        })
        return state

    diagnostics.start_monitoring(diagnostic_state)

    # 7. Define asynchronous setup task for loading the model and starting listeners
    def async_init(icon):
        if "--show" in sys.argv:
            desktop.show()
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

        tray_app.select_model(initial_model, persist=False)

    # 8. Start system tray (blocks main thread until exited)
    # The async_init runs on a background thread automatically once the tray is ready
    try:
        tray_app.start(setup_callback=async_init)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Shutting down.")
        tray_app.exit_app()
    except Exception as e:
        logger.critical("Unhandled exception in main tray thread", exc_info=True)
        raise
    finally:
        diagnostics.record_snapshot("Tray event loop returned")
        desktop.close()
        if tray_app.running:
            diagnostics.log.error("Tray event loop ended without an Exit request")

if __name__ == "__main__":
    main()
