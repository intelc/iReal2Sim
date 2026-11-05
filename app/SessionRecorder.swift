import AVFoundation
import Foundation

/// Confined to CaptureService's serial data queue. No frame buffers are queued by the app.
final class SessionRecorder: @unchecked Sendable {
    let url: URL
    private let queue: DispatchQueue
    private let events: JSONLines
    private let imu: JSONLines
    private var manifest: [String: Any]
    private var segment: RecordingSegment?
    private var segments: [RecordingSegment] = []
    private let finalizations = DispatchGroup()
    private var stopped = false
    private let rgbFPS: Int
    private let videoBitrate: Int
    private(set) var counts = ["main": 0, "ultra": 0, "depth": 0, "accelerometer": 0, "gyroscope": 0]
    private(set) var drops = ["main": 0, "ultra": 0, "depth": 0]
    private(set) var firstHostNS: Int64?
    private(set) var lastHostNS: Int64?
    static let segmentSeconds: Double = 30

    init(configuration: [String: Any], queue: DispatchQueue) throws {
        self.queue = queue
        rgbFPS = (configuration["requested_rgb_fps"] as? NSNumber)?.intValue ?? 30
        videoBitrate = (configuration["video_bitrate_bps"] as? NSNumber)?.intValue ?? 10_000_000
        let date = ISO8601DateFormatter().string(from: Date()).replacingOccurrences(of: ":", with: "-")
        url = CaptureFiles.sessions.appendingPathComponent("\(date)-\(UUID().uuidString.prefix(8))", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        events = try JSONLines(url.appendingPathComponent("events.jsonl"))
        imu = try JSONLines(url.appendingPathComponent("imu.jsonl"))
        manifest = [
            "schema": "iphone-rsa.session/1", "status": "recording",
            "created_at": ISO8601DateFormatter().string(from: Date()), "start_host_ns": CaptureFiles.now(),
            "device": CaptureMetadata.deviceIdentity(), "configuration": configuration,
            "required_streams": ["main", "ultra", "depth", "accelerometer", "gyroscope"],
            "segment_duration_target_s": Self.segmentSeconds, "segment_count": 0,
            "clock": ["host_ns": "CMClockGetHostTimeClock nanoseconds; decimal strings",
                      "capture_pts": "Original AVCaptureSession synchronizationClock CMTime",
                      "imu": "Core Motion seconds since boot, mapped by timestamp * 1e9; validate sensor latency experimentally"],
            "geometry": ["matrix_layout": "row-major nested arrays", "length_unit": "metre",
                         "depth": "Apple RGB-assisted depth in metres, distorted/aligned to accompanying main RGB",
                         "world_pose": "not_recorded", "camera_to_imu": "not_calibrated"],
            "depth_encoding": "tightly packed little-endian float32, row-major, native invalid values preserved"
        ]
        try saveManifest()
        try event("recording_started")
    }

    private func saveManifest() throws {
        manifest["counts"] = counts
        manifest["drops"] = drops
        manifest["segment_count"] = segments.count
        try CaptureFiles.writeJSON(manifest, to: url.appendingPathComponent("manifest.json"))
    }

    func checkpoint() throws { try saveManifest() }

    func event(_ kind: String, fields: [String: Any] = [:]) throws {
        var row = fields
        row["event"] = kind
        row["host_ns"] = CaptureFiles.now()
        try events.append(row)
    }

    func dropped(_ stream: String, reason: String, hostNS: String? = nil) throws {
        drops[stream, default: 0] += 1
        try event("sample_dropped", fields: ["stream": stream, "reason": reason,
                                            "sample_host_ns": hostNS as Any? ?? NSNull()])
    }

    /// Rotate at an actual delivered batch timestamp; secondary-only batches are valid.
    func beginBatch(hostNS: String) throws {
        guard !stopped, let timestamp = Int64(hostNS) else { return }
        if let current = segment,
           Double(timestamp - current.startHostNS) / 1e9 >= Self.segmentSeconds {
            finalize(current)
            segment = nil
        }
        if segment == nil {
            guard segments.filter({ !$0.finalized }).count < 3 else {
                throw CaptureFailure.message("Video finalization is falling behind; recording stopped to bound memory use.")
            }
            let next = try RecordingSegment(parent: url, index: segments.count, hostNS: timestamp, fps: rgbFPS, bitrate: videoBitrate)
            segment = next
            segments.append(next)
            try saveManifest()
        }
    }

    func video(_ sample: CMSampleBuffer, stream: String, timestamp: CMTime, hostNS: String,
               cameraState: [String: Any]) throws {
        guard !stopped, let segment else { return }
        if try segment.video(sample, stream: stream, timestamp: timestamp, hostNS: hostNS, cameraState: cameraState) {
            counts[stream, default: 0] += 1
            noteTimestamp(hostNS)
        } else {
            try dropped(stream, reason: "encoder_backpressure", hostNS: hostNS)
        }
    }

    func depth(_ value: AVDepthData, timestamp: CMTime, hostNS: String) throws {
        guard !stopped, let segment else { return }
        try segment.depth(value, timestamp: timestamp, hostNS: hostNS)
        counts["depth", default: 0] += 1
        noteTimestamp(hostNS)
    }

    func motion(sensor: String, timestamp: TimeInterval, xyz: [Double]) throws {
        guard !stopped else { return }
        try imu.append(["sensor": sensor, "sensor_time_s": timestamp,
                        "host_ns": String(Int64((timestamp * 1e9).rounded())), "xyz": xyz,
                        "unit": sensor == "accelerometer" ? "g" : "rad/s"])
        counts[sensor, default: 0] += 1
    }

    private func noteTimestamp(_ text: String) {
        guard let value = Int64(text) else { return }
        firstHostNS = min(firstHostNS ?? value, value)
        lastHostNS = max(lastHostNS ?? value, value)
    }

    private func finalize(_ segment: RecordingSegment) {
        finalizations.enter()
        segment.finish(on: queue) { self.finalizations.leave() }
    }

    func finish(reason: String, error: String?, completion: @escaping (String?) -> Void) {
        guard !stopped else { return }
        stopped = true
        var closingError = error
        do {
            try event("recording_stopped", fields: ["reason": reason, "error": error as Any? ?? NSNull()])
            try imu.close()
            try events.close()
            manifest["status"] = "finalizing"
            try saveManifest()
        } catch { closingError = closingError ?? error.localizedDescription }
        if let segment { finalize(segment) }
        self.segment = nil
        let capturedError = closingError
        finalizations.notify(queue: queue) {
            let failure = capturedError ?? self.segments.compactMap(\.failure).first
            let missing = self.counts.filter { $0.value == 0 }.keys.sorted()
            let resultError = failure ?? (missing.isEmpty ? nil : "Missing required streams: \(missing.joined(separator: ", "))")
            self.manifest["status"] = resultError == nil ? "complete" : "failed"
            self.manifest["stop_reason"] = reason
            self.manifest["error"] = resultError as Any? ?? NSNull()
            self.manifest["end_host_ns"] = CaptureFiles.now()
            self.manifest["first_sample_host_ns"] = self.firstHostNS.map(String.init) as Any? ?? NSNull()
            self.manifest["last_sample_host_ns"] = self.lastHostNS.map(String.init) as Any? ?? NSNull()
            do {
                try self.saveManifest()
                completion(resultError)
            } catch { completion(error.localizedDescription) }
        }
    }
}

private final class RecordingSegment {
    let startHostNS: Int64
    private let url: URL
    private let frames: JSONLines
    private let depths: JSONLines
    private let depthFile: FileHandle
    private var depthOffset: UInt64 = 0
    private var videos: [String: VideoFile] = [:]
    private var depthCount = 0
    private let fps: Int
    private let bitrate: Int
    private(set) var finalized = false
    private(set) var failure: String?

