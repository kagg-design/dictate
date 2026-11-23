import AppKit
import ApplicationServices

final class DictationShortcut {
    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var held = false
    var changed: ((Bool) -> Void)?

    var installed: Bool { tap != nil }

    func install() {
        guard tap == nil, CGPreflightListenEventAccess() else { return }
        let eventMask = CGEventMask(1) << CGEventType.flagsChanged.rawValue
        guard let newTap = CGEvent.tapCreate(
            tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
            eventsOfInterest: eventMask, callback: { _, type, event, context in
                if let context {
                    let shortcut = Unmanaged<DictationShortcut>.fromOpaque(context).takeUnretainedValue()
                    shortcut.receive(type, event: event)
                }
                return Unmanaged.passUnretained(event)
            }, userInfo: Unmanaged.passUnretained(self).toOpaque()
        ) else { return }

        tap = newTap
        source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, newTap, 0)
        if let source { CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes) }
        CGEvent.tapEnable(tap: newTap, enable: true)
    }

    private func receive(_ type: CGEventType, event: CGEvent) {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if held {
                held = false
                changed?(false)
            }
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return
        }
        let modifiers: CGEventFlags = [.maskControl, .maskAlternate]
        let extras: CGEventFlags = [.maskCommand, .maskShift]
        let next = event.flags.contains(modifiers) && event.flags.intersection(extras).isEmpty
        if next != held {
            held = next
            changed?(held)
        }
    }

    deinit {
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        if let tap { CFMachPortInvalidate(tap) }
    }
}

struct InsertionTarget {
    let pid: pid_t
    let focused: AXUIElement?

    init?() {
        guard let pid = NSWorkspace.shared.frontmostApplication?.processIdentifier else { return nil }
        self.pid = pid
        focused = Self.focusedElement(pid: pid)
    }

    func insert(_ transcript: String) -> Bool {
        guard AXIsProcessTrusted(),
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
              let focused, let current = Self.focusedElement(pid: pid),
              CFEqual(focused, current) else { return false }
        var role: CFTypeRef?
        if AXUIElementCopyAttributeValue(current, kAXSubroleAttribute as CFString, &role) == .success,
           role as? String == kAXSecureTextFieldSubrole as String { return false }
        var writable = DarwinBoolean(false)
        if AXUIElementIsAttributeSettable(current, kAXSelectedTextAttribute as CFString, &writable) == .success,
           writable.boolValue,
           AXUIElementSetAttributeValue(current, kAXSelectedTextAttribute as CFString, transcript as CFString) == .success {
            return true
        }

        // Some editors expose focus but handle edits only through input events.
        // Unicode events target the original process and leave the clipboard alone.
        guard let source = CGEventSource(stateID: .privateState) else { return false }
        var units = [UniChar]()
        for character in transcript {
            let next = Array(String(character).utf16)
            if units.count + next.count > 16 && !units.isEmpty {
                guard post(units, from: source) else { return false }
                units.removeAll(keepingCapacity: true)
            }
            units.append(contentsOf: next)
        }
        return units.isEmpty || post(units, from: source)
    }

    private func post(_ units: [UniChar], from source: CGEventSource) -> Bool {
        guard let down = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: true),
              let up = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: false) else { return false }
        down.flags = []
        up.flags = []
        units.withUnsafeBufferPointer { characters in
            down.keyboardSetUnicodeString(stringLength: characters.count, unicodeString: characters.baseAddress)
        }
        down.postToPid(pid)
        up.postToPid(pid)
        return true
    }

    private static func focusedElement(pid: pid_t) -> AXUIElement? {
        var value: CFTypeRef?
        let result = AXUIElementCopyAttributeValue(
            AXUIElementCreateApplication(pid), kAXFocusedUIElementAttribute as CFString, &value
        )
        guard result == .success, let value, CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }
}
