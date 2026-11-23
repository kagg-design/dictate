import CoreML
import CryptoKit
import FluidAudio
import Foundation

enum SpeechModel: String, CaseIterable, Identifiable, Sendable {
    case whisper
    case mini

    var id: String { rawValue }
    var label: String {
        switch self {
        case .whisper: return "Whisper Large v3 Turbo · Metal"
        case .mini: return "Fluid Speech Mini · Core ML"
        }
    }
}

struct DictateError: LocalizedError {
    let errorDescription: String?

    init(_ message: String) { errorDescription = message }
}

actor SpeechEngine {
    private var whisper: WhisperWorker?
    private var mini: AsrManager?
    private(set) var activeModel: SpeechModel?

    func prepare(_ model: SpeechModel, in models: URL) async throws {
        if activeModel == model { return }

        switch model {
        case .whisper:
            let weights = try await ModelFiles.whisper(in: models)
            guard let helper = Bundle.main.url(forAuxiliaryExecutable: "DictateWhisper") else {
                throw DictateError("The Whisper executable is missing from Dictate.app.")
            }
            whisper = try WhisperWorker(executable: helper, model: weights)
            mini = nil
        case .mini:
            let directory = try await ModelFiles.mini(in: models)
            let asrModels = try await AsrModels.loadLocalOnly(from: directory, version: .fluidParakeetMini)
            let manager = AsrManager()
            try await manager.initialize(models: asrModels)
            mini = manager
            whisper = nil
        }
        activeModel = model
    }

    func transcribe(_ audio: [Float]) async throws -> String {
        guard audio.count >= 1_600 else { return "" }
        let padded = audio + [Float](repeating: 0, count: 4_000)
        let text: String
        if let whisper {
            text = try whisper.transcribe(padded)
        } else if let mini {
            try await mini.resetDecoderState(for: .microphone)
            text = try await mini.transcribe(padded, source: .microphone).text
        } else {
            throw DictateError("Choose a speech model first.")
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func unload() {
        whisper = nil
        mini = nil
        activeModel = nil
    }
}

private enum ModelFiles {
    static func whisper(in cache: URL) async throws -> URL {
        let filename = "ggml-large-v3-turbo-q5_0.bin"
        let destination = cache.appendingPathComponent(filename)
        if FileManager.default.fileExists(atPath: destination.path) { return destination }
        let remote = URL(string: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + filename)!
        return try await download(
            remote, to: destination, sha256: "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
        )
    }

    static func mini(in cache: URL) async throws -> URL {
        let destination = cache.appendingPathComponent("fluid-mini-1.1.1")
        if AsrModels.localModelsExist(at: destination, version: .fluidParakeetMini) { return destination }
        let fm = FileManager.default
        let staging = cache.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try fm.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: staging) }
        let archive = try await download(
            URL(string: "https://models.fluidvoice.com/parakeet/fluid-mini/1.1.1/fluid-parakeet-mini-coreml.tar")!,
            to: staging.appendingPathComponent("mini.tar"),
            sha256: "7f811554cc670ded812937502f928ae6aad946bbf6e4c2eecd6fcffbe76f4d16"
        )

        // The pinned archive contains a single, verified model directory.
        let unpack = Process()
        unpack.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
        unpack.arguments = ["-xf", archive.path, "-C", staging.path]
        try unpack.run()
        unpack.waitUntilExit()
        let folder = staging.appendingPathComponent("fluid-parakeet-mini-coreml", isDirectory: true)
        guard unpack.terminationStatus == 0,
              AsrModels.localModelsExist(at: folder, version: .fluidParakeetMini) else {
            throw DictateError("The Fluid Speech Mini archive could not be unpacked.")
        }
        if fm.fileExists(atPath: destination.path) { try fm.removeItem(at: destination) }
        try fm.moveItem(at: folder, to: destination)
        return destination
    }

    private static func download(_ url: URL, to destination: URL, sha256: String) async throws -> URL {
        let fm = FileManager.default
        try fm.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        let (file, response) = try await URLSession.shared.download(from: url)
        defer { try? fm.removeItem(at: file) }
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw DictateError("The model server could not provide the download.")
        }
        let stream = try FileHandle(forReadingFrom: file)
        defer { try? stream.close() }
        var hash = SHA256()
        while let data = try stream.read(upToCount: 1_048_576), !data.isEmpty {
            hash.update(data: data)
        }
        let actual = hash.finalize().map { String(format: "%02x", $0) }.joined()
        guard actual == sha256 else { throw DictateError("The model download did not match its SHA-256.") }
        try fm.moveItem(at: file, to: destination)
        return destination
    }
}

private final class WhisperWorker {
    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()

    init(executable: URL, model: URL) throws {
        process.executableURL = executable
        process.arguments = [model.path]
        process.standardInput = input
        process.standardOutput = output
        process.standardError = FileHandle.nullDevice
        try process.run()
        _ = try response()
    }

    deinit {
        try? input.fileHandleForWriting.close()
        if process.isRunning { process.terminate() }
    }

    func transcribe(_ audio: [Float]) throws -> String {
        var count = UInt32(audio.count).littleEndian
        try input.fileHandleForWriting.write(contentsOf: Data(bytes: &count, count: MemoryLayout<UInt32>.size))
        try input.fileHandleForWriting.write(contentsOf: audio.withUnsafeBytes { Data($0) })
        return try response()
    }

    private func response() throws -> String {
        let header = try read(4)
        let value = header.withUnsafeBytes { UInt32(littleEndian: $0.loadUnaligned(as: UInt32.self)) }
        let count = Int(value & 0x7fff_ffff)
        guard count <= 1_048_576 else { throw DictateError("The Whisper worker returned an invalid response.") }
        let text = String(decoding: try read(count), as: UTF8.self)
        if value & 0x8000_0000 != 0 { throw DictateError(text) }
        return text
    }

    private func read(_ count: Int) throws -> Data {
        var result = Data()
        while result.count < count {
            guard let next = try output.fileHandleForReading.read(upToCount: count - result.count),
                  !next.isEmpty else { throw DictateError("The Whisper worker stopped. Reload the model to try again.") }
            result.append(next)
        }
        return result
    }
}