    init(parent: URL, index: Int, hostNS: Int64, fps: Int, bitrate: Int) throws {
        startHostNS = hostNS
        self.fps = fps
        self.bitrate = bitrate
        url = parent.appendingPathComponent(String(format: "segment-%04d", index), isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: false)
        frames = try JSONLines(url.appendingPathComponent("frames.jsonl"))
        depths = try JSONLines(url.appendingPathComponent("depth.jsonl"))
        let depthURL = url.appendingPathComponent("depth.f32")
        guard FileManager.default.createFile(atPath: depthURL.path, contents: nil) else {
            throw CaptureFailure.message("Cannot create depth payload.")
        }
        depthFile = try FileHandle(forWritingTo: depthURL)
        try metadata(status: "recording")
    }

    private func metadata(status: String) throws {
        try CaptureFiles.writeJSON([
            "schema": "iphone-rsa.segment/1", "status": status, "start_host_ns": String(startHostNS),
            "depth_count": depthCount, "depth_bytes": depthOffset,
            "videos": videos.mapValues { $0.metadata }, "error": failure as Any? ?? NSNull()
        ], to: url.appendingPathComponent("segment.json"))
    }

    func video(_ sample: CMSampleBuffer, stream: String, timestamp: CMTime, hostNS: String,
               cameraState: [String: Any]) throws -> Bool {
        guard let format = CMSampleBufferGetFormatDescription(sample) else {
            throw CaptureFailure.message("Video sample has no format description.")
        }
        if videos[stream] == nil {
            videos[stream] = try VideoFile(url: url.appendingPathComponent("\(stream).mov"), format: format, origin: timestamp, fps: fps, bitrate: bitrate)
        }
        let video = videos[stream]!
        guard try video.append(sample) else { return false }
        let dimensions = CMVideoFormatDescriptionGetDimensions(format)
        try frames.append([
            "stream": stream, "frame_index": video.count - 1, "host_ns": hostNS,
            "capture_pts_value": String(timestamp.value), "capture_pts_timescale": timestamp.timescale,
            "media_time_s": CMTimeSubtract(timestamp, video.origin).seconds,
            "width": dimensions.width, "height": dimensions.height,
            "intrinsics": CaptureMetadata.intrinsics(sample), "camera": cameraState
        ])
        return true
    }

