# Dictate for macOS beta

Requires macOS 15 or later on an Apple Silicon Mac. Open the disk image and drag **Dictate** into **Applications**. Start Dictate and choose **Open Dictate** from its waveform icon in the menu bar.

This beta uses an ad hoc signature. If macOS asks you to confirm the app from this download, use Apple's [first-launch instructions](https://support.apple.com/en-us/102445).

In Dictate settings, allow **Microphone**, **Input Monitoring**, and **Accessibility**. After granting Input Monitoring, choose **Quit Dictate** from the menu bar and reopen it from **Applications**. Wait for **Ready to dictate**. In a TextEdit document, hold **Control + Option**, speak, then release. Dictate stores the result in **History** if the target window or field changes before insertion.

**Fluid Speech Mini** uses Core ML and **Whisper Large v3 Turbo** uses Metal. Each downloads its model on first selection and then runs locally. Fluid Speech Mini is provided by FluidVoice. **0 ms pre-roll** keeps the microphone closed between dictations. Positive pre-roll keeps the microphone open and holds only the selected length of recent sound in memory until dictation starts.

The beta uses the current macOS default microphone and decodes completed phrases as you speak. A long uninterrupted phrase is finalized on release.

In **Settings**, choose the shortcut and enable **Press to start / stop** for hands-free dictation. With **Esc cancels and keeps text in History** enabled, Escape ends the recording and saves any recognized text without inserting it. **Copy Last Dictation** is available in the menu bar and with **Control + Command + V**.

Choose **Insert Last Dictation** in the menu bar or press **Control + Command + F9** to insert the most recent transcript into the focused editor without using the clipboard. Under **Settings → Dictation controls**, click either shortcut and hold two or three keys together to record it. Use modifiers alone or add one other key. You can turn off the insert shortcut and still use the menu.

A pulsing red microphone appears while Dictate is recording. **Play recording sounds** plays distinct start and stop cues. Adjust **Recording cue volume** and use **Test sounds** to hear both cues. **Show live transcript while dictating** adds text to the indicator and is off by default. Enable **Start Dictate at login** to use the macOS login item. If macOS asks for approval, enable Dictate under **System Settings → General → Login Items**.

Select a word or phrase in **History** and choose **Add replacement…** to fix its spelling in future dictations. Edit or remove saved rules under **Settings → Text replacements**.

Use **Pin** and **Pinned only** to keep and find important dictations. With **Save WAV files in History** enabled, use **Open recordings folder** to find the audio in Finder. Choose an audio retention period in Settings to remove older unpinned WAVs.

For optional text editing, start Ollama, choose an installed model in **Settings → Local post-processing**, and set **Dictate with editing**. The selected model warms up in the background after the speech model loads.

## Build

On an Apple Silicon Mac with Xcode 26 or newer selected and CMake on PATH, run these commands.

```bash
git clone https://github.com/kagg-design/dictate.git
cd dictate
bash macos/build.sh
```

The build runs tests, signs the app ad hoc, and writes `Dictate-macOS-arm64-beta.dmg` and its SHA-256 file to `artifacts/macos/`. Set `DICTATE_SMOKE_AUDIO` to a local 16 kHz mono WAV to run both packaged models before creating the image. Set `DICTATE_SMOKE_EXPECTED` to check the decoded phrase.
