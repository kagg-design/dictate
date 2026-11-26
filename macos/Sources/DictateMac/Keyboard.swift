import AppKit
import ApplicationServices
import Carbon.HIToolbox

struct ShortcutBinding: Codable, Equatable {
    static let dictationDefault = Self([.maskControl, .maskAlternate])
    static let insertLastDefault = Self([.maskControl, .maskCommand], key: Int64(kVK_F9), name: "F9")
    static let copyLast = Self([.maskControl, .maskCommand], key: Int64(kVK_ANSI_V), name: "V")
    static let modifierMask: CGEventFlags = [.maskControl, .maskAlternate, .maskCommand, .maskShift]

    private let modifierBits: UInt64
    let key: Int64?
    private let name: String

    init(_ modifiers: CGEventFlags, key: Int64? = nil, name: String = "") {
        modifierBits = modifiers.rawValue
        self.key = key
        self.name = name
    }

    var modifiers: CGEventFlags { CGEventFlags(rawValue: modifierBits) }

    var isValid: Bool {
        let size = modifierBits.nonzeroBitCount + (key == nil ? 0 : 1)
        return (2...3).contains(size) && modifiers.isSubset(of: Self.modifierMask) &&
            (key == nil || !name.isEmpty)
    }

    var label: String {
        let labels: [(CGEventFlags, String)] = [
            (.maskControl, "Control"), (.maskAlternate, "Option"), (.maskShift, "Shift"), (.maskCommand, "Command"),
        ]
        return (labels.compactMap { modifiers.contains($0.0) ? $0.1 : nil } + (key == nil ? [] : [name]))
            .joined(separator: " + ")
    }

    static func == (lhs: Self, rhs: Self) -> Bool {
        lhs.modifierBits == rhs.modifierBits && lhs.key == rhs.key
    }

    func conflicts(with other: Self) -> Bool {
        if key == nil { return modifiers.isSubset(of: other.modifiers) }
        if other.key == nil { return other.modifiers.isSubset(of: modifiers) }
        return self == other
    }

    static func decode(_ data: Data?) -> Self? {
        guard let data, let binding = try? JSONDecoder().decode(Self.self, from: data),
              binding.isValid else { return nil }
        return binding
    }

    static func legacyDictation(_ value: String?) -> Self {
        switch value {
        case "controlOptionSpace": Self([.maskControl, .maskAlternate], key: Int64(kVK_Space), name: "Space")
        case "controlShiftSpace": Self([.maskControl, .maskShift], key: Int64(kVK_Space), name: "Space")
        default: dictationDefault
        }
    }

    static func keyName(_ code: Int64, event: CGEvent) -> String {
        switch Int(code) {
        case kVK_Space: return "Space"
        case kVK_Escape: return "Esc"
        case kVK_Return, kVK_ANSI_KeypadEnter: return "Return"
        case kVK_Tab: return "Tab"
        case kVK_Delete: return "Backspace"
        case kVK_ForwardDelete: return "Delete"
        case kVK_Home: return "Home"
        case kVK_End: return "End"
        case kVK_PageUp: return "Page Up"
        case kVK_PageDown: return "Page Down"
        case kVK_LeftArrow: return "Left"
        case kVK_RightArrow: return "Right"
        case kVK_UpArrow: return "Up"
        case kVK_DownArrow: return "Down"
        default:
            if let characters = NSEvent(cgEvent: event)?.charactersIgnoringModifiers, !characters.isEmpty {
                if let scalar = characters.unicodeScalars.first, (0xF704...0xF717).contains(scalar.value) {
                    return "F\(scalar.value - 0xF703)"
                }
                return characters.uppercased()
            }
            return "Key \(code)"
        }
    }
}

final class DictationShortcut {
    enum Action { case start, startWithPostprocessing, finish, cancel, copy, insertLast }