    func depth(_ value: AVDepthData, timestamp: CMTime, hostNS: String) throws {
        let converted = value.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32)
        let buffer = converted.depthDataMap
        let width = CVPixelBufferGetWidth(buffer), height = CVPixelBufferGetHeight(buffer)
        let bytes = try DepthPayload.pack(buffer)
        try depthFile.write(contentsOf: bytes)
        try depths.append([
            "frame_index": depthCount, "host_ns": hostNS,
            "capture_pts_value": String(timestamp.value), "capture_pts_timescale": timestamp.timescale,
            "offset_bytes": depthOffset, "length_bytes": bytes.count, "width": width, "height": height,
            "filtered": converted.isDepthDataFiltered,
            "accuracy": converted.depthDataAccuracy == .absolute ? "absolute" : "relative",
            "quality": converted.depthDataQuality.rawValue,
            "calibration": converted.cameraCalibrationData.map(CaptureMetadata.calibration) as Any? ?? NSNull()
        ])
        depthOffset += UInt64(bytes.count)
        depthCount += 1
    }

    func finish(on queue: DispatchQueue, completion: @escaping () -> Void) {
        do {
            try depthFile.synchronize()
            try depthFile.close()
            try frames.close()
            try depths.close()
            try metadata(status: "finalizing")
        } catch { failure = error.localizedDescription }
        let group = DispatchGroup()
        for video in videos.values {
            group.enter()
            video.finish { group.leave() }
        }
        group.notify(queue: queue) {
            self.failure = self.failure ?? self.videos.values.compactMap(\.failure).first
            self.finalized = true
            do { try self.metadata(status: self.failure == nil ? "complete" : "failed") }
            catch { self.failure = error.localizedDescription }
            completion()
        }
    }
}

