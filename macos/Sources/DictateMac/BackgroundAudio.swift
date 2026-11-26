import AudioToolbox
import CoreAudio
import Foundation

final class BackgroundAudio {
    private var saved: (device: AudioDeviceID, before: Float32, lowered: Float32)?

    func lower() {
        guard saved == nil else { return }
        var output = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var device = AudioDeviceID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        guard AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &output, 0, nil, &size, &device) == noErr,
              device != kAudioObjectUnknown, let before = volume(device), before > 0.001
        else { return }
        let lowered = before * 0.2
        if setVolume(lowered, device) {
            saved = (device, before, lowered)
        }
    }

    func restore() {
        guard let saved else { return }
        self.saved = nil
        if let current = volume(saved.device), abs(current - saved.lowered) < 0.005 {
            _ = setVolume(saved.before, saved.device)
        }
    }

    private var mainVolume: AudioObjectPropertyAddress {
        AudioObjectPropertyAddress(
            mSelector: kAudioHardwareServiceDeviceProperty_VirtualMainVolume,
            mScope: kAudioDevicePropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain
        )
    }

    private func volume(_ device: AudioDeviceID) -> Float32? {
        var address = mainVolume
        var value: Float32 = 0
        var size = UInt32(MemoryLayout<Float32>.size)
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &value) == noErr else { return nil }
        return value
    }

    private func setVolume(_ value: Float32, _ device: AudioDeviceID) -> Bool {
        var address = mainVolume
        var value = value
        return AudioObjectSetPropertyData(device, &address, 0, nil, UInt32(MemoryLayout<Float32>.size), &value) == noErr
    }
}
