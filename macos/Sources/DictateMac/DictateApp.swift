import AppKit
import ApplicationServices
import AVFoundation
import SwiftUI

@main
struct EntryPoint {
    @MainActor
    static func main() async {
        if CommandLine.arguments.count >= 3 && CommandLine.arguments[1] == "--check-models" {
            do {
                let file = try AVAudioFile(forReading: URL(fileURLWithPath: CommandLine.arguments[2]))
                guard file.processingFormat.sampleRate == 16_000, file.processingFormat.channelCount == 1,
                      let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length)) else {
                    throw DictateError("Use a 16 kHz mono WAV for the model check.")
                }
                try file.read(into: buffer)
                guard let pcm = buffer.floatChannelData?.pointee else { throw DictateError("Cannot read WAV samples.") }
                let samples = Array(UnsafeBufferPointer(start: pcm, count: Int(buffer.frameLength)))
                let expected = CommandLine.arguments.dropFirst(3).first ?? ""
                let engine = SpeechEngine()
                for model in SpeechModel.allCases {
                    try await engine.prepare(model, in: DictateFiles.models)
                    let text = try await engine.transcribe(samples)
                    guard !text.isEmpty, expected.isEmpty || text.localizedCaseInsensitiveContains(expected) else {
                        throw DictateError(model.label + " did not recognize the expected speech.")
                    }
                    print(model.label + " passed the speech check.")
                    try Task.checkCancellation()
                    await engine.unload()
                }
                return
            } catch {
                FileHandle.standardError.write(Data((error.localizedDescription + "\n").utf8))
                exit(1)
            }
        }
        DictateApp.main()
    }
}

private struct DictateApp: App {
    @StateObject private var state = DictateState()

    var body: some Scene {
        MenuBarExtra {
            DictateMenu(state: state)
        } label: {
            Image(systemName: state.recording ? "microphone.fill" : "waveform")
                .foregroundStyle(state.recording ? Color.red : Color.primary)
                .accessibilityLabel(state.recording ? "Dictate is recording" : "Dictate")
        }

        Window("Dictate", id: "dictate") {
            DictateWindow(state: state)
        }
        .defaultSize(width: 720, height: 510)
    }
}

private struct DictateMenu: View {
    @ObservedObject var state: DictateState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Open Dictate") {
            openWindow(id: "dictate")
            NSApp.activate(ignoringOtherApps: true)
        }
        Divider()
        Text(state.status)
        Button("Quit Dictate") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}

@MainActor
final class DictateState: ObservableObject {
    @Published private(set) var selectedModel: SpeechModel
    @Published private(set) var preRoll: Int
    @Published private(set) var saveWav: Bool
    @Published private(set) var status = "Starting Dictate…"
    @Published private(set) var microphoneAllowed = false
    @Published private(set) var keyboardAllowed = false
    @Published private(set) var insertionAllowed = false
    @Published private(set) var loading = false
    @Published private(set) var recording = false
    @Published private(set) var finishing = false
    @Published private(set) var history: [HistoryEntry] = []

    private let recorder = AudioCapture()
    private let shortcut = DictationShortcut()
    private let engine = SpeechEngine()
    private var tick: Timer?
    private var modelReady = false
    private var session: Session?
    private var player: AVAudioPlayer?
    private var maySaveHistory = true

    private final class Session {
        let target = InsertionTarget()
        let date = Date()
        let model: SpeechModel
        var consumed = 0
        var attempted = 0
        var parts = [String]()
        var pending: Task<Void, Never>?

        init(model: SpeechModel) { self.model = model }
    }

    var busy: Bool { loading || recording || finishing }

