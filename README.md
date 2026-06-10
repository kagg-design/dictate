# Dictate: Local Push-to-Talk Dictation for Windows

A lightweight system tray application that transcribes your voice locally using `faster-whisper` and pastes the text directly into the currently focused window (any application) upon releasing a global hotkey.

## Features

- **Push-to-Talk Recording:** Press and hold `Ctrl+Win` to start recording from your default microphone; release to stop.
- **Ultra-Fast Local Transcription:** Transcribes audio on-device using the GPU-accelerated `faster-whisper` model (`large-v3-turbo`).
- **Mixed-Language Support:** Automatically detects the spoken language (perfect for switching between English and Russian in a single phrase).
- **Direct Insertion:** Automatically pastes the transcribed text into whichever application is currently active.
- **Smart Clipboard Recovery:** Restores the previous contents of your clipboard automatically after pasting.
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
