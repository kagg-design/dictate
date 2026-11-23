import AppKit
import ApplicationServices

enum ShortcutChoice: String, CaseIterable {
    case controlOption, controlOptionSpace, controlShiftSpace

    var label: String {
        switch self {
        case .controlOption: "Control + Option"
        case .controlOptionSpace: "Control + Option + Space"
        case .controlShiftSpace: "Control + Shift + Space"
        }
    }

    var modifiers: CGEventFlags {
        switch self {
        case .controlOption, .controlOptionSpace: [.maskControl, .maskAlternate]
        case .controlShiftSpace: [.maskControl, .maskShift]
        }
    }
}

final class DictationShortcut {
    enum Action { case start, finish, cancel, copy }

    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var requestedFiltering = false
    private var spaceDown = false
    private var chordDown = false
    private var active = false
    private var suppressedKeys = Set<Int64>()
    var choice: ShortcutChoice = .controlOption
    var toggle = false
    var escapeCancels = true
    var changed: ((Action) -> Void)?

    var installed: Bool { tap != nil }

    func install(filterKeys: Bool) {
        guard CGPreflightListenEventAccess() else { return }
        if installed && requestedFiltering == filterKeys { return }
        if let source { CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes) }
        if let tap { CFMachPortInvalidate(tap) }
        source = nil
        tap = nil
        requestedFiltering = filterKeys
        let options: CGEventTapOptions = filterKeys ? .defaultTap : .listenOnly
        tap = makeTap(options) ?? (filterKeys ? makeTap(.listenOnly) : nil)
        guard let tap else { return }
        source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0)
        if let source { CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes) }
        CGEvent.tapEnable(tap: tap, enable: true)
    }

    private func makeTap(_ options: CGEventTapOptions) -> CFMachPort? {
        let eventMask = [CGEventType.flagsChanged, .keyDown, .keyUp].reduce(CGEventMask(0)) {
            $0 | (CGEventMask(1) << $1.rawValue)
        }
        return CGEvent.tapCreate(
            tap: .cgSessionEventTap, place: .headInsertEventTap, options: options,
            eventsOfInterest: eventMask, callback: { _, type, event, context in
                if let context {
                    let shortcut = Unmanaged<DictationShortcut>.fromOpaque(context).takeUnretainedValue()
                    if !shortcut.receive(type, event: event) { return nil }
                }
                return Unmanaged.passUnretained(event)
            }, userInfo: Unmanaged.passUnretained(self).toOpaque()
        )
    }

    func disarm() { active = false }

    private func receive(_ type: CGEventType, event: CGEvent) -> Bool {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if active {
                disarm()
                changed?(.cancel)
            }
            chordDown = false
            spaceDown = false
            suppressedKeys.removeAll()
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return true
        }
        let code = event.getIntegerValueField(.keyboardEventKeycode)
        let down = type == .keyDown
        let up = type == .keyUp
        let modifiers = event.flags.intersection([.maskControl, .maskAlternate, .maskCommand, .maskShift])
        if (down || up) && code == 49 { spaceDown = down }
        let suppressRelease = up && suppressedKeys.remove(code) != nil
        if down && suppressedKeys.contains(code) { return false }
        if down && event.getIntegerValueField(.keyboardEventAutorepeat) == 0 {
            if code == 9 && modifiers == [.maskControl, .maskCommand] {
                suppressedKeys.insert(code)
                changed?(.copy)
                return false
            }
            if code == 53 && escapeCancels && active {
                suppressedKeys.insert(code)
                disarm()
                changed?(.cancel)
                return false
            }
        }
        let usesSpace = choice != .controlOption
        let next = modifiers == choice.modifiers && (!usesSpace || spaceDown)
        guard next != chordDown else { return !suppressRelease }
        chordDown = next
        if next {
            // A Space preset begins on the Space key down, not when a later
            // modifier is added to a Space key already held by another app.
            guard !usesSpace || (code == 49 && down) else { return !suppressRelease }
            active = toggle ? !active : true
            changed?(active ? .start : .finish)
            if usesSpace {
                suppressedKeys.insert(code)
                return false
            }
        } else if active && !toggle {
            disarm()
            changed?(.finish)
        }
        return !suppressRelease
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

    func insert(_ text: String, useCursorContext: Bool) -> Bool {
        guard AXIsProcessTrusted(),
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
              let focused, let current = Self.focusedElement(pid: pid),
              CFEqual(focused, current) else { return false }
        var role: CFTypeRef?
        if AXUIElementCopyAttributeValue(current, kAXSubroleAttribute as CFString, &role) == .success,
           role as? String == kAXSecureTextFieldSubrole as String { return false }
        let transcript = useCursorContext ? Self.format(text, for: current) : text
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

    private static func format(_ text: String, for element: AXUIElement) -> String {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, kAXSelectedTextRangeAttribute as CFString, &value) == .success,
              let value, CFGetTypeID(value) == AXValueGetTypeID() else { return text }
        var selected = CFRange(location: 0, length: 0)
        guard AXValueGetValue(value as! AXValue, .cfRange, &selected),
              selected.location >= 0, selected.length >= 0, selected.location <= Int.max - selected.length else { return text }
        let start = max(0, selected.location - 48)
        guard let before = string(in: CFRange(location: start, length: selected.location - start), of: element) else {
            return text
        }
        let after = string(in: CFRange(location: selected.location + selected.length, length: 1), of: element)
        var result = text.trimmingCharacters(in: .whitespaces)
        guard let first = result.first else { return result }
        let preceding = before.trimmingCharacters(in: .whitespaces)
        if preceding.isEmpty || preceding.last.map({ ".!?…\r\n".contains($0) }) == true {
            result = first.uppercased() + String(result.dropFirst())
        } else if let last = preceding.last, (last.isLetter || last.isNumber || ",;:".contains(last)),
                  let word = result.split(whereSeparator: \.isWhitespace).first,
                  word != "I", word.count > 1, word.dropFirst().allSatisfy(\.isLowercase) {
            result = first.lowercased() + String(result.dropFirst())
        }
        if let next = after?.first, !next.isNewline && !next.isWhitespace,
           result.hasSuffix("."), !result.hasSuffix(".."),
           result.range(of: #"\b(?:[\p{L}]\.){2,}$"#, options: .regularExpression) == nil {
            result.removeLast()
        }
        if let last = before.last, (last.isLetter || last.isNumber || ".,!?…:;)".contains(last)),
           first.isLetter || first.isNumber { result = " " + result }
        if let next = after?.first, (next.isLetter || next.isNumber),
           let last = result.last, last.isLetter || last.isNumber || ".,!?…:;)".contains(last) {
            result += " "
        }
        return result
    }

    private static func string(in selection: CFRange, of element: AXUIElement) -> String? {
        var range = selection
        if range.length == 0 { return "" }
        guard let parameter = AXValueCreate(.cfRange, &range) else { return nil }
        var value: CFTypeRef?
        guard AXUIElementCopyParameterizedAttributeValue(
            element, kAXStringForRangeParameterizedAttribute as CFString, parameter, &value
        ) == .success else { return nil }
        return value as? String
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