    init() {
        let defaults = UserDefaults.standard
        selectedModel = defaults.string(forKey: "model").flatMap(SpeechModel.init(rawValue:)) ?? .mini
        let saved = defaults.object(forKey: "preRoll") as? Int ?? 500
        preRoll = [0, 250, 500, 1_000].contains(saved) ? saved : 500
        saveWav = defaults.bool(forKey: "saveWav")
        do {
            history = try DictateFiles.loadHistory()
        } catch {
            maySaveHistory = false
            status = "Could not read the existing history."
        }
        shortcut.changed = { [weak self] held in
            DispatchQueue.main.async {
                if held { self?.start() } else { self?.finish() }
            }
        }
        refreshPermissions()
        chooseModel(selectedModel)
        tick = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.update() }
        }
    }

    func requestMicrophone() {
        Task {
            _ = await AVCaptureDevice.requestAccess(for: .audio)
            refreshPermissions()
        }
    }

    func requestKeyboard() {
        _ = CGRequestListenEventAccess()
        refreshPermissions()
    }

    func requestInsertion() {
        let prompt = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(prompt)
        refreshPermissions()
    }

    private func refreshPermissions() {
        let allowed = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        if allowed != microphoneAllowed {
            microphoneAllowed = allowed
            do { try recorder.setPreRoll(preRoll, microphoneAllowed: allowed) }
            catch { status = error.localizedDescription }
        }
        shortcut.install()
        if keyboardAllowed != shortcut.installed { keyboardAllowed = shortcut.installed }
        let trusted = AXIsProcessTrusted()
        if insertionAllowed != trusted { insertionAllowed = trusted }
    }

    func chooseModel(_ model: SpeechModel) {
        guard !busy, !modelReady || model != selectedModel else { return }
        loading = true
        status = "Downloading or loading " + model.label + "…"
        Task {
            defer { loading = false }
            do {
                try await engine.prepare(model, in: DictateFiles.models)
                selectedModel = model
                modelReady = true
                UserDefaults.standard.set(model.rawValue, forKey: "model")
                status = "Ready to dictate"
            } catch {
                status = error.localizedDescription
            }
        }
    }

    func setPreRoll(_ milliseconds: Int) {
        guard !recording, !finishing else { return }
        do {
            try recorder.setPreRoll(milliseconds, microphoneAllowed: microphoneAllowed)
            preRoll = milliseconds
            UserDefaults.standard.set(milliseconds, forKey: "preRoll")
        } catch {
            status = error.localizedDescription
        }
    }

    func setSaveWav(_ enabled: Bool) {
        saveWav = enabled
        UserDefaults.standard.set(enabled, forKey: "saveWav")
    }

    func copy(_ entry: HistoryEntry) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(entry.text, forType: .string)
    }

    func play(_ entry: HistoryEntry) {
        guard let file = entry.audioFile, let url = DictateFiles.audioURL(file) else { return }
        do {
            player = try AVAudioPlayer(contentsOf: url)
            player?.play()
        } catch {
            status = error.localizedDescription
        }
    }

    private func start() {
        guard !busy else { return }
        guard microphoneAllowed else { status = "Allow microphone access in Dictate settings."; return }
        guard modelReady else { status = "Load a speech model in Dictate settings."; return }
        do {
            let session = Session(model: selectedModel)
            try recorder.start()
            self.session = session
            recording = true
            status = "Recording"
        } catch {
            status = error.localizedDescription
        }
    }

    private func update() {
        refreshPermissions()
        guard recording, let session else { return }
        if recorder.sampleCount >= 16_000 * 600 { finish(); return }
        guard session.pending == nil else { return }
        let samples = recorder.snapshot(after: session.consumed)
        guard let cut = PhraseBoundary.last(in: samples), cut > session.attempted else { return }
        session.attempted = cut
        let phrase = Array(samples.prefix(cut))
        session.pending = Task {
            defer { session.pending = nil }
            if let text = try? await engine.transcribe(phrase), !text.isEmpty {
                session.parts.append(text)
                session.consumed += cut
                session.attempted = 0
            }
        }
    }

    private func finish() {
        guard recording, let session else { return }
        recording = false
        finishing = true
        status = "Finishing dictation…"
        let interrupted = recorder.interrupted
        let samples = recorder.stop()
        if Date().timeIntervalSince(session.date) < 0.3 {
            finishing = false
            self.session = nil
            status = "Ready to dictate"
            return
        }
        let keepWav = saveWav
        Task {
            defer {
                finishing = false
                self.session = nil
            }
            await session.pending?.value
            do {
                if samples.count > session.consumed {
                    let tail = try await engine.transcribe(Array(samples.dropFirst(session.consumed)))
                    if !tail.isEmpty { session.parts.append(tail) }
                }
                let full = session.parts.joined(separator: " ")
                guard !full.isEmpty else { status = interrupted ? "Microphone stopped responding." : "No speech recognized."; return }
                let id = UUID()
                let audioFile = keepWav ? try? DictateFiles.saveAudio(samples, id: id) : nil
                let entry = HistoryEntry(id: id, date: session.date, model: session.model.label, text: full, audioFile: audioFile)
                history.insert(entry, at: 0)
                let evicted = Array(history.dropFirst(100))
                history = Array(history.prefix(100))
                if maySaveHistory, (try? DictateFiles.saveHistory(history)) != nil {
                    for previous in evicted {
                        if let file = previous.audioFile, let url = DictateFiles.audioURL(file) {
                            try? FileManager.default.removeItem(at: url)
                        }
                    }
                }
                let inserted = session.target?.insert(full) ?? false
                if interrupted {
                    status = "Microphone interrupted. Check the transcript in History."
                } else {
                    status = inserted ? "Ready to dictate" : "Text available in History. Check Accessibility and the focused field."
                }
            } catch {
                status = error.localizedDescription
            }
        }
    }
}

