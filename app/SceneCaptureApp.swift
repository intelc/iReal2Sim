import SwiftUI
import UniformTypeIdentifiers
import AVFoundation

@main
struct SceneCaptureApp: App {
    var body: some Scene { WindowGroup { CaptureView() } }
}

final class CaptureModel: ObservableObject {
    @Published var status = CaptureStatus()
    @Published var mainPreview: UIImage?
    @Published var ultraPreview: UIImage?
    @Published var sessions: [URL] = []
    @Published var exportURL: URL?
    @Published var showingExport = false
    @Published var probeAvailable = false
    private let service = CaptureService()
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    var isBusy: Bool { ["preparing", "checking", "starting", "recording", "finalizing"].contains(status.phase) }

    init() {
        service.onStatus = { [weak self] value in
            self?.status = value
            UIApplication.shared.isIdleTimerDisabled = ["recording", "finalizing"].contains(value.phase)
            self?.refreshSessions()
            if !["recording", "finalizing"].contains(value.phase), let self, self.backgroundTask != .invalid {
                UIApplication.shared.endBackgroundTask(self.backgroundTask)
                self.backgroundTask = .invalid
            }
        }
        service.onPreview = { [weak self] stream, image in
            if stream == "main" { self?.mainPreview = image } else { self?.ultraPreview = image }
        }
        refreshSessions()
        status.freeBytes = CaptureFiles.diskFreeBytes()
    }
    func prepare() { status.phase = "preparing"; service.prepare() }
    func start() { status.phase = "starting"; service.startRecording() }
    func stop() { service.stopRecording() }
    func suspend() {
        if ["recording", "finalizing"].contains(status.phase), backgroundTask == .invalid {
            backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Finish capture files") { [weak self] in
                guard let self, self.backgroundTask != .invalid else { return }
                UIApplication.shared.endBackgroundTask(self.backgroundTask)
                self.backgroundTask = .invalid
            }
        }
        service.suspend()
    }
    func refreshSessions() {
        sessions = ((try? FileManager.default.contentsOfDirectory(at: CaptureFiles.sessions,
                      includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles])) ?? [])
            .filter { (try? $0.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
        probeAvailable = FileManager.default.fileExists(atPath: CaptureFiles.probe.path)
    }
    func export(_ url: URL) { exportURL = url; showingExport = true }
    func summary(_ url: URL) -> String {
        guard let data = try? Data(contentsOf: url.appendingPathComponent("manifest.json")),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "Incomplete session" }
        let phase = object["status"] as? String ?? "unknown"
        if phase == "recording" || phase == "finalizing" {
            return isBusy ? phase.capitalized : "Interrupted · partial data retained"
        }
        return phase.capitalized
    }
}

