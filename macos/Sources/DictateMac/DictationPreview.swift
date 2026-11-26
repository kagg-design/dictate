import AppKit
import QuartzCore

@MainActor
final class DictationPreview {
    private let label = NSTextField(wrappingLabelWithString: "")
    private let badge = NSView()
    private let symbol = NSImageView(frame: NSRect(x: 6, y: 6, width: 20, height: 20))
    private let arc = CAShapeLayer()
    private let panel = NSPanel(
        contentRect: NSRect(x: 0, y: 0, width: 440, height: 76),
        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false
    )
    private var pulse: Timer?
    private var screen: NSScreen?

    init() {
        panel.ignoresMouseEvents = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.backgroundColor = .windowBackgroundColor
        panel.hasShadow = true
        symbol.imageScaling = .scaleProportionallyDown
        symbol.wantsLayer = true
        let path = CGMutablePath()
        path.addArc(center: CGPoint(x: 10, y: 10), radius: 6.5,
                    startAngle: 0, endAngle: 7 * CGFloat.pi / 4, clockwise: false)
        arc.path = path
        arc.fillColor = nil
        arc.strokeColor = NSColor.systemBlue.cgColor
        arc.lineWidth = 2
        arc.lineCap = .round
        arc.frame = symbol.bounds
        arc.isHidden = true
        symbol.layer?.addSublayer(arc)
        badge.wantsLayer = true
        badge.layer?.borderWidth = 1.5
        badge.layer?.cornerRadius = 6
        badge.addSubview(symbol)
        panel.contentView?.addSubview(badge)
        label.maximumNumberOfLines = 3
        panel.contentView?.addSubview(label)
    }

    func showRecording() {
        screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) }) ?? NSScreen.main
        symbol.layer?.removeAnimation(forKey: "processing")
        arc.isHidden = true
        setSymbol("mic.fill", color: .systemRed, description: "Dictate is recording")
        show("")
        pulse?.invalidate()
        let timer = Timer(timeInterval: 0.4, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.pulse != nil else { return }
                self.symbol.alphaValue = self.symbol.alphaValue > 0.9 ? 0.45 : 1
            }
        }
        pulse = timer
        RunLoop.main.add(timer, forMode: .common)
    }

    func show(_ text: String) {
        guard let screen else { return }
        let withText = !text.isEmpty
        let size = withText ? NSSize(width: 440, height: 76) : NSSize(width: 48, height: 48)
        badge.frame = NSRect(x: 8, y: (size.height - 32) / 2, width: 32, height: 32)
        label.frame = NSRect(x: 52, y: 8, width: 372, height: 60)
        label.isHidden = !withText
        label.stringValue = text
        showPanel(size, on: screen)
    }

    func showProcessing() {
        guard let screen = NSScreen.screens.first(where: { $0.frame.contains(NSEvent.mouseLocation) })
                ?? NSScreen.main else { return }
        symbol.image = nil
        symbol.alphaValue = 1
        symbol.setAccessibilityLabel("Dictate is processing")
        badge.layer?.borderColor = NSColor.systemBlue.cgColor
        arc.isHidden = false
        badge.frame = NSRect(x: 8, y: 8, width: 32, height: 32)
        label.isHidden = true
        let rotation = CABasicAnimation(keyPath: "transform.rotation.z")
        rotation.fromValue = 0
        rotation.toValue = -Double.pi * 2
        rotation.duration = 0.9
        rotation.repeatCount = .infinity
        symbol.layer?.add(rotation, forKey: "processing")
        showPanel(NSSize(width: 48, height: 48), on: screen)
    }

    private func setSymbol(_ name: String, color: NSColor, description: String) {
        let image = NSImage(systemSymbolName: name, accessibilityDescription: description)
        image?.isTemplate = true
        symbol.image = image
        symbol.setAccessibilityLabel(description)
        symbol.contentTintColor = color
        symbol.alphaValue = 1
        badge.layer?.borderColor = color.cgColor
    }

    private func showPanel(_ size: NSSize, on screen: NSScreen) {
        panel.setContentSize(size)
        let area = screen.visibleFrame
        panel.setFrameOrigin(NSPoint(x: area.midX - panel.frame.width / 2, y: area.minY + 36))
        panel.orderFrontRegardless()
    }

    func hide() {
        pulse?.invalidate()
        pulse = nil
        symbol.layer?.removeAnimation(forKey: "processing")
        screen = nil
        panel.orderOut(nil)
    }
}
