import AVFoundation
import CoreImage
import CoreMotion
import UIKit

struct CaptureStatus {
    var phase = "idle"
    var message = "Prepare the cameras to check this device."
    var counts: [String: Int] = [:]
    var drops: [String: Int] = [:]
    var elapsed: Double = 0
    var freeBytes: Int64 = 0
    var thermal = "nominal"
    var sessionURL: URL?
    var liveFPS: [String: Double] = [:]
    var liveFrames: [String: Int] = [:]
}

final class CaptureService: NSObject, AVCaptureDataOutputSynchronizerDelegate {
    var onStatus: ((CaptureStatus) -> Void)?
    var onPreview: ((String, UIImage) -> Void)?
    private let session = AVCaptureMultiCamSession()
    private let sessionQueue = DispatchQueue(label: "scene.capture.session", qos: .userInitiated)
    private let dataQueue = DispatchQueue(label: "scene.capture.data", qos: .userInitiated)
    private let motionQueue = OperationQueue()
    private let motion = CMMotionManager()
    private let mainOutput = AVCaptureVideoDataOutput()
    private let ultraOutput = AVCaptureVideoDataOutput()
    private let depthOutput = AVCaptureDepthDataOutput()
    private let imageContext = CIContext(options: [.cacheIntermediates: false])
    private var synchronizer: AVCaptureDataOutputSynchronizer?
    private var mainCamera: AVCaptureDevice?
    private var ultraCamera: AVCaptureDevice?
    private var clock: CMClock?
    private var configuration: [String: Any] = [:]
    private var recorder: SessionRecorder?
    private var status = CaptureStatus()
    private var finalizing = false
    private var lastPreview: [String: Double] = [:]
    private var observers: [NSObjectProtocol] = []
    private var configured = false // sessionQueue only
    private var suspended = false // dataQueue only
    private var healthTimer: DispatchSourceTimer?
    private var recordingStartedAt: Double = 0
    private var lastSeen: [String: Double] = [:]
    private var liveFrames = ["main": 0, "ultra": 0, "depth": 0]
    private var liveDrops = ["main": 0, "ultra": 0, "depth": 0]
    private var previousLiveFrames = ["main": 0, "ultra": 0, "depth": 0]
    private var lastSampleHostNS: [String: String] = [:]
    private var liveStartedAt: Double = 0
    private var lastRateUpdate: Double = 0
    private var secondaryOnlyCallbacks = 0
    private let profile = CaptureProfile.requested
    private var readyRateWindows = 0
    #if DEBUG
    private var deviceTestStarted = false
    private var deviceTestDeadline: Double?
    private let deviceTestSeconds: Double? = {
        let arguments = ProcessInfo.processInfo.arguments
        guard let index = arguments.firstIndex(of: "--capture-test-seconds"),
              index + 1 < arguments.count, let seconds = Double(arguments[index + 1]),
              (5...120).contains(seconds) else { return nil }
        return seconds
    }()
    #endif

