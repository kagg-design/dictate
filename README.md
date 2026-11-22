# Dictate: Local Push-to-Talk Dictation for Windows

A Windows dictation app that runs Fluid Speech Mini or Whisper locally and inserts the final text into the focused window when you release the hotkey.

## Install on Windows

1. Open [GitHub Releases](https://github.com/kagg-design/dictate/releases) and download `Dictate-Setup-x64.exe` from the release's **Assets**. The installer is for Windows 10 and 11 on x64 PCs and includes its own Python and Inter font.
2. Run Setup. If you have an NVIDIA GPU, keep **Download NVIDIA libraries for GPU acceleration** selected. This downloads about 1.3 GB and uses your existing NVIDIA driver. Setup warns when it expects to run without GPU acceleration and lets you continue in the slower CPU mode or cancel. Dictate shows **CPU mode** whenever Whisper is using the processor.
3. Open **Dictate** from the Start menu. Give it time to download the Whisper model on first launch. After the model is ready, hold `Ctrl+Win` while you speak and release it to insert the text.

Whisper runs on your computer once the model has downloaded. To use Fluid Speech Mini, install FluidVoice for Windows, download and activate Mini there, then select it in Dictate's **Settings**.

The installer is unsigned. Compare the file with the SHA-256 checksum attached to the same release if Windows shows an unknown publisher. If Dictate reports a missing `MSVCP140.dll` or `VCRUNTIME140.dll`, install Microsoft's [Visual C++ Redistributable for x64](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/).

Close Dictate from its tray menu before updating or uninstalling. Remove it under **Settings → Apps → Installed apps**. Dictate keeps configuration, history, and saved WAVs under `%LOCALAPPDATA%\Dictate` across upgrades and uninstall.

## Features

- **Push-to-Talk Recording:** Press and hold `Ctrl+Win` to start recording from your default microphone; release to stop.
- **Ultra-Fast Local Transcription:** Transcribes audio on-device using the GPU-accelerated `faster-whisper` model (`large-v3-turbo`).
- **Mixed-Language Support:** Automatically detects the spoken language (perfect for switching between English and Russian in a single phrase).
- **Direct Insertion:** Types transcribed text into the active Windows application using Unicode input events.
- **Cursor Context:** When an editable field exposes its caret or selection to Windows UI Automation, Dictate adjusts the first letter, final period, and spaces to fit the surrounding text. Recognized search and address fields keep the dictated capitalization and omit a final period. The previous insertion behavior applies when context is unavailable.
- **Clipboard Safe:** Automatic dictation leaves the clipboard alone. Use **Copy text** in History to copy a saved transcript.
- **Dictation History:** Search and recover the latest 100 complete transcripts, including text rejected by the destination field. Optional WAV audio plays from the History window.
- **System Tray Control:** Click the icon to open Dictate. The right-click menu offers Pause/Resume and Exit.
- **Safety Hard Caps:** Restricts recording to 60 seconds maximum and discards recordings under 300 ms to avoid accidental keypresses.

---

## Running from source

1. **Operating System:** Windows 10 or 11.
2. **GPU:** NVIDIA GPU with at least 6 GB VRAM (for running `large-v3-turbo` in float16) and up-to-date NVIDIA drivers.
3. **Python:** Python 3.12 installed and added to your PATH.
4. **Permissions:** Use a standard account for ordinary desktop apps. If Windows blocks dictation into a target app running as administrator, launch Dictate at the same elevation as the target.

---

### Source installation

1. **Clone or download** this repository to your target folder.
2. **Open Command Prompt or PowerShell** in the project directory.
3. **Create a virtual environment:**
   ```cmd
   python -m venv .venv
   ```
4. **Activate the virtual environment:**
   ```cmd
   .venv\Scripts\activate
   ```
5. **Install dependencies:**
   ```cmd
   pip install -r requirements.txt
   ```

---

### Starting from the checkout

### Option A: Via Command Line (for output/debugging)
From the project directory:
```cmd
.venv\Scripts\python.exe -m src.main
```

### Option B: As a Background/Windowless Process
Double-click the `run.bat` script, or run:
```cmd
.venv\Scripts\pythonw.exe -m src.main
```
This starts Dictate in the system tray. Click its icon or choose **Dictate** in the Start menu to open the History and Settings window.

### Building a Windows release

Build on an x64 Windows PC with Python 3.12 (including Tk) and [Inno Setup 6](https://jrsoftware.org/isdl.php). From the repository root, run this in PowerShell. Substitute the path to `ISCC.exe` if necessary.

```powershell
py -3.12 scripts/build_windows.py --compiler 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe' --version 0.1.0
```

The build checks the packaged Python runtime and produces `Dictate-Setup-x64.exe` and `Dictate-Setup-x64.exe.sha256` in `artifacts/windows/`. Attach both files to a GitHub Release for the matching tag. The binary is a Release asset and stays out of the source tree.

---

## Verification & Diagnostics

Before running the application for the first time, you can verify your CUDA/Whisper pipeline configuration by running the diagnostics script:
```cmd
.venv\Scripts\python.exe scripts/check_gpu.py
```
This script will locate the NVIDIA runtimes, load the Whisper model into your GPU, perform a quick 1-second silent transcription, and report timing and device availability.

---

## Settings

Open **Settings** to choose a microphone and select Whisper or Fluid Speech Mini. The input meter shows whether the selected microphone is receiving sound. Dictate saves the selection after opening the microphone or loading the model.

Fluid Speech Mini uses the model and CUDA runtime already installed by FluidVoice. Install FluidVoice for Windows and enable Mini there once to make those files available. For a separate installation of the same runtime and model, set `fluid_mini_dll` and `fluid_mini_model` in `config.yaml`.

Dictate uses the microphone's pre-roll at the start of each recording. During recording, Fluid Speech Mini processes overlapping audio windows, including continuous speech. Whisper processes completed phrases at pauses. Dictate assembles the final dictation and inserts it once when you release the hotkey.

## Adding to Windows Startup

To launch this application automatically when Windows starts:
1. Right-click on the `run.bat` file in this directory and select **Show more options** -> **Create shortcut**.
2. Rename the new shortcut to `Dictate`.
3. Press `Win + R`, type `shell:startup`, and press Enter. This opens the Windows Startup folder.
4. Drag and drop the `Dictate` shortcut into the Startup folder.

---

## Troubleshooting

### 1. `ValueError: Library cublas64_12.dll is not found` or `cudnn64_8.dll is not found`
This happens when CTranslate2/faster-whisper cannot locate the NVIDIA DLLs.
- **Our Solution:** The application dynamically detects and adds `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` pip packages to Windows DLL directories during startup.
- If you still encounter this error, ensure the packages installed correctly:
  ```cmd
  pip install --force-reinstall nvidia-cublas-cu12 nvidia-cudnn-cu12
  ```
- Alternatively, you can download the cuBLAS and cuDNN DLLs from NVIDIA and place them directly in your Python folder or add them to your Windows System `PATH`.

### 2. Windows Start Menu Pops Up when Dictating
- Close applications that also listen for the same hotkey. If the target application was started as administrator, restart Dictate at the same elevation and try again.

### 3. Microphone Not Recording
- Ensure the default microphone is set correctly in Windows Sound Settings.
- Verify that "Allow desktop apps to access your microphone" is toggled **On** under **Windows Privacy Settings**.

### 4. Dictation Stops After the NVIDIA GPU Disappears
- Check `logs/app.log` for `no CUDA-capable device is detected`. Windows may show the NVIDIA GPU as absent while the integrated GPU remains available.
- On a ProArt laptop, open **ProArt Creator Hub > Dashboard > GPU Mode** and select **Standard** if **Eco Mode** is active. Then restart Dictate to use CUDA again.
- Whisper can fall back to CPU with `int8` compute. The tray tooltip indicates the active model and device. Fluid Speech Mini uses the CUDA runtime.

### 5. Long Text Appears in Bursts or Turns into Spaces
- Dictate collects the complete transcript before inserting it. Delays after the first words appear belong to text input, rather than continued speech recognition.
- Native Edit/RichEdit controls (including Notepad) receive the entire text through `EM_REPLACESEL`. Chromium controls receive literal `WM_CHAR` messages, avoiding the `VK_PACKET` path that can corrupt delayed Unicode input. Other controls use paced keyboard input. Automatic insertion does not replace the system clipboard.
- Diagnostics are written automatically to `logs/app.log`; older parts are `app.log.1`, `app.log.2`, and `app.log.3`. These files include the recognized text.
- `Transcription completed` reports recognition time and device. `Text delivered via` reports the control class, native input method, and delivery time. `Unicode keyboard events submitted` reports keyboard fallback submission time; it does not confirm that the receiving application displayed the text correctly.
- To check text insertion independently of the microphone/GPU, run `.venv\Scripts\pythonw.exe scripts\check_text_input.py`, start its countdown, and focus an empty test editor. The diagnostic writes to `logs/text-input-check.log`.

### 6. Dictate Disappears After Sleep or Closing the Lid
- `logs/diagnostics.log` records process IDs, startup/shutdown, Windows suspend/resume notifications, uncaught Python exceptions, and a heartbeat every 60 seconds. Each heartbeat includes tray/worker state and the age of the latest microphone callback, without querying the audio/GPU drivers. Older parts are `diagnostics.log.1` through `.3`.
- `logs/crash.log` captures windowless Python stderr and fatal native exception tracebacks from `faulthandler`. It is opened before native imports and kept open for the entire process. It appends across restarts; process-start headers distinguish sessions. An external kill or power loss may leave no crash traceback or shutdown marker.
- To test, finish dictating and wait for the idle tray icon, then choose **Start > Power > Sleep** manually. Leave the laptop asleep for 1–2 minutes, wake it, unlock it, and dictate a short sentence in Notepad. Wait another minute for a heartbeat. A healthy run retains the same PID, reports suspend/resume, and continues receiving microphone callbacks and transcribing.
- If the icon disappears, preserve these journals before restarting. Compare them with Windows **System** events from **Kernel-Power** (506/507 on Modern Standby), and **Application** errors. A short successful test does not rule out a failure during an entire night; repeat the usual lid-close scenario afterwards.

### 7. Recover Dictation Rejected by a Field
- Click the Dictate tray icon and open **History**. Search or select a row to read a full transcript, then use **Copy text** to copy it.
- The latest 100 non-empty transcripts are saved locally to `logs/dictation-history.json` before insertion. On first use, available transcripts can be recovered from the application logs.
- To retain audio for future dictations, turn on **Save WAV audio locally** in Settings. WAV files are saved after text insertion and are playable from History. They stay with their transcripts until the entry ages out.
