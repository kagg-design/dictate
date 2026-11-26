import AppKit
import ApplicationServices
import AVFoundation
import ServiceManagement
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
        Button("Insert Last Dictation") {
            // Use the editor once the menu is dismissed.
            DispatchQueue.main.async { state.insertLatest() }
        }
        .disabled(state.history.isEmpty || state.recording || state.finishing)
        Button("Copy Last Dictation") { state.copyLatest() }
            .disabled(state.history.isEmpty)
        Divider()
        Text(state.status)
        Button("Quit Dictate") { NSApp.terminate(nil) }
            .keyboardShortcut("q")
    }
}

@MainActor
final class DictateState: ObservableObject {
    enum ShortcutSlot: Equatable { case dictation, insertLast, postprocess }
    static let cueVolumeOptions = [0, 25, 50, 75, 100]

    @Published private(set) var selectedModel: SpeechModel
    @Published private(set) var preRoll: Int
    @Published private(set) var saveWav: Bool
    @Published private(set) var shortcutChoice: ShortcutBinding
    @Published private(set) var insertLastShortcut: ShortcutBinding? = nil
    @Published private(set) var postprocessShortcut: ShortcutBinding? = nil
    @Published private(set) var postprocessModel = ""
    @Published private(set) var capturingShortcut: ShortcutSlot? = nil
    @Published private(set) var shortcutError = ""
    @Published private(set) var toggleMode: Bool
    @Published private(set) var escapeCancels: Bool
    @Published private(set) var livePreview: Bool
    @Published private(set) var recordingSounds: Bool
    @Published private(set) var recordingCueVolume = 100
    @Published private(set) var duckBackgroundAudio = false
    @Published private(set) var useCursorContext: Bool
    @Published private(set) var replacements: TextReplacements
    @Published private(set) var loginEnabled = false
    @Published private(set) var loginRequiresApproval = false
    @Published private(set) var status = "Starting Dictate…"
    @Published private(set) var microphoneAllowed = false
    @Published private(set) var keyboardAllowed = false
    @Published private(set) var insertionAllowed = false
    @Published private(set) var loading = false
    @Published private(set) var recording = false
    @Published private(set) var finishing = false
    @Published private(set) var history: [HistoryEntry] = []
    @Published private(set) var wavRetentionDays = 0

    private let recorder = AudioCapture()
    private let shortcut = DictationShortcut()
    private let engine = SpeechEngine()
    private var tick: Timer?
    private var modelReady = false
    private var session: Session?
    private var player: AVAudioPlayer?
    private var maySaveHistory = true
    private var preview: DictationPreview?
    private var insertingLast = false
    private lazy var startSound = NSSound(named: "Tink")
    private lazy var stopSound = NSSound(named: "Pop")
    private let backgroundAudio = BackgroundAudio()
    private var audioDucking: Task<Void, Never>?

    private final class Session {
        let target = InsertionTarget()
        let date = Date()
        let model: SpeechModel
        let postprocessModel: String
        var consumed = 0
        var attempted = 0
        var parts = [String]()
        var pending: Task<Void, Never>?

        init(model: SpeechModel, postprocessModel: String) {
            self.model = model
            self.postprocessModel = postprocessModel
        }
    }

    var busy: Bool { loading || recording || finishing }

