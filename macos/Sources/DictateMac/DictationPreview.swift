import AppKit

@MainActor
final class DictationPreview {
    private let label = NSTextField(wrappingLabelWithString: "")
    private let panel = NSPanel(
        contentRect: NSRect(x: 0, y: 0, width: 440, height: 76),
        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false
    )

    init() {
        panel.ignoresMouseEvents = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = .windowBackgroundColor
        panel.hasShadow = true
        label.maximumNumberOfLines = 3
        label.frame = NSRect(x: 14, y: 8, width: 412, height: 60)
        panel.contentView?.addSubview(label)
    }

    func show(_ text: String) {
        guard let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) })
                ?? NSScreen.main else { return }
        label.stringValue = text
        let area = screen.visibleFrame
        panel.setFrameOrigin(NSPoint(x: area.midX - panel.frame.width / 2, y: area.minY + 36))
        panel.orderFrontRegardless()
    }

    func hide() { panel.orderOut(nil) }
}