enum DepthPayload {
    static func pack(_ buffer: CVPixelBuffer) throws -> Data {
        guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_DepthFloat32 else {
            throw CaptureFailure.message("Depth payload requires native float32 metres.")
        }
        guard CVPixelBufferLockBaseAddress(buffer, .readOnly) == kCVReturnSuccess else {
            throw CaptureFailure.message("Cannot lock depth pixels.")
        }
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(buffer) else { throw CaptureFailure.message("Depth buffer is empty.") }
        let height = CVPixelBufferGetHeight(buffer)
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let rowBytes = CVPixelBufferGetWidth(buffer) * MemoryLayout<Float32>.size
        var bytes = Data(capacity: rowBytes * height)
        for row in 0..<height {
            bytes.append(base.advanced(by: row * stride).assumingMemoryBound(to: UInt8.self), count: rowBytes)
        }
        return bytes
    }
}

private final class VideoFile {
    let origin: CMTime
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let width: Int32
    private let height: Int32
    private let fps: Int
    private let bitrate: Int
    private(set) var count = 0
    var failure: String? { writer.error?.localizedDescription ?? (writer.status == .completed ? nil : "Video writer did not complete (status \(writer.status.rawValue)).") }
    var metadata: [String: Any] {
        ["frame_count": count, "origin_capture_pts_value": String(origin.value),
         "origin_capture_pts_timescale": origin.timescale, "width": width, "height": height,
         "codec": "hevc", "container": "mov", "rotation_applied": false,
         "expected_source_fps": fps, "target_bitrate_bps": bitrate]
    }
    init(url: URL, format: CMFormatDescription, origin: CMTime, fps: Int, bitrate: Int) throws {
        self.origin = origin
        self.fps = fps
        self.bitrate = bitrate
        let dimensions = CMVideoFormatDescriptionGetDimensions(format)
        width = dimensions.width
        height = dimensions.height
        writer = try AVAssetWriter(outputURL: url, fileType: .mov)
        input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.hevc,
            AVVideoWidthKey: Int(width), AVVideoHeightKey: Int(height),
            AVVideoCompressionPropertiesKey: [AVVideoAverageBitRateKey: bitrate,
                                            AVVideoExpectedSourceFrameRateKey: fps,
                                            AVVideoAllowFrameReorderingKey: false,
                                            AVVideoMaxKeyFrameIntervalKey: fps]
        ], sourceFormatHint: format)
        input.expectsMediaDataInRealTime = true
        guard writer.canAdd(input) else { throw CaptureFailure.message("HEVC writer cannot accept this camera format.") }
        writer.add(input)
        guard writer.startWriting() else { throw CaptureFailure.message(writer.error?.localizedDescription ?? "Cannot start video writer.") }
        writer.startSession(atSourceTime: origin)
    }
    func append(_ sample: CMSampleBuffer) throws -> Bool {
        guard writer.status == .writing else { throw CaptureFailure.message(writer.error?.localizedDescription ?? "Video encoder stopped.") }
        guard let format = CMSampleBufferGetFormatDescription(sample) else { return false }
        let dimensions = CMVideoFormatDescriptionGetDimensions(format)
        guard dimensions.width == width, dimensions.height == height else {
            throw CaptureFailure.message("Camera changed resolution during recording.")
        }
        guard input.isReadyForMoreMediaData else { return false }
        guard input.append(sample) else { throw CaptureFailure.message(writer.error?.localizedDescription ?? "Video encoder rejected a frame.") }
        count += 1
        return true
    }
    func finish(completion: @escaping () -> Void) {
        guard writer.status == .writing else { completion(); return }
        input.markAsFinished()
        writer.finishWriting(completionHandler: completion)
    }
}