    init() {
        let defaults = UserDefaults.standard
        selectedModel = defaults.string(forKey: "model").flatMap(SpeechModel.init(rawValue:)) ?? .mini
        let saved = defaults.object(forKey: "preRoll") as? Int ?? 500
        preRoll = [0, 250, 500, 1_000].contains(saved) ? saved : 500
        saveWav = defaults.bool(forKey: "saveWav")
        let savedDictation = ShortcutBinding.decode(defaults.data(forKey: "dictationShortcutBinding")) ??
            ShortcutBinding.legacyDictation(defaults.string(forKey: "shortcut"))
        let dictation = savedDictation.conflicts(with: .copyLast) ? .dictationDefault : savedDictation
        shortcutChoice = dictation
        if !defaults.bool(forKey: "insertLastShortcutDisabled") {
            let savedInsert = ShortcutBinding.decode(defaults.data(forKey: "insertLastShortcutBinding")) ??
                .insertLastDefault
            if !savedInsert.conflicts(with: .copyLast) && !savedInsert.conflicts(with: dictation) {
                insertLastShortcut = savedInsert
            }
        }
        toggleMode = defaults.bool(forKey: "toggleMode")
        postprocessModel = defaults.string(forKey: "postprocessModel") ?? ""
        if let saved = ShortcutBinding.decode(defaults.data(forKey: "postprocessShortcutBinding")), saved.key != nil,
           !saved.conflicts(with: dictation), !saved.conflicts(with: .copyLast),
           insertLastShortcut.map({ !saved.conflicts(with: $0) }) ?? true {
            postprocessShortcut = saved
        }
        escapeCancels = defaults.object(forKey: "escapeCancels") as? Bool ?? true
        livePreview = defaults.bool(forKey: "livePreview")
        recordingSounds = defaults.object(forKey: "recordingSounds") as? Bool ?? true
        let cueVolume = defaults.object(forKey: "recordingCueVolume") as? Int ?? 100
        if Self.cueVolumeOptions.contains(cueVolume) { recordingCueVolume = cueVolume }
        duckBackgroundAudio = defaults.bool(forKey: "duckBackgroundAudio")
        useCursorContext = defaults.object(forKey: "useCursorContext") as? Bool ?? true
        replacements = TextReplacements(
            defaults.dictionary(forKey: "textReplacements") as? [String: String] ?? [:],
            fuzzySources: Set(defaults.stringArray(forKey: "fuzzyReplacements") ?? [])
        )
        do {
            let days = defaults.integer(forKey: "wavRetentionDays")
            if HistoryEntry.retentionDays.contains(days) { wavRetentionDays = days }
            history = try DictateFiles.loadHistory()
        } catch {
            maySaveHistory = false
            status = "Could not read the existing history."
        }
        let retained = HistoryEntry.retained(history, audioDays: wavRetentionDays)
        if retained.count != history.count || retained.map(\.audioFile) != history.map(\.audioFile) {
            do { try storeHistory(retained) }
            catch { status = "Could not clean up older recordings." }
        }
        shortcut.choice = shortcutChoice
        shortcut.insertLast = insertLastShortcut
        shortcut.postprocess = postprocessShortcut
        shortcut.toggle = toggleMode
        shortcut.escapeCancels = escapeCancels
        shortcut.changed = { [weak self] action in
            DispatchQueue.main.async {
                switch action {
                case .start: self?.start()
                case .startWithPostprocessing: self?.start(postprocess: true)
                case .finish: self?.finish()
                case .cancel: self?.finish(insert: false)
                case .copy: self?.copyLatest()
                case .insertLast: self?.insertLatest()
                }
            }
        }
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.restoreBackgroundAudio() }
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
        let trusted = AXIsProcessTrusted()
        shortcut.install(filterKeys: trusted)
        if keyboardAllowed != shortcut.installed { keyboardAllowed = shortcut.installed }
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

    func captureShortcut(_ slot: ShortcutSlot) {
        if capturingShortcut != nil {
            cancelShortcutCapture()
            return
        }
        guard shortcut.beginCapture({ [weak self] binding, error in
            DispatchQueue.main.async {
                guard let self, self.capturingShortcut == slot else { return }
                self.capturingShortcut = nil
                if let binding { self.saveShortcut(binding, slot: slot) }
                else { self.shortcutError = error }
            }
        }) else {
            shortcutError = "Allow Input Monitoring to record a shortcut."
            return
        }
        shortcutError = ""
        capturingShortcut = slot
    }

    func cancelShortcutCapture() {
        shortcut.cancelCapture()
        capturingShortcut = nil
    }

    func disableInsertLastShortcut() {
        insertLastShortcut = nil
        shortcut.insertLast = nil
        shortcutError = ""
        UserDefaults.standard.set(true, forKey: "insertLastShortcutDisabled")
    }

