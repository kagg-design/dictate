import AVFoundation
import Foundation

final class AudioCapture {
    static let sampleRate = 16_000
    private let lock = NSLock()
    private var engine: AVAudioEngine?
    private var generation: UUID?
    private var history = [Float]()
    private var recording = [Float]()
    private var recordingActive = false
    private var preRollSamples = 8_000
    private var receivedAt = Date.distantPast

    func setPreRoll(_ milliseconds: Int, microphoneAllowed: Bool) throws {
        let length = milliseconds * Self.sampleRate / 1_000
        let previous = lock.withLock { preRollSamples }
        if length > 0 && microphoneAllowed {
            try open()
        }
        lock.withLock {
            preRollSamples = length
            if previous != length { history = Array(history.suffix(length)) }
        }
        if length == 0 || !microphoneAllowed { close() }
    }

    func start() throws {
        try open()
        lock.withLock {
            recording = history
            receivedAt = Date()
            recordingActive = true
        }
    }

    func stop() -> [Float] {
        let result = lock.withLock {
            recordingActive = false
            let audio = recording
            recording = []
            return audio
        }
        if lock.withLock({ preRollSamples == 0 }) { close() }
        return result
    }

    func snapshot(after offset: Int) -> [Float] {
        lock.withLock { Array(recording.dropFirst(offset)) }
    }

    var sampleCount: Int { lock.withLock { recording.count } }
    var interrupted: Bool { lock.withLock { Date().timeIntervalSince(receivedAt) > 1.0 } }

    private func open() throws {
        if let engine, engine.isRunning { return }
        close()
        let engine = AVAudioEngine()
        let node = engine.inputNode
        let input = node.outputFormat(forBus: 0)
        guard input.channelCount > 0, input.sampleRate > 0,
              let output = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(Self.sampleRate),
                                         channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: input, to: output) else {
            throw DictateError("No microphone is available.")
        }

        let token = UUID()
        lock.withLock { generation = token }
        node.installTap(onBus: 0, bufferSize: 1024, format: input) { [weak self] buffer, _ in
            let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * output.sampleRate / input.sampleRate)) + 64
            guard let converted = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: capacity) else { return }
            var supplied = false
            var error: NSError?
            let status = converter.convert(to: converted, error: &error) { _, state in
                state.pointee = supplied ? .noDataNow : .haveData
                if supplied { return nil }
                supplied = true
                return buffer
            }
            guard status != .error, let pcm = converted.floatChannelData?.pointee,
                  converted.frameLength > 0 else { return }
            self?.receive(Array(UnsafeBufferPointer(start: pcm, count: Int(converted.frameLength))), token: token)
        }
        do {
            try engine.start()
            self.engine = engine
        } catch {
            lock.withLock { generation = nil }
            node.removeTap(onBus: 0)
            throw error
        }
    }

    private func receive(_ samples: [Float], token: UUID) {
        lock.withLock {
            guard generation == token else { return }
            receivedAt = Date()
            if recordingActive { recording.append(contentsOf: samples) }
            if preRollSamples > 0 {
                history.append(contentsOf: samples)
                if history.count > preRollSamples {
                    history.removeFirst(history.count - preRollSamples)
                }
            }
        }
    }

    private func close() {
        lock.withLock {
            generation = nil
            history.removeAll()
        }
        if let engine {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            self.engine = nil
        }
    }
}

enum PhraseBoundary {
    // Recognize completed phrases while recording. Keep audio after the midpoint
    // of a quiet interval for the next phrase or the final decode.
    static func last(in samples: [Float]) -> Int? {
        let blockSize = 320
        let minimumPhrase = 16_000
        let minimumPause = 7_680
        var speechSinceCut = false
        var quiet = 0
        var latest: Int?
        guard samples.count >= minimumPhrase + minimumPause else { return nil }

        for end in stride(from: blockSize, through: samples.count, by: blockSize) {
            let energy = samples[(end - blockSize)..<end].reduce(Float(0)) { $0 + $1 * $1 }
            if energy / Float(blockSize) > 0.000_064 {
                speechSinceCut = true
                quiet = 0
            } else {
                quiet += blockSize
                if speechSinceCut && quiet == minimumPause {
                    let boundary = end - minimumPause / 2
                    if boundary >= minimumPhrase {
                        latest = boundary
                        speechSinceCut = false
                    }
                }
            }
        }
        return latest
    }
}