struct CaptureView: View {
    @StateObject private var model = CaptureModel()
    @Environment(\.scenePhase) private var scenePhase
    private let background = Color(red: 0.055, green: 0.075, blue: 0.085)
    private let accent = Color(red: 0.65, green: 0.88, blue: 0.71)

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    HStack {
                        Label(model.status.phase.uppercased(), systemImage: model.status.phase == "recording" ? "record.circle.fill" : "circle.fill")
                            .font(.caption.monospaced().weight(.semibold))
                            .foregroundStyle(model.status.phase == "recording" ? .red : accent)
                        Spacer()
                        Text(ByteCountFormatter.string(fromByteCount: model.status.freeBytes, countStyle: .file) + " free")
                            .font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                    Text(model.status.message).font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(alignment: .top, spacing: 12) {
                        preview(model.mainPreview, title: "MAIN · 1×")
                        preview(model.ultraPreview, title: "ULTRA-WIDE · 0.5×")
                    }
                    HStack {
                        ForEach(["main", "ultra", "depth"], id: \.self) { stream in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(stream.uppercased()).font(.caption2.monospaced()).foregroundStyle(.secondary)
                                Text(String(format: "%.1f fps", model.status.liveFPS[stream] ?? 0))
                                    .font(.caption.monospaced())
                                    .foregroundStyle((model.status.liveFPS[stream] ?? 0) > 0 ? accent : .secondary)
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                    VStack(spacing: 12) {
                        HStack {
                            Text(String(format: "%02d:%02d", Int(model.status.elapsed) / 60, Int(model.status.elapsed) % 60))
                                .font(.system(size: 46, weight: .light, design: .monospaced)).contentTransition(.numericText())
                            Spacer()
                            VStack(alignment: .trailing) {
                                Text("THERMAL").font(.caption2.monospaced()).foregroundStyle(.secondary)
                                Text(model.status.thermal).font(.caption)
                            }
                        }
                        ForEach(["main", "ultra", "depth", "accelerometer", "gyroscope"], id: \.self) { stream in
                            HStack {
                                Text(stream.capitalized).foregroundStyle(.secondary)
                                Spacer()
                                Text("\(model.status.counts[stream] ?? 0)").monospacedDigit()
                                if let drops = model.status.drops[stream], drops > 0 {
                                    Text("\(drops) dropped").foregroundStyle(.orange).font(.caption)
                                }
                            }.font(.subheadline)
                        }
                    }
                    .padding(18).background(.white.opacity(0.045), in: RoundedRectangle(cornerRadius: 14))
                    controls
                    DisclosureGroup("After recording: build your scene") {
                        VStack(alignment: .leading, spacing: 10) {
                            Text("1. Stop recording and wait for Complete. Tap the session below to export its entire folder, or copy it using Finder file sharing.")
                            Text("2. On your Mac, download iReal2Sim from GitHub and run ./start. Choose the exported folder in the local dashboard.")
                            Text("3. Optionally enter an OpenAI API key on your Mac for capture review and object recognition. No API key is needed on this iPhone.")
                            Text("Both cameras request 1080p60 and LiDAR up to 30 fps. Compatibility is checked on this phone; use short test clips before a long capture.")
                        }.font(.footnote).foregroundStyle(.secondary).padding(.top, 8)
                    }
                    Text("A rigid chest mount and both hands in view. Capture stays on this phone; recording stops when the app enters the background.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Divider()
                    HStack {
                        Text("Recordings").font(.title3.weight(.semibold))
                        Spacer()
                        if model.probeAvailable {
                            Button("Export device report") { model.export(CaptureFiles.probe) }
                                .font(.caption).disabled(model.isBusy)
                        }
                    }
                    if model.sessions.isEmpty {
                        Text("Your sessions will appear here. They are also available in Files → On My iPhone → Scene Capture.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(model.sessions, id: \.self) { url in
                        Button { model.export(url) } label: {
                            HStack(alignment: .center) {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(url.lastPathComponent).font(.caption.monospaced()).lineLimit(2)
                                    Text(model.summary(url)).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Image(systemName: "square.and.arrow.up")
                            }.padding(.vertical, 8)
                        }.buttonStyle(.plain).disabled(model.isBusy)
                    }
                }.padding(22)
            }
            .background(background).navigationTitle("Scene Capture")
            .toolbarColorScheme(.dark, for: .navigationBar)
            .preferredColorScheme(.dark).tint(accent)
            .sheet(isPresented: $model.showingExport) {
                if let url = model.exportURL { ExportPicker(url: url) }
            }
            .onChange(of: scenePhase) { _, phase in if phase == .background { model.suspend() } }
            #if DEBUG
            .task {
                // Device recording tests require an explicit bounded launch argument.
                if (ProcessInfo.processInfo.arguments.contains("--prepare-cameras") || ProcessInfo.processInfo.arguments.contains("--capture-test-seconds")),
                   AVCaptureDevice.authorizationStatus(for: .video) == .authorized {
                    model.prepare()
                }
            }
            #endif
        }
    }

    private var controls: some View {
        Group {
            if model.status.phase == "recording" {
                Button(action: model.stop) { Label("Stop recording", systemImage: "stop.fill").frame(maxWidth: .infinity).padding(.vertical, 10) }
                    .buttonStyle(.borderedProminent).tint(.red)
            } else if model.status.phase == "ready" {
                Button(action: model.start) { Label("Start recording", systemImage: "record.circle").frame(maxWidth: .infinity).padding(.vertical, 10) }
                    .buttonStyle(.borderedProminent)
            } else {
                Button(action: model.prepare) {
                    HStack { if model.isBusy { ProgressView() }; Text(model.isBusy ? "Please wait…" : "Prepare cameras") }
                        .frame(maxWidth: .infinity).padding(.vertical, 10)
                }.buttonStyle(.borderedProminent).disabled(model.isBusy)
            }
        }
    }

    private func preview(_ image: UIImage?, title: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(.white.opacity(0.045))
                if let image { Image(uiImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 12)) }
                else { Image(systemName: "camera").font(.title2).foregroundStyle(.secondary) }
            }.aspectRatio(3 / 4, contentMode: .fit)
            Text(title).font(.system(size: 10, weight: .medium, design: .monospaced)).foregroundStyle(.secondary)
        }.frame(maxWidth: .infinity)
    }
}

private struct ExportPicker: UIViewControllerRepresentable {
    let url: URL
    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        UIDocumentPickerViewController(forExporting: [url], asCopy: true)
    }
    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}
}