    func setPostprocessModel(_ value: String) {
        postprocessModel = value.trimmingCharacters(in: .whitespacesAndNewlines)
        UserDefaults.standard.set(postprocessModel, forKey: "postprocessModel")
    }

    func disablePostprocessShortcut() {
        postprocessShortcut = nil
        shortcut.postprocess = nil
        shortcutError = ""
        UserDefaults.standard.removeObject(forKey: "postprocessShortcutBinding")
    }

    private func saveShortcut(_ binding: ShortcutBinding, slot: ShortcutSlot) {
        guard !binding.conflicts(with: .copyLast) else {
            shortcutError = "Control + Command + V is assigned to Copy Last Dictation."
            return
        }
        let bindings: [(ShortcutSlot, ShortcutBinding?)] = [
            (.dictation, shortcutChoice), (.insertLast, insertLastShortcut), (.postprocess, postprocessShortcut),
        ]
        guard !bindings.contains(where: { key, other in
            key != slot && (other.map { binding.conflicts(with: $0) } ?? false)
        }) else {
            shortcutError = "Choose distinct shortcuts for dictation, insertion and post-processing."
            return
        }
        if slot == .postprocess && binding.key == nil {
            shortcutError = "Include a non-modifier key in the post-processing shortcut."
            return
        }
        guard let data = try? JSONEncoder().encode(binding) else {
            shortcutError = "Could not save the shortcut."
            return
        }
        switch slot {
        case .dictation:
            UserDefaults.standard.set(data, forKey: "dictationShortcutBinding")
            shortcutChoice = binding
            shortcut.choice = binding
        case .insertLast:
            UserDefaults.standard.set(data, forKey: "insertLastShortcutBinding")
            UserDefaults.standard.set(false, forKey: "insertLastShortcutDisabled")
            insertLastShortcut = binding
            shortcut.insertLast = binding
        case .postprocess:
            UserDefaults.standard.set(data, forKey: "postprocessShortcutBinding")
            postprocessShortcut = binding
            shortcut.postprocess = binding
        }
    }

    func setToggleMode(_ enabled: Bool) {
        toggleMode = enabled
        shortcut.toggle = enabled
        UserDefaults.standard.set(enabled, forKey: "toggleMode")
    }

    func setEscapeCancels(_ enabled: Bool) {
        escapeCancels = enabled
        shortcut.escapeCancels = enabled
        UserDefaults.standard.set(enabled, forKey: "escapeCancels")
    }

    func setLivePreview(_ enabled: Bool) {
        livePreview = enabled
        if !enabled && recording { preview?.show("") }
        UserDefaults.standard.set(enabled, forKey: "livePreview")
    }

    func setRecordingSounds(_ enabled: Bool) {
        recordingSounds = enabled
        UserDefaults.standard.set(enabled, forKey: "recordingSounds")
    }

    func setRecordingCueVolume(_ volume: Int) {
        recordingCueVolume = volume
        UserDefaults.standard.set(volume, forKey: "recordingCueVolume")
    }

    func setDuckBackgroundAudio(_ enabled: Bool) {
        duckBackgroundAudio = enabled
        UserDefaults.standard.set(enabled, forKey: "duckBackgroundAudio")
    }

    func setUseCursorContext(_ enabled: Bool) {
        useCursorContext = enabled
        UserDefaults.standard.set(enabled, forKey: "useCursorContext")
    }

    func refreshLoginStatus() {
        let current = SMAppService.mainApp.status
        loginEnabled = current == .enabled || current == .requiresApproval
        loginRequiresApproval = current == .requiresApproval
    }

    func setStartAtLogin(_ enabled: Bool) {
        defer { refreshLoginStatus() }
        do {
            if enabled { try SMAppService.mainApp.register() }
            else { try SMAppService.mainApp.unregister() }
        } catch {
            status = "Could not change login settings. " + error.localizedDescription
        }
    }

    func copyLatest() {
        if let last = history.first { copy(last) }
    }

