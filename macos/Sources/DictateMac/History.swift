import Foundation

struct HistoryEntry: Identifiable, Codable {
    let id: UUID
    let date: Date
    let model: String
    let text: String
    var audioFile: String?
    var pinned: Bool? = nil

    static let retentionDays = [0, 7, 30, 90]

    static func retained(_ entries: [Self], audioDays: Int, now: Date = Date()) -> [Self] {
        let cutoff = now.addingTimeInterval(-Double(audioDays) * 86_400)
        var remaining = 100
        return entries.compactMap { entry in
            if entry.pinned == true { return entry }
            guard remaining > 0 else { return nil }
            remaining -= 1
            var retained = entry
            if audioDays > 0, entry.date < cutoff { retained.audioFile = nil }
            return retained
        }
    }
}

enum DictateFiles {
    static let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("Dictate", isDirectory: true)
    static let models = root.appendingPathComponent("Models", isDirectory: true)
    private static let history = root.appendingPathComponent("history.json")

    static func loadHistory() throws -> [HistoryEntry] {
        guard FileManager.default.fileExists(atPath: history.path) else { return [] }
        return try JSONDecoder().decode([HistoryEntry].self, from: Data(contentsOf: history))
    }

    static func saveHistory(_ entries: [HistoryEntry]) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try JSONEncoder().encode(entries).write(to: history, options: .atomic)
    }

    static func audioURL(_ name: String) -> URL? {
        guard name.hasSuffix(".wav"), UUID(uuidString: String(name.dropLast(4))) != nil else { return nil }
        return root.appendingPathComponent("Audio", isDirectory: true).appendingPathComponent(name)
    }

    static func saveAudio(_ samples: [Float], id: UUID) throws -> String {
        let name = id.uuidString + ".wav"
        guard let url = audioURL(name) else { throw DictateError("Could not name the WAV file.") }
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var data = Data()
        let bytes = UInt32(samples.count * 2)
        data.append(Data("RIFF".utf8))
        data.appendLE(bytes + 36)
        data.append(Data("WAVEfmt ".utf8))
        data.appendLE(UInt32(16))
        data.appendLE(UInt16(1))
        data.appendLE(UInt16(1))
        data.appendLE(UInt32(16_000))
        data.appendLE(UInt32(32_000))
        data.appendLE(UInt16(2))
        data.appendLE(UInt16(16))
        data.append(Data("data".utf8))
        data.appendLE(bytes)
        let pcm: [Int16] = samples.map { Int16((max(-1, min(1, $0)) * 32767).rounded()).littleEndian }
        data.append(pcm.withUnsafeBytes { Data($0) })
        try data.write(to: url, options: .atomic)
        return name
    }
}

private extension Data {
    mutating func appendLE<T: FixedWidthInteger>(_ value: T) {
        var value = value.littleEndian
        Swift.withUnsafeBytes(of: &value) { append(contentsOf: $0) }
    }
}