    private var tap: CFMachPort?
    private var source: CFRunLoopSource?
    private var requestedFiltering = false
    private var keysDown = Set<Int64>()
    private var chordDown = false
    private var active = false
    private var postprocessingActive = false
    private var suppressedKeys = Set<Int64>()
    private var insertArmed = false
    private var capture: ((ShortcutBinding?, String) -> Void)?
    private var captureSize = 0
    private var captureResult: ShortcutBinding?
    private var captureKeyNames: [Int64: String] = [:]
    var choice: ShortcutBinding = .dictationDefault { didSet { chordDown = false } }
    var insertLast: ShortcutBinding? = .insertLastDefault { didSet { insertArmed = false } }
    var postprocess: ShortcutBinding?
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

    func disarm() {
        active = false
        postprocessingActive = false
    }

    func beginCapture(_ completion: @escaping (ShortcutBinding?, String) -> Void) -> Bool {
        guard installed, !active, capture == nil else { return false }
        capture = completion
        captureSize = 0
        captureResult = nil
        captureKeyNames.removeAll()
        insertArmed = false
        return true
    }

    func cancelCapture() { capture = nil }

    private func receiveCapture(_ type: CGEventType, event: CGEvent, code: Int64, modifiers: CGEventFlags) {
        if type == .keyDown && code == Int64(kVK_Escape) && modifiers.isEmpty {
            let completion = capture
            cancelCapture()
            completion?(nil, "")
            return
        }
        if type == .keyDown {
            captureKeyNames[code] = ShortcutBinding.keyName(code, event: event)
        }
        let size = modifiers.rawValue.nonzeroBitCount + keysDown.count
        if size == 0 && captureSize > 0 {
            let completion = capture
            cancelCapture()
            completion?(captureResult, captureResult == nil ? "Press two or three keys together with a modifier." : "")
        } else if size > 0 && size >= captureSize {
            captureSize = size
            let key = keysDown.first
            let candidate = ShortcutBinding(modifiers, key: key, name: key.flatMap { captureKeyNames[$0] } ?? "")
            captureResult = candidate.isValid && keysDown.count <= 1 ? candidate : nil
        }
    }

    private func matchesInsert(_ type: CGEventType, code: Int64, modifiers: CGEventFlags) -> Bool {
        guard let insertLast else { return false }
        if let key = insertLast.key {
            return type == .keyDown && code == key && modifiers == insertLast.modifiers
        }
        if type == .keyDown { insertArmed = false }
        if type == .flagsChanged {
            let wasArmed = insertArmed
            insertArmed = modifiers == insertLast.modifiers && keysDown.isEmpty
            return wasArmed && modifiers != insertLast.modifiers && modifiers.isSubset(of: insertLast.modifiers)
        }
        return false
    }