    func insertLatest() {
        guard !recording, !finishing, !insertingLast, let last = history.first else { return }
        guard let target = InsertionTarget() else {
            status = "Allow Accessibility and focus a text field to insert."
            return
        }
        insertingLast = true
        Task {
            defer { insertingLast = false }
            let deadline = ContinuousClock.now + .seconds(3)
            while !CGEventSource.flagsState(.combinedSessionState)
                .intersection(ShortcutBinding.modifierMask).isEmpty {
                guard ContinuousClock.now < deadline else { return }
                try? await Task.sleep(for: .milliseconds(15))
            }
            guard !recording, !finishing else { return }
            if !target.insert(last.text, useCursorContext: useCursorContext) {
                status = "Could not insert the last dictation. Check Accessibility and the focused field."
            }
        }
    }

    func saveReplacement(_ draft: ReplacementDraft) throws {
        let source = draft.source.trimmingCharacters(in: .whitespacesAndNewlines)
        let value = draft.value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !source.isEmpty, !value.isEmpty else {
            throw DictateError("Enter both the recognized text and its replacement.")
        }
        guard !draft.fuzzy || TextReplacements.supportsFuzzySpelling(value) else {
            throw DictateError("Similar-spelling corrections support words made of letters.")
        }
        var rules = replacements.saved
        if let previous = draft.previous { rules.removeValue(forKey: previous) }
        guard !rules.keys.contains(where: { $0.caseInsensitiveCompare(source) == .orderedSame }) else {
            throw DictateError("A replacement for this text already exists.")
        }
        rules[source] = value
        var fuzzySources = replacements.fuzzySources
        if let previous = draft.previous { fuzzySources.remove(previous) }
        if draft.fuzzy { fuzzySources.insert(source) }
        storeReplacements(rules, fuzzySources: fuzzySources)
    }

    func removeReplacement(_ source: String) {
        var rules = replacements.saved
        rules.removeValue(forKey: source)
        storeReplacements(rules, fuzzySources: replacements.fuzzySources.subtracting([source]))
    }

    private func storeReplacements(_ rules: [String: String], fuzzySources: Set<String>) {
        UserDefaults.standard.set(rules, forKey: "textReplacements")
        UserDefaults.standard.set(fuzzySources.sorted(), forKey: "fuzzyReplacements")
        replacements = TextReplacements(rules, fuzzySources: fuzzySources)
    }