private struct DictateWindow: View {
    @ObservedObject var state: DictateState
    @State private var tab = "Settings"

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Dictate").font(.largeTitle.bold())
            Text(state.status).foregroundStyle(.secondary).textSelection(.enabled)
            TabView(selection: $tab) {
                settings.tabItem { Label("Settings", systemImage: "gear") }.tag("Settings")
                history.tabItem { Label("History", systemImage: "clock") }.tag("History")
            }
        }
        .padding(20)
        .frame(minWidth: 610, minHeight: 480)
    }

    private var settings: some View {
        Form {
            Section("Transcription") {
                Picker("Speech model", selection: Binding(get: { state.selectedModel }, set: state.chooseModel)) {
                    ForEach(SpeechModel.allCases) { Text($0.label).tag($0) }
                }
                .disabled(state.busy)
                Button("Load selected model") { state.chooseModel(state.selectedModel) }
                    .disabled(state.busy)
                LabeledContent("Hold to dictate", value: "Control + Option (⌃⌥)")
            }
            Section("Microphone and history") {
                Picker("Pre-roll", selection: Binding(get: { state.preRoll }, set: state.setPreRoll)) {
                    ForEach([0, 250, 500, 1_000], id: \.self) { Text("\($0) ms").tag($0) }
                }
                .disabled(state.recording || state.finishing)
                Text("At 0 ms the ring buffer is off and the microphone opens only for dictation. When enabled, the mic stays open and only the selected duration of recent audio is held in memory before dictation.")
                    .font(.callout).foregroundStyle(.secondary)
                Toggle("Save WAV files in History", isOn: Binding(get: { state.saveWav }, set: state.setSaveWav))
            }
            Section("macOS permissions") {
                permission("Microphone", enabled: state.microphoneAllowed, request: state.requestMicrophone)
                permission("Input Monitoring", enabled: state.keyboardAllowed, request: state.requestKeyboard)
                permission("Accessibility", enabled: state.insertionAllowed, request: state.requestInsertion)
            }
        }
        .formStyle(.grouped)
    }

    private var history: some View {
        List(state.history) { entry in
            VStack(alignment: .leading, spacing: 6) {
                Text(entry.date.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption).foregroundStyle(.secondary)
                Text(entry.text).textSelection(.enabled)
                HStack {
                    Button("Copy") { state.copy(entry) }
                    if entry.audioFile != nil { Button("Play WAV") { state.play(entry) } }
                }
            }
            .padding(.vertical, 6)
        }
        .overlay {
            if state.history.isEmpty { ContentUnavailableView("No dictations yet", systemImage: "waveform") }
        }
    }

    private func permission(_ name: String, enabled: Bool, request: @escaping () -> Void) -> some View {
        LabeledContent(name) {
            if enabled { Text("Granted").foregroundStyle(.secondary) }
            else { Button("Allow", action: request) }
        }
    }
}