    private func receive(_ type: CGEventType, event: CGEvent) -> Bool {
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
            if active {
                disarm()
                changed?(.cancel)
            }
            chordDown = false
            keysDown.removeAll()
            insertArmed = false
            let completion = capture
            cancelCapture()
            completion?(nil, "Keyboard capture was interrupted.")
            suppressedKeys.removeAll()
            if let tap { CGEvent.tapEnable(tap: tap, enable: true) }
            return true
        }
        let code = event.getIntegerValueField(.keyboardEventKeycode)
        let down = type == .keyDown
        let up = type == .keyUp
        let modifiers = event.flags.intersection(ShortcutBinding.modifierMask)
        if down { keysDown.insert(code) }
        if up { keysDown.remove(code) }
        let suppressRelease = up && suppressedKeys.remove(code) != nil
        if down && suppressedKeys.contains(code) { return false }
        if capture != nil {
            receiveCapture(type, event: event, code: code, modifiers: modifiers)
            chordDown = false
            if down { suppressedKeys.insert(code) }
            return !down && !suppressRelease
        }
        let freshDown = down && event.getIntegerValueField(.keyboardEventAutorepeat) == 0
        if freshDown {
            if let postprocess, code == postprocess.key, modifiers == postprocess.modifiers,
               !active || postprocessingActive {
                suppressedKeys.insert(code)
                active.toggle()
                postprocessingActive = active
                chordDown = false
                changed?(active ? .startWithPostprocessing : .finish)
                return false
            }
            if code == ShortcutBinding.copyLast.key && modifiers == ShortcutBinding.copyLast.modifiers {
                suppressedKeys.insert(code)
                changed?(.copy)
                return false
            }
            if code == Int64(kVK_Escape) && escapeCancels && active &&
                !(choice.key == code && modifiers == choice.modifiers) {
                suppressedKeys.insert(code)
                disarm()
                changed?(.cancel)
                return false
            }
        }
        if (!down || freshDown) && matchesInsert(type, code: code, modifiers: modifiers) {
            changed?(.insertLast)
            if down {
                suppressedKeys.insert(code)
                return false
            }
        }
        if postprocessingActive { return !suppressRelease }
        let next = modifiers == choice.modifiers && (choice.key.map { keysDown.contains($0) } ?? true)
        guard next != chordDown else { return !suppressRelease }
        chordDown = next
        if next {
            // An ordinary key must go down after its modifiers are in place.
            guard choice.key.map({ freshDown && code == $0 }) ?? (type == .flagsChanged) else { return !suppressRelease }
            active = toggle ? !active : true
            changed?(active ? .start : .finish)
            if choice.key != nil {
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
    private let pid: pid_t
    private let app: AXUIElement
    private let window: AXUIElement?
    private let focused: AXUIElement?
    private let isSlack: Bool

    init?() {
        guard AXIsProcessTrusted(), let frontmost = NSWorkspace.shared.frontmostApplication else { return nil }
        let pid = frontmost.processIdentifier
        let app = AXUIElementCreateApplication(pid)
        let isSlack = frontmost.bundleIdentifier == "com.tinyspeck.slackmacgap"
        var editor = Self.focusedElement(in: app, pid: pid, preferEditor: isSlack)
        if isSlack && (editor == nil || editor.map(Self.isEditor) == false) {
            // Electron can expose the window before building its editor's AX tree.
            AXUIElementSetAttributeValue(app, "AXManualAccessibility" as CFString, true as CFTypeRef)
            editor = Self.focusedElement(in: app, pid: pid, preferEditor: true)
        }
        self.init(
            pid: pid, app: app, window: Self.focusedWindow(in: app, editor: editor),
            focused: editor.flatMap { isSlack && Self.isUnresolvedEditor($0) ? nil : $0 },
            isSlack: isSlack
        )
    }

    private init(pid: pid_t, app: AXUIElement, window: AXUIElement?, focused: AXUIElement?, isSlack: Bool) {
        self.pid = pid
        self.app = app
        self.window = window
        self.focused = focused
        self.isSlack = isSlack
    }

    func resolved() -> InsertionTarget? {
        guard AXIsProcessTrusted(),
              NSWorkspace.shared.frontmostApplication?.processIdentifier == pid,
              let current = Self.focusedElement(in: app, pid: pid, preferEditor: isSlack)
        else { return nil }
        let subrole = Self.attribute(kAXSubroleAttribute, of: current) as? String
        guard subrole != kAXSecureTextFieldSubrole as String else { return nil }
        let currentWindow = Self.focusedWindow(in: app, editor: current)
        if let window {
            guard let currentWindow, CFEqual(window, currentWindow) else { return nil }
        }
        if isSlack {
            guard Self.isEditor(current) else { return nil }
        }
        if let focused {
            // Electron can replace the AX node while keeping the same editor.
            guard CFEqual(focused, current) || (isSlack && window != nil && Self.sameEditor(focused, current))
            else { return nil }
        } else {
            // Accept a late Slack AX editor only in the original window.
            guard isSlack && window != nil else { return nil }
        }
        return InsertionTarget(pid: pid, app: app, window: currentWindow, focused: current, isSlack: isSlack)
    }

    func insert(_ text: String, useCursorContext: Bool) -> Bool {
        guard let target = resolved(), let current = target.focused else { return false }
        let transcript = useCursorContext ? Self.format(text, for: current) : text
        var writable = DarwinBoolean(false)
        if !isSlack,
           AXUIElementIsAttributeSettable(current, kAXSelectedTextAttribute as CFString, &writable) == .success,
           writable.boolValue,
           AXUIElementSetAttributeValue(current, kAXSelectedTextAttribute as CFString, transcript as CFString) == .success {
            return true
        }

        // Slack's composer handles normal session keyboard events. Send one
        // character at a time without a Return key that could submit a message.
        guard let source = CGEventSource(stateID: .privateState) else { return false }
        if isSlack {
            for (index, character) in Self.slackTypingText(transcript).enumerated() {
                if index.isMultiple(of: 16),
                   NSWorkspace.shared.frontmostApplication?.processIdentifier != pid { return false }
                guard post(Array(String(character).utf16), from: source) else { return false }
            }
            return true
        }
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

    static func slackTypingText(_ text: String) -> String {
        String(text.map { character in
            character.isNewline || character.unicodeScalars.contains { $0.value < 32 || $0.value == 127 }
                ? " " : character
        })
    }

    private static func isEditor(_ element: AXUIElement) -> Bool {
        switch role(of: element) {
        case "AXTextArea", "AXTextField", "AXComboBox": return true
        default: return attribute(kAXSelectedTextRangeAttribute, of: element) != nil
        }
    }

    private static func isUnresolvedEditor(_ element: AXUIElement) -> Bool {
        guard !isEditor(element) else { return false }
        switch role(of: element) {
        case nil, "AXApplication", "AXWindow", "AXGroup", "AXWebArea", "AXUnknown": return true
        default: return false
        }
    }

    private static func sameEditor(_ first: AXUIElement, _ second: AXUIElement) -> Bool {
        guard role(of: first) == role(of: second) else { return false }
        return ["AXDOMIdentifier", kAXIdentifierAttribute].contains { name in
            guard let identifier = attribute(name, of: first) as? String, !identifier.isEmpty else { return false }
            return identifier == attribute(name, of: second) as? String
        }
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
            if isSlack {
                up.keyboardSetUnicodeString(stringLength: characters.count, unicodeString: characters.baseAddress)
            }
        }
        if isSlack {
            down.post(tap: .cgSessionEventTap)
            up.post(tap: .cgSessionEventTap)
        } else {
            down.postToPid(pid)
            up.postToPid(pid)
        }
        return true
    }

    private static func focusedElement(in app: AXUIElement, pid: pid_t, preferEditor: Bool) -> AXUIElement? {
        let focused = element(kAXFocusedUIElementAttribute, of: app)
        if let focused, !preferEditor || isEditor(focused) { return focused }
        guard let fallback = element(kAXFocusedUIElementAttribute, of: AXUIElementCreateSystemWide()) else {
            return focused
        }
        var owner: pid_t = 0
        guard AXUIElementGetPid(fallback, &owner) == .success, owner == pid else { return focused }
        return fallback
    }

    private static func role(of element: AXUIElement) -> String? {
        attribute(kAXRoleAttribute, of: element) as? String
    }

    private static func focusedWindow(in app: AXUIElement, editor: AXUIElement?) -> AXUIElement? {
        element(kAXFocusedWindowAttribute, of: app) ?? editor.flatMap { element(kAXWindowAttribute, of: $0) }
    }

    private static func attribute(_ name: String, of element: AXUIElement) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value
    }

    private static func element(_ name: String, of parent: AXUIElement) -> AXUIElement? {
        guard let value = attribute(name, of: parent), CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }
}