    func copy(_ entry: HistoryEntry) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(entry.text, forType: .string)
    }

    func setPinned(_ entry: HistoryEntry, _ pinned: Bool) {
        guard let index = history.firstIndex(where: { $0.id == entry.id }) else { return }
        var updated = history
        updated[index].pinned = pinned ? true : nil
        do {
            try storeHistory(HistoryEntry.retained(updated, audioDays: wavRetentionDays))
        } catch {
            status = "Could not update this pin."
        }
    }

    func setWavRetention(_ days: Int) {
        do {
            try storeHistory(HistoryEntry.retained(history, audioDays: days))
            wavRetentionDays = days
            UserDefaults.standard.set(days, forKey: "wavRetentionDays")
        } catch {
            status = "Could not clean up older recordings."
        }
    }

    private func storeHistory(_ updated: [HistoryEntry], added: HistoryEntry? = nil) throws {
        guard maySaveHistory else { throw DictateError("Could not read the existing history.") }
        try DictateFiles.saveHistory(updated)
        let remainingAudio = Set(updated.compactMap(\.audioFile))
        let previousAudio = history.compactMap(\.audioFile) + [added?.audioFile].compactMap { $0 }
        for file in previousAudio where !remainingAudio.contains(file) {
            if let url = DictateFiles.audioURL(file) { try? FileManager.default.removeItem(at: url) }
        }
        history = updated
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

    private func start(postprocess: Bool = false) {
        guard !busy else { shortcut.disarm(); return }
        if postprocess && postprocessModel.isEmpty {
            shortcut.disarm()
            status = "Select a local Ollama model in Dictate Settings."
            return
        }
        guard microphoneAllowed else {
            shortcut.disarm()
            status = "Allow microphone access in Dictate settings."
            return
        }
        guard modelReady else {
            shortcut.disarm()
            status = "Load a speech model in Dictate settings."
            return
        }
        do {
            let session = Session(model: selectedModel, postprocessModel: postprocess ? postprocessModel : "")
            try recorder.start()
            self.session = session
            recording = true
            status = "Recording"
            if preview == nil { preview = DictationPreview() }
            preview?.showRecording()
            if recordingSounds { playRecordingSound(startSound, interrupting: stopSound) }
            lowerBackgroundAudio()
        } catch {
            shortcut.disarm()
            status = error.localizedDescription
        }
    }

    private func update() {
        refreshPermissions()
        guard recording, let session else { return }
        if recorder.sampleCount >= 16_000 * 600 { shortcut.disarm(); finish(); return }
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
                if self.recording && self.livePreview {
                    self.preview?.show(String(session.parts.joined(separator: " ").suffix(180)))
                }
            }
        }
    }

    private func finish(insert: Bool = true) {
        guard recording, let session else { return }
        recording = false
        preview?.hide()
        finishing = true
        status = "Finishing dictation…"
        let interrupted = recorder.interrupted
        let samples = recorder.stop()
        restoreBackgroundAudio()
        if recordingSounds { playRecordingSound(stopSound, interrupting: startSound) }
        if Date().timeIntervalSince(session.date) < 0.3 {
            finishing = false
            self.session = nil
            status = "Ready to dictate"
            return
        }
        let keepWav = saveWav
        let feedback = Task { [weak self] in
            try? await Task.sleep(for: .milliseconds(400))
            guard !Task.isCancelled, let self, self.finishing else { return }
            self.preview?.showProcessing()
        }
        let target = insert ? session.target?.resolved() : nil
        Task {
            defer {
                feedback.cancel()
                preview?.hide()
                finishing = false
                self.session = nil
            }
            await session.pending?.value
            do {
                if samples.count > session.consumed {
                    let tail = try await engine.transcribe(Array(samples.dropFirst(session.consumed)))
                    if !tail.isEmpty { session.parts.append(tail) }
                }
                var full = replacements.apply(session.parts.joined(separator: " "))
                guard !full.isEmpty else { status = interrupted ? "Microphone stopped responding." : "No speech recognized."; return }
                var polishFailed = false
                if insert, !session.postprocessModel.isEmpty {
                    do { full = try await Postprocess.polish(full, model: session.postprocessModel) }
                    catch { polishFailed = true }
                }
                let id = UUID()
                let audioFile = keepWav ? try? DictateFiles.saveAudio(samples, id: id) : nil
                let entry = HistoryEntry(id: id, date: session.date, model: session.model.label, text: full, audioFile: audioFile)
                let updated = [entry] + history
                let retained = HistoryEntry.retained(updated, audioDays: wavRetentionDays)
                do {
                    try storeHistory(retained, added: entry)
                } catch {
                    // Keep the new transcript recoverable in memory after a failed write.
                    history = HistoryEntry.retained(updated, audioDays: 0)
                }
                let inserted = target?.insert(full, useCursorContext: useCursorContext) ?? false
                if !insert {
                    status = "Insertion canceled. Text available in History."
                } else if interrupted {
                    status = "Microphone interrupted. Check the transcript in History."
                } else if polishFailed {
                    status = inserted ? "Local editing failed. Inserted the recognized text."
                        : "Local editing failed. The recognized text is in History."
                } else {
                    status = inserted ? "Ready to dictate" : "Text available in History. Check Accessibility and the focused field."
                }
            } catch {
                status = error.localizedDescription
            }
        }
    }

    private func playRecordingSound(_ sound: NSSound?, interrupting previous: NSSound?) {
        previous?.stop()
        guard recordingCueVolume > 0 else { return }
        sound?.volume = 0.35 * Float(recordingCueVolume) / 100
        sound?.play()
    }

    private func lowerBackgroundAudio() {
        guard duckBackgroundAudio else { return }
        let cueDuration = recordingSounds && recordingCueVolume > 0 ? startSound?.duration ?? 0 : 0
        audioDucking = Task { [weak self] in
            if cueDuration > 0 { try? await Task.sleep(for: .seconds(cueDuration)) }
            guard !Task.isCancelled, let self, self.recording else { return }
            self.backgroundAudio.lower()
        }
    }

    private func restoreBackgroundAudio() {
        audioDucking?.cancel()
        audioDucking = nil
        backgroundAudio.restore()
    }
}