    override init() {
        super.init()
        motionQueue.name = "scene.capture.motion"
        motionQueue.maxConcurrentOperationCount = 1
        motionQueue.underlyingQueue = dataQueue
        let timer = DispatchSource.makeTimerSource(queue: dataQueue)
        timer.schedule(deadline: .now() + 1, repeating: 1)
        timer.setEventHandler { [weak self] in self?.heartbeat() }
        timer.resume()
        healthTimer = timer
        for name in [AVCaptureSession.wasInterruptedNotification, AVCaptureSession.runtimeErrorNotification] {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: session, queue: nil) { [weak self] note in
                guard let self else { return }
                let reason = (note.userInfo?[AVCaptureSessionErrorKey] as? Error)?.localizedDescription
                    ?? "Camera session was interrupted (\(String(describing: note.userInfo)))."
                self.dataQueue.async {
                    if self.recorder != nil { self.stopOnDataQueue(reason: "camera_interruption", error: reason) }
                    else { self.publish(phase: "failed", message: reason) }
                }
            })
        }
    }

    deinit {
        healthTimer?.cancel()
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
    }

    func prepare() {
        dataQueue.async { self.publish(phase: "preparing", message: "Checking camera formats and motion sensors…") }
        sessionQueue.async {
            do {
                try FileManager.default.createDirectory(at: CaptureFiles.documents, withIntermediateDirectories: true)
                var probe = CapabilityProbe.report()
                try CaptureFiles.writeJSON(probe, to: CaptureFiles.probe)
                #if targetEnvironment(simulator)
                throw CaptureFailure.message("Simulator capture is disabled. Run on a physical LiDAR-equipped Pro iPhone to test the cameras; the device report is available.")
                #else
                guard AVCaptureMultiCamSession.isMultiCamSupported else {
                    throw CaptureFailure.message("This device does not support MultiCam. The capability report is available; use a LiDAR-equipped Pro iPhone to record.")
                }
                switch AVCaptureDevice.authorizationStatus(for: .video) {
                case .authorized: break
                case .notDetermined:
                    AVCaptureDevice.requestAccess(for: .video) { granted in
                        if granted { self.prepare() }
                        else { self.dataQueue.async { self.publish(phase: "failed", message: "Camera access was denied. Enable it in Settings.") } }
                    }
                    return
                default: throw CaptureFailure.message("Camera access is disabled. Enable it in Settings.")
                }
                guard self.motion.isAccelerometerAvailable, self.motion.isGyroAvailable else {
                    throw CaptureFailure.message("Both accelerometer and gyroscope are required.")
                }
                if !self.configured {
                    do {
                        try self.configure()
                    } catch {
                        probe["configuration_errors"] = [error.localizedDescription]
                        try CaptureFiles.writeJSON(probe, to: CaptureFiles.probe)
                        throw error
                    }
                }
                if !self.session.isRunning { self.session.startRunning() }
                guard self.session.isRunning, let clock = self.session.synchronizationClock else {
                    throw CaptureFailure.message("The camera graph did not start or has no synchronization clock.")
                }
                probe["selected_configuration"] = self.configuration
                try CaptureFiles.writeJSON(probe, to: CaptureFiles.probe)
                let settings = self.configuration
                self.dataQueue.async {
                    self.suspended = false
                    self.clock = clock
                    self.configuration = settings
                    self.liveFrames = ["main": 0, "ultra": 0, "depth": 0]
                    self.liveDrops = self.liveFrames
                    self.previousLiveFrames = self.liveFrames
                    self.lastSeen = [:]
                    self.lastSampleHostNS = [:]
                    self.lastPreview = [:]
                    self.secondaryOnlyCallbacks = 0
                    self.readyRateWindows = 0
                    self.liveStartedAt = ProcessInfo.processInfo.systemUptime
                    self.lastRateUpdate = self.liveStartedAt
                    let fps = settings["requested_rgb_fps"] as? Int32 ?? 30
                    self.publish(phase: "checking", message: "1920×1080 at \(fps) fps RGB, 30 fps depth. Verifying live rates…")
                }
                #endif
            } catch {
                self.dataQueue.async {
                    self.publish(phase: "failed", message: error.localizedDescription)
                    #if DEBUG
                    if self.deviceTestSeconds != nil {
                        try? CaptureFiles.writeJSON(["schema": "iphone-rsa.device-capture-test/1", "phase": "failed",
                            "app_build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
                            "host_ns": CaptureFiles.now(), "message": error.localizedDescription],
                            to: CaptureFiles.documents.appendingPathComponent("capture-test.json"))
                    }
                    #endif
                }
            }
        }
    }

    private func format(for device: AVCaptureDevice, needsDepth: Bool) throws -> AVCaptureDevice.Format {
        let candidates = device.formats.filter { format in
            let dimensions = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
            let subtype = CMFormatDescriptionGetMediaSubType(format.formatDescription)
            return format.isMultiCamSupported && dimensions.width == profile.width && dimensions.height == profile.height
                && [kCVPixelFormatType_420YpCbCr8BiPlanarFullRange, kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange].contains(subtype)
                && format.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= Double(profile.rgbFPS) && $0.maxFrameRate >= Double(profile.rgbFPS) }
                && (!needsDepth || format.supportedDepthDataFormats.contains { depth in
                    [kCVPixelFormatType_DepthFloat16, kCVPixelFormatType_DepthFloat32].contains(CMFormatDescriptionGetMediaSubType(depth.formatDescription))
                        && depth.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= Double(profile.depthFPS) && $0.maxFrameRate >= Double(profile.depthFPS) }
                })
        }
        guard let selected = candidates.sorted(by: { a, b in
            let pa = CMFormatDescriptionGetMediaSubType(a.formatDescription)
            let pb = CMFormatDescriptionGetMediaSubType(b.formatDescription)
            if pa != pb { return pa == kCVPixelFormatType_420YpCbCr8BiPlanarFullRange }
            return a.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()! < b.videoSupportedFrameRateRanges.map(\.maxFrameRate).max()!
        }).first else { throw CaptureFailure.message("No 8-bit 1920×1080 60 fps MultiCam format for \(device.localizedName)\(needsDepth ? " with 30 fps LiDAR depth" : ""). See capabilities.json.") }
        return selected
    }

    private func configure() throws {
        let discovery = CapabilityProbe.discover()
        guard let main = discovery.devices.first(where: { $0.deviceType == .builtInLiDARDepthCamera }),
              let ultra = discovery.devices.first(where: { $0.deviceType == .builtInUltraWideCamera }) else {
            throw CaptureFailure.message("A rear LiDAR depth camera and ultra-wide camera are both required.")
        }
        let mainFormat = try format(for: main, needsDepth: true)
        let ultraFormat = try format(for: ultra, needsDepth: false)
        let fps = profile.rgbFPS
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        synchronizer?.setDelegate(nil, queue: nil)
        synchronizer = nil
        for connection in session.connections { session.removeConnection(connection) }
        for output in session.outputs { session.removeOutput(output) }
        for input in session.inputs { session.removeInput(input) }
        for (device, selected) in [(main, mainFormat), (ultra, ultraFormat)] {
            try device.lockForConfiguration()
            defer { device.unlockForConfiguration() }
            device.activeFormat = selected
            device.activeVideoMinFrameDuration = CMTime(value: 1, timescale: fps)
            device.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: fps)
            device.videoZoomFactor = 1
            if device.isGeometricDistortionCorrectionSupported { device.isGeometricDistortionCorrectionEnabled = false }
            if device.isFocusModeSupported(.continuousAutoFocus) { device.focusMode = .continuousAutoFocus }
            if device.isExposureModeSupported(.continuousAutoExposure) { device.exposureMode = .continuousAutoExposure }
            if device === main {
                let depths = selected.supportedDepthDataFormats.filter {
                    [kCVPixelFormatType_DepthFloat16, kCVPixelFormatType_DepthFloat32].contains(CMFormatDescriptionGetMediaSubType($0.formatDescription))
                        && $0.videoSupportedFrameRateRanges.contains { $0.minFrameRate <= Double(profile.depthFPS) && $0.maxFrameRate >= Double(profile.depthFPS) }
                }
                device.activeDepthDataFormat = depths.max { a, b in
                    let da = CMVideoFormatDescriptionGetDimensions(a.formatDescription)
                    let db = CMVideoFormatDescriptionGetDimensions(b.formatDescription)
                    return da.width * da.height < db.width * db.height
                }
                device.activeDepthDataMinFrameDuration = CMTime(value: 1, timescale: profile.depthFPS)
            }
        }
        let mainInput = try AVCaptureDeviceInput(device: main)
        let ultraInput = try AVCaptureDeviceInput(device: ultra)
        for input in [mainInput, ultraInput] {
            guard session.canAddInput(input) else { throw CaptureFailure.message("This phone rejects simultaneous LiDAR + ultra-wide inputs.") }
            session.addInputWithNoConnections(input)
            input.videoMinFrameDurationOverride = CMTime(value: 1, timescale: fps)
        }
        for output in [mainOutput, ultraOutput] {
            output.alwaysDiscardsLateVideoFrames = true
            guard session.canAddOutput(output) else { throw CaptureFailure.message("Cannot add both RGB outputs.") }
            session.addOutputWithNoConnections(output)
        }
        guard session.canAddOutput(depthOutput) else { throw CaptureFailure.message("Cannot add LiDAR output alongside both cameras.") }
        session.addOutputWithNoConnections(depthOutput)
        depthOutput.isFilteringEnabled = false
        depthOutput.alwaysDiscardsLateDepthData = true
        for (input, output) in [(mainInput, mainOutput), (ultraInput, ultraOutput)] {
            guard let port = input.ports.first(where: { $0.mediaType == .video }) else {
                throw CaptureFailure.message("Camera input has no video port.")
            }
            let connection = AVCaptureConnection(inputPorts: [port], output: output)
            guard session.canAddConnection(connection) else { throw CaptureFailure.message("Cannot connect a required RGB stream.") }
            session.addConnection(connection)
            if connection.isVideoStabilizationSupported { connection.preferredVideoStabilizationMode = .off }
            if connection.isVideoMirroringSupported { connection.automaticallyAdjustsVideoMirroring = false; connection.isVideoMirrored = false }
            if connection.isCameraIntrinsicMatrixDeliverySupported { connection.isCameraIntrinsicMatrixDeliveryEnabled = true }
            let supported = output.availableVideoPixelFormatTypes
            let selected = input === mainInput ? mainFormat : ultraFormat
            let pixelType = CMFormatDescriptionGetMediaSubType(selected.formatDescription)
            guard supported.contains(pixelType) else {
                throw CaptureFailure.message("Camera does not support the requested efficient 8-bit video buffers.")
            }
            output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: pixelType]
        }
        guard let depthPort = mainInput.ports.first(where: { $0.mediaType == .depthData }) else {
            throw CaptureFailure.message("LiDAR input did not expose a depth port.")
        }
        let depthConnection = AVCaptureConnection(inputPorts: [depthPort], output: depthOutput)
        guard session.canAddConnection(depthConnection) else { throw CaptureFailure.message("Cannot connect the required depth stream.") }
        session.addConnection(depthConnection)
        guard session.hardwareCost <= 1 else { throw CaptureFailure.message("Selected formats exceed this phone's MultiCam hardware budget (\(session.hardwareCost)).") }
        var extrinsics: Any = NSNull()
        if let physicalMain = discovery.devices.first(where: { $0.deviceType == .builtInWideAngleCamera }),
           let data = AVCaptureDevice.extrinsicMatrix(from: physicalMain, to: ultra),
           data.count == MemoryLayout<simd_float4x3>.size {
            extrinsics = CaptureMetadata.matrix4x3(data.withUnsafeBytes { $0.loadUnaligned(as: simd_float4x3.self) }, translationScale: 0.001)
        }
        configuration = [
            "requested_width": profile.width, "requested_height": profile.height,
            "requested_rgb_fps": fps, "requested_depth_fps": profile.depthFPS, "requested_imu_hz": 100,
            "video_bitrate_bps": profile.videoBitrate, "automatic_profile_fallback": false,
            "main": cameraConfiguration(main, output: mainOutput), "ultra": cameraConfiguration(ultra, output: ultraOutput),
            "depth_format": main.activeDepthDataFormat.map(CaptureMetadata.format) as Any? ?? NSNull(),
            "depth_filtering": depthOutput.isFilteringEnabled,
            "depth_min_frame_duration_s": main.activeDepthDataMinFrameDuration.seconds,
            "ultra_from_main_m": extrinsics,
            "extrinsics_equation": "X_ultra = R * X_main + t; t in metres; physical camera factory calibration, validate against recorded geometry",
            "hardware_cost": session.hardwareCost, "system_pressure_cost": session.systemPressureCost,
            "depth_rate_note": "Depth cadence is device/format dependent; inspect recorded timestamps.",
            "image_orientation": "Native sensor buffers, no rotation or mirror applied; UI preview only rotates 90 degrees."
        ]
        mainCamera = main
        ultraCamera = ultra
        synchronizer = AVCaptureDataOutputSynchronizer(dataOutputs: [mainOutput, depthOutput, ultraOutput])
        synchronizer?.setDelegate(self, queue: dataQueue)
        configured = true
    }

    private func cameraConfiguration(_ camera: AVCaptureDevice, output: AVCaptureVideoDataOutput) -> [String: Any] {
        let connection = output.connection(with: .video)
        return ["device_id": camera.uniqueID, "format": CaptureMetadata.format(camera.activeFormat),
                "intrinsics_delivery_enabled": connection?.isCameraIntrinsicMatrixDeliveryEnabled ?? false,
                "active_video_min_frame_duration_s": camera.activeVideoMinFrameDuration.seconds,
                "active_video_max_frame_duration_s": camera.activeVideoMaxFrameDuration.seconds,
                "stabilization_mode": connection?.activeVideoStabilizationMode.rawValue ?? -1,
                "state": CaptureMetadata.cameraState(camera)]
    }

    func startRecording() {
        dataQueue.async {
            guard self.status.phase == "ready", self.recorder == nil, !self.finalizing, self.clock != nil else { return }
            do {
                guard CaptureFiles.diskFreeBytes() > 2_000_000_000 else { throw CaptureFailure.message("At least 2 GB of free storage is required to start.") }
                self.recorder = try SessionRecorder(configuration: self.configuration, queue: self.dataQueue)
                self.status.sessionURL = self.recorder?.url
                self.status.elapsed = 0
                self.recordingStartedAt = ProcessInfo.processInfo.systemUptime
                self.lastSeen = [:]
                self.startMotion()
                self.publish(phase: "recording", message: "Recording all required streams locally.")
            } catch { self.publish(phase: "failed", message: error.localizedDescription) }
        }
    }

    private func startMotion() {
        motion.accelerometerUpdateInterval = 0.01
        motion.gyroUpdateInterval = 0.01
        motion.startAccelerometerUpdates(to: motionQueue) { [weak self] sample, error in
            guard let self else { return }
            do {
                if let error { throw error }
                if let sample {
                    try self.recorder?.motion(sensor: "accelerometer", timestamp: sample.timestamp, xyz: [sample.acceleration.x, sample.acceleration.y, sample.acceleration.z])
                    self.lastSeen["accelerometer"] = ProcessInfo.processInfo.systemUptime
                }
            } catch { self.stopOnDataQueue(reason: "motion_error", error: error.localizedDescription) }
        }
        motion.startGyroUpdates(to: motionQueue) { [weak self] sample, error in
            guard let self else { return }
            do {
                if let error { throw error }
                if let sample {
                    try self.recorder?.motion(sensor: "gyroscope", timestamp: sample.timestamp, xyz: [sample.rotationRate.x, sample.rotationRate.y, sample.rotationRate.z])
                    self.lastSeen["gyroscope"] = ProcessInfo.processInfo.systemUptime
                }
            } catch { self.stopOnDataQueue(reason: "motion_error", error: error.localizedDescription) }
        }
    }

    func stopRecording(reason: String = "user") { dataQueue.async { self.stopOnDataQueue(reason: reason, error: nil) } }

    private func stopOnDataQueue(reason: String, error: String?) {
        guard let recorder, !finalizing else { return }
        motion.stopAccelerometerUpdates()
        motion.stopGyroUpdates()
        publish()
        self.recorder = nil
        finalizing = true
        publish(phase: "finalizing", message: "Finalizing video segments… Keep the app open.")
        recorder.finish(reason: reason, error: error) { failure in
            self.finalizing = false
            self.publish(phase: failure == nil ? (self.suspended ? "idle" : "ready") : "failed",
                         message: failure ?? "Saved \(recorder.url.lastPathComponent). Export it from the session list.")
        }
    }

    func suspend() {
        dataQueue.async {
            self.suspended = true
            self.stopOnDataQueue(reason: "app_backgrounded", error: nil)
            self.sessionQueue.async {
                if self.session.isRunning { self.session.stopRunning() }
                self.dataQueue.async {
                    if !self.finalizing { self.publish(phase: "idle", message: "Capture paused. Prepare the cameras again to resume.") }
                }
            }
        }
    }

    private func publish(phase: String? = nil, message: String? = nil) {
        if let phase { status.phase = phase }
        if let message { status.message = message }
        if let recorder {
            status.counts = recorder.counts
            status.drops = recorder.drops
            if let first = recorder.firstHostNS, let last = recorder.lastHostNS { status.elapsed = Double(last - first) / 1e9 }
        }
        status.freeBytes = CaptureFiles.diskFreeBytes()
        status.liveFrames = liveFrames
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: status.thermal = "nominal"
        case .fair: status.thermal = "fair"
        case .serious: status.thermal = "serious"
        case .critical: status.thermal = "critical"
        @unknown default: status.thermal = "unknown"
        }
        let value = status
        DispatchQueue.main.async { self.onStatus?(value) }
    }

    private func hostTime(_ timestamp: CMTime) throws -> String {
        guard let clock else { throw CaptureFailure.message("Capture synchronization clock is unavailable.") }
        return try CaptureFiles.hostNanoseconds(CMSyncConvertTime(timestamp, from: clock, to: CMClockGetHostTimeClock()))
    }

    func dataOutputSynchronizer(_ synchronizer: AVCaptureDataOutputSynchronizer,
                                didOutput collection: AVCaptureSynchronizedDataCollection) {
        guard clock != nil else { return }
        do {
            let main = collection.synchronizedData(for: mainOutput)
            let ultra = collection.synchronizedData(for: ultraOutput)
            let depth = collection.synchronizedData(for: depthOutput)
            guard let timestamp = CaptureBatchTiming.earliest([main?.timestamp, ultra?.timestamp, depth?.timestamp]) else { return }
            if main == nil { secondaryOnlyCallbacks += 1 }
            let batchHost = try hostTime(timestamp)
            if let recorder { try recorder.beginBatch(hostNS: batchHost) }
            for (stream, output, camera) in [("main", mainOutput, mainCamera), ("ultra", ultraOutput, ultraCamera)] {
                guard let data = collection.synchronizedData(for: output) as? AVCaptureSynchronizedSampleBufferData else { continue }
                let host = try hostTime(data.timestamp)
                if data.sampleBufferWasDropped {
                    liveDrops[stream, default: 0] += 1
                    try recorder?.dropped(stream, reason: "avfoundation_\(data.droppedReason.rawValue)", hostNS: host)
                    continue
                }
                let sample = data.sampleBuffer
                let pts = CMSampleBufferGetPresentationTimeStamp(sample)
                let sampleHost = try hostTime(pts)
                if let camera { try recorder?.video(sample, stream: stream, timestamp: pts, hostNS: sampleHost, cameraState: CaptureMetadata.cameraState(camera)) }
                lastSeen[stream] = ProcessInfo.processInfo.systemUptime
                liveFrames[stream, default: 0] += 1
                lastSampleHostNS[stream] = sampleHost
                preview(sample, stream: stream, time: pts.seconds)
            }
            if let data = collection.synchronizedData(for: depthOutput) as? AVCaptureSynchronizedDepthData {
                let host = try hostTime(data.timestamp)
                if data.depthDataWasDropped {
                    liveDrops["depth", default: 0] += 1
                    try recorder?.dropped("depth", reason: "avfoundation_\(data.droppedReason.rawValue)", hostNS: host)
                }
                else {
                    try recorder?.depth(data.depthData, timestamp: data.timestamp, hostNS: host)
                    lastSeen["depth"] = ProcessInfo.processInfo.systemUptime
                    liveFrames["depth", default: 0] += 1
                    lastSampleHostNS["depth"] = host
                }
            }
        } catch {
            if recorder != nil { stopOnDataQueue(reason: "capture_error", error: error.localizedDescription) }
            else { publish(phase: "failed", message: error.localizedDescription) }
        }
    }

    private func heartbeat() {
        do {
            let now = ProcessInfo.processInfo.systemUptime
            if clock != nil, !suspended {
                let interval = now - lastRateUpdate
                if interval > 0 {
                    status.liveFPS = liveFrames.mapValues { Double($0) }
                    for stream in liveFrames.keys {
                        status.liveFPS[stream] = Double(liveFrames[stream, default: 0] - previousLiveFrames[stream, default: 0]) / interval
                    }
                }
                previousLiveFrames = liveFrames
                lastRateUpdate = now
                let missing = liveFrames.keys.filter { liveFrames[$0, default: 0] < 3 || now - (lastSeen[$0] ?? liveStartedAt) > 2 }.sorted()
                if status.phase == "checking", now - liveStartedAt >= 2 {
                    readyRateWindows = missing.isEmpty && profile.ratesMeetTarget(status.liveFPS) ? readyRateWindows + 1 : 0
                    if readyRateWindows >= 2 {
                        publish(phase: "ready", message: "1080p60 on both cameras and 30 fps LiDAR verified in preview. Ready to record.")
                    } else if now - liveStartedAt > 12 {
                        publish(phase: "failed", message: "Requested 60/60/30 fps was not sustained in preview. See live rates and device diagnostics.")
                    }
                }
                if ["checking", "ready"].contains(status.phase), now - liveStartedAt > 5, !missing.isEmpty {
                    publish(phase: "failed", message: "No live frames from: \(missing.joined(separator: ", ")). Device diagnostics saved.")
                }
                try CaptureFiles.writeJSON([
                    "schema": "iphone-rsa.stream-health/1", "host_ns": CaptureFiles.now(),
                    "app_build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
                    "phase": status.phase, "message": status.message,
                    "frames": liveFrames, "fps": status.liveFPS, "capture_drops": liveDrops,
                    "secondary_only_callbacks": secondaryOnlyCallbacks,
                    "last_sample_host_ns": lastSampleHostNS,
                    "last_seen_age_s": lastSeen.mapValues { now - $0 },
                    "preview_sample_pts_s": lastPreview,
                    "configuration": configuration, "thermal_state": ProcessInfo.processInfo.thermalState.rawValue,
                    "main_pressure": mainCamera?.systemPressureState.level.rawValue ?? "unknown",
                    "ultra_pressure": ultraCamera?.systemPressureState.level.rawValue ?? "unknown"
                ], to: CaptureFiles.streamHealth)
                publish()
                #if DEBUG
                if let seconds = deviceTestSeconds {
                    if status.phase == "ready", !deviceTestStarted {
                        deviceTestStarted = true
                        deviceTestDeadline = now + seconds
                        startRecording()
                    }
                    if status.phase == "recording", let deadline = deviceTestDeadline, now >= deadline {
                        stopOnDataQueue(reason: "device_test_duration_reached", error: nil)
                    }
                    try CaptureFiles.writeJSON([
                        "schema": "iphone-rsa.device-capture-test/1", "host_ns": CaptureFiles.now(),
                        "app_build": Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown",
                        "requested_duration_s": seconds, "phase": status.phase, "message": status.message,
                        "session": status.sessionURL?.lastPathComponent as Any? ?? NSNull(),
                        "counts": status.counts, "drops": status.drops, "configuration": configuration
                    ], to: CaptureFiles.documents.appendingPathComponent("capture-test.json"))
                }
                #endif
            }
            guard let recorder else { return }
            if now - recordingStartedAt > 5 {
                let stalled = recorder.counts.keys.filter { now - (lastSeen[$0] ?? recordingStartedAt) > 2 }.sorted()
                if !stalled.isEmpty {
                    throw CaptureFailure.message("Required streams stalled: \(stalled.joined(separator: ", ")). Partial data retained.")
                }
            }
            try recorder.event("telemetry", fields: ["thermal_state": ProcessInfo.processInfo.thermalState.rawValue,
                "free_bytes": CaptureFiles.diskFreeBytes(),
                "main_pressure": mainCamera?.systemPressureState.level.rawValue ?? "unknown",
                "ultra_pressure": ultraCamera?.systemPressureState.level.rawValue ?? "unknown"])
            try recorder.checkpoint()
            if CaptureFiles.diskFreeBytes() < 500_000_000 {
                throw CaptureFailure.message("Recording stopped with less than 500 MB available.")
            }
            publish()
        } catch { stopOnDataQueue(reason: "health_check_failed", error: error.localizedDescription) }
    }

    private func preview(_ sample: CMSampleBuffer, stream: String, time: Double) {
        guard time - (lastPreview[stream] ?? -.infinity) >= 0.5,
              let pixel = CMSampleBufferGetImageBuffer(sample) else { return }
        lastPreview[stream] = time
        let image = CIImage(cvPixelBuffer: pixel).oriented(.right)
        let factor = min(1, 480 / image.extent.width)
        let scaled = image.transformed(by: CGAffineTransform(scaleX: factor, y: factor))
        guard let cg = imageContext.createCGImage(scaled, from: scaled.extent) else { return }
        let uiImage = UIImage(cgImage: cg)
        DispatchQueue.main.async { self.onPreview?(stream, uiImage) }
    }
}
