# Dictate: Local Push-to-Talk Dictation for Windows

A lightweight system tray application that transcribes your voice locally using `faster-whisper` and pastes the text directly into the currently focused window (any application) upon releasing a global hotkey.

## Features

- **Push-to-Talk Recording:** Press and hold `Ctrl+Win` to start recording from your default microphone; release to stop.
- **Ultra-Fast Local Transcription:** Transcribes audio on-device using the GPU-accelerated `faster-whisper` model (`large-v3-turbo`).
- **Mixed-Language Support:** Automatically detects the spoken language (perfect for switching between English and Russian in a single phrase).
- **Direct Insertion:** Types transcribed text into the active Windows application using Unicode input events.
- **Clipboard Safe:** Automatic dictation never replaces the system clipboard. Selecting a saved phrase from the history menu explicitly copies it for `Ctrl+V`.
- **Dictation History:** Recover the last 20 complete transcripts from the tray menu, including text rejected by the destination field. History survives application restarts.
- **System Tray Control:** A simple icon showing the current state (Idle, Recording, Transcribing, Paused) with options to Pause/Resume and Exit.
- **Safety Hard Caps:** Restricts recording to 60 seconds maximum and discards recordings under 300 ms to avoid accidental keypresses.

---

## Requirements

1. **Operating System:** Windows 10 or 11.
2. **GPU:** NVIDIA GPU with at least 6 GB VRAM (for running `large-v3-turbo` in float16) and up-to-date NVIDIA drivers.
3. **Python:** Python 3.12 installed and added to your PATH.
4. **Permissions:** Must be run as **Administrator** so the global keyboard listener can hook keys and suppress the Windows Start Menu from popping up when releasing the hotkey.

---

## Installation

1. **Clone or download** this repository to your target folder.
2. **Open Command Prompt (or PowerShell) as Administrator** in the project directory.
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

## Running the Application

### Option A: Via Command Line (for output/debugging)
From an elevated command line:
```cmd
.venv\Scripts\python.exe -m src.main
```

### Option B: As a Background/Windowless Process
Double-click the `run.bat` script, or run:
```cmd
.venv\Scripts\pythonw.exe -m src.main
```
This runs the application without a Command Prompt window. The system tray icon is your interface.

---

## Verification & Diagnostics

Before running the application for the first time, you can verify your CUDA/Whisper pipeline configuration by running the diagnostics script:
```cmd
.venv\Scripts\python.exe scripts/check_gpu.py
```
This script will locate the NVIDIA runtimes, load the Whisper model into your GPU, perform a quick 1-second silent transcription, and report timing and device availability.

---

## Adding to Windows Startup

To launch this application automatically when Windows starts:
1. Right-click on the `run.bat` file in this directory and select **Show more options** -> **Create shortcut**.
2. Rename the new shortcut to `Dictate`.
3. Press `Win + R`, type `shell:startup`, and press Enter. This opens the Windows Startup folder.
4. Drag and drop the `Dictate` shortcut into the Startup folder.
5. *Note: Since the script needs administrative rights to block the Windows key, you may need to set the shortcut properties to "Run as Administrator", or configure it via the Windows Task Scheduler to run with highest privileges.*

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
This happens if the application is not running with administrative rights.
- **Fix:** Close the application via the tray icon, reopen your terminal **as Administrator**, and run it again. Global key hooking and event suppression require elevated permissions on Windows.

### 3. Microphone Not Recording
- Ensure the default microphone is set correctly in Windows Sound Settings.
- Verify that "Allow desktop apps to access your microphone" is toggled **On** under **Windows Privacy Settings**.

### 4. Dictation Stops After the NVIDIA GPU Disappears
- Check `logs/app.log` for `no CUDA-capable device is detected`. Windows may show the NVIDIA GPU as absent while the integrated GPU remains available.
- On a ProArt laptop, open **ProArt Creator Hub > Dashboard > GPU Mode** and select **Standard** if **Eco Mode** is active. Then restart Dictate to use CUDA again.
- While NVIDIA is unavailable, Dictate automatically loads the model on CPU with `int8` compute. The tray tooltip says `CPU fallback`; transcription may be slower. Notification permissions do not control recording or model loading.

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
- Right-click the Dictate tray icon, open **Dictation History**, and select a phrase by its time and preview. The complete text is copied, including any portion omitted from the menu preview. Focus the intended text field and press `Ctrl+V`.
- The last 20 non-empty transcripts are saved to `logs/dictation-history.json` before insertion. Storage is replaced atomically and retained across restarts; saving/automatic insertion does not copy anything to the clipboard. Only explicitly selecting a history item changes it.
- On first use, available recent transcripts are recovered from `logs/app.log` and its backups. History stores text, not audio; phrases lost during recognition itself cannot be reconstructed from it.