private struct DictateWindow: View {
    @ObservedObject var state: DictateState
    @State private var tab = "Settings"
    @State private var replacementDraft: ReplacementDraft?

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
        .sheet(item: $replacementDraft) { draft in
            ReplacementEditor(draft: draft, save: state.saveReplacement)
        }
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
            }
            Section("Dictation controls") {
                LabeledContent("Dictation shortcut") {
                    shortcutButton(.dictation, label: state.shortcutChoice.label)
                }
                LabeledContent("Insert last shortcut") {
                    if let binding = state.insertLastShortcut {
                        Button("Turn off", action: state.disableInsertLastShortcut)
                            .disabled(state.capturingShortcut != nil)
                        shortcutButton(.insertLast, label: binding.label)
                    } else {
                        shortcutButton(.insertLast, label: "Off")
                    }
                }
                if !state.shortcutError.isEmpty {
                    Text(state.shortcutError).font(.callout).foregroundStyle(.red)
                }
                Toggle("Press to start / stop", isOn: Binding(get: { state.toggleMode }, set: state.setToggleMode))
                Toggle("Esc cancels and keeps text in History",
                       isOn: Binding(get: { state.escapeCancels }, set: state.setEscapeCancels))
                Toggle("Show live transcript while dictating",
                       isOn: Binding(get: { state.livePreview }, set: state.setLivePreview))
                Toggle("Play recording sounds",
                       isOn: Binding(get: { state.recordingSounds }, set: state.setRecordingSounds))
                Picker("Recording cue volume",
                       selection: Binding(get: { state.recordingCueVolume }, set: state.setRecordingCueVolume)) {
                    ForEach(DictateState.cueVolumeOptions, id: \.self) { volume in
                        Text("\(volume)%").tag(volume)
                    }
                }.disabled(!state.recordingSounds)
                Toggle("Lower background audio while recording",
                       isOn: Binding(get: { state.duckBackgroundAudio }, set: state.setDuckBackgroundAudio))
                Toggle("Use text around the cursor",
                       isOn: Binding(get: { state.useCursorContext }, set: state.setUseCursorContext))
            }
            .disabled(state.busy)
            Section("Local post-processing") {
                PostprocessSettings(state: state)
                LabeledContent("Dictate with editing") {
                    if state.postprocessShortcut != nil {
                        Button("Turn off", action: state.disablePostprocessShortcut)
                            .disabled(state.capturingShortcut != nil)
                    }
                    shortcutButton(.postprocess, label: state.postprocessShortcut?.label ?? "Off")
                }
                Text("Press the editing shortcut once to start and again to finish.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            .disabled(state.busy)
            Section("Microphone and history") {
                Picker("Pre-roll", selection: Binding(get: { state.preRoll }, set: state.setPreRoll)) {
                    ForEach([0, 250, 500, 1_000], id: \.self) { Text("\($0) ms").tag($0) }
                }
                .disabled(state.recording || state.finishing)
                Text("At 0 ms the ring buffer is off and the microphone opens only for dictation. When enabled, the mic stays open and only the selected duration of recent audio is held in memory before dictation.")
                    .font(.callout).foregroundStyle(.secondary)
                Toggle("Save WAV files in History", isOn: Binding(get: { state.saveWav }, set: state.setSaveWav))
                Picker("Delete unpinned WAV audio after",
                       selection: Binding(get: { state.wavRetentionDays }, set: state.setWavRetention)) {
                    ForEach(HistoryEntry.retentionDays, id: \.self) { days in
                        Text(days == 0 ? "Keep with transcript" : "\(days) days").tag(days)
                    }
                }
                Toggle("Start Dictate at login", isOn: Binding(get: { state.loginEnabled }, set: state.setStartAtLogin))
                if state.loginRequiresApproval {
                    Text("Allow Dictate under System Settings → General → Login Items.")
                        .font(.callout).foregroundStyle(.secondary)
                }
            }
            Section("macOS permissions") {
                permission("Microphone", enabled: state.microphoneAllowed, request: state.requestMicrophone)
                permission("Input Monitoring", enabled: state.keyboardAllowed, request: state.requestKeyboard)
                permission("Accessibility", enabled: state.insertionAllowed, request: state.requestInsertion)
            }
            Section("Text replacements") {
                Button("Add replacement…") { replacementDraft = ReplacementDraft() }
                ForEach(state.replacements.rules) { rule in
                    LabeledContent {
                        HStack {
                            Button("Edit…") {
                                replacementDraft = ReplacementDraft(
                                    source: rule.source, value: rule.replacement, previous: rule.source,
                                    fuzzy: state.replacements.fuzzySources.contains(rule.source)
                                )
                            }
                            Button("Remove") { state.removeReplacement(rule.source) }
                        }.buttonStyle(.borderless)
                    } label: {
                        Text(rule.source)
                        Text(rule.replacement)
                    }
                }
            }
        }
        .formStyle(.grouped)
        .onAppear { state.refreshLoginStatus() }
        .onDisappear { state.cancelShortcutCapture() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
            state.cancelShortcutCapture()
        }
    }

    private func shortcutButton(_ slot: DictateState.ShortcutSlot, label: String) -> some View {
        Button(state.capturingShortcut == slot ? "Press 2 or 3 keys…" : label) {
            state.captureShortcut(slot)
        }
        .disabled(state.capturingShortcut != nil && state.capturingShortcut != slot)
        .help("Click, then hold two or three keys together. Press Esc to cancel.")
    }

    private var history: some View {
        List(state.history) { entry in
            HistoryRow(entry: entry, state: state) { selected in
                let existing = state.replacements.rules.first {
                    $0.source.caseInsensitiveCompare(selected) == .orderedSame
                }
                replacementDraft = ReplacementDraft(
                    source: existing?.source ?? selected, value: existing?.replacement ?? "", previous: existing?.source,
                    fuzzy: state.replacements.fuzzySources.contains(existing?.source ?? "")
                )
            }
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

private struct PostprocessSettings: View {
    @ObservedObject var state: DictateState
    @State private var models = [String]()
    @State private var loading = false
    @State private var status = ""

    var body: some View {
        LabeledContent("Ollama model") {
            Picker("Ollama model", selection: Binding(get: { state.postprocessModel }, set: state.setPostprocessModel)) {
                Text("Select a model").tag("")
                if !state.postprocessModel.isEmpty, !models.contains(state.postprocessModel) {
                    Text(state.postprocessModel).tag(state.postprocessModel)
                }
                ForEach(models, id: \.self) { Text($0).tag($0) }
            }
            .labelsHidden()
            Button("Refresh") { Task { await refresh() } }.disabled(loading)
            if !status.isEmpty { Text(status).foregroundStyle(.secondary) }
        }
        .task { await refresh() }
    }

    private func refresh() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            models = try await Postprocess.installedModels()
            status = models.isEmpty ? "No local models" : ""
        } catch {
            models = []
            status = "Ollama unavailable"
        }
    }
}

private struct HistoryRow: View {
    let entry: HistoryEntry
    let state: DictateState
    let addReplacement: (String) -> Void
    @State private var selection = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if entry.pinned == true { Image(systemName: "star.fill").accessibilityLabel("Pinned") }
                Text(entry.date.formatted(date: .abbreviated, time: .shortened))
            }
            .font(.caption).foregroundStyle(.secondary)
            HistoryTranscript(text: entry.text, selection: $selection)
            HStack {
                Button("Copy") { state.copy(entry) }
                if entry.audioFile != nil { Button("Play WAV") { state.play(entry) } }
                Button("Add replacement…") { addReplacement(selection) }.disabled(selection.isEmpty)
                Button(entry.pinned == true ? "Unpin" : "Pin") { state.setPinned(entry, entry.pinned != true) }
            }
            .buttonStyle(.borderless)
        }
        .padding(.vertical, 6)
    }
}
