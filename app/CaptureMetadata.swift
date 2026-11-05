import AVFoundation
import CoreMotion
import UIKit
import simd

enum CaptureFailure: LocalizedError {
    case message(String)
    var errorDescription: String? {
        switch self { case .message(let text): return text }
    }
}

enum CaptureFiles {
    static var documents: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    }
    static var sessions: URL { documents.appendingPathComponent("Sessions", isDirectory: true) }
    static var probe: URL { documents.appendingPathComponent("capabilities.json") }
    static var streamHealth: URL { documents.appendingPathComponent("stream-health.json") }

    static func writeJSON(_ value: [String: Any], to url: URL) throws {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: .atomic)
    }

    static func hostNanoseconds(_ time: CMTime) throws -> String {
        guard time.isValid, time.isNumeric else {
            throw CaptureFailure.message("Capture timestamp could not be mapped to the host clock.")
        }
        return String(CMTimeConvertScale(time, timescale: 1_000_000_000, method: .roundHalfAwayFromZero).value)
    }

    static func now() -> String {
        // A valid host clock read cannot fail conversion.
        (try? hostNanoseconds(CMClockGetTime(CMClockGetHostTimeClock()))) ?? "0"
    }

    static func diskFreeBytes() -> Int64 {
        let values = try? documents.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage ?? 0
    }
}

final class JSONLines {
    private let handle: FileHandle
    private var closed = false
    init(_ url: URL) throws {
        guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
            throw CaptureFailure.message("Cannot create \(url.lastPathComponent).")
        }
        handle = try FileHandle(forWritingTo: url)
    }
    func append(_ value: [String: Any]) throws {
        var data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        data.append(0x0A)
        try handle.write(contentsOf: data)
    }
    func close() throws {
        guard !closed else { return }
        closed = true
        try handle.synchronize()
        try handle.close()
    }
    deinit { try? handle.close() }
}

enum CaptureMetadata {
    static func matrix3(_ m: simd_float3x3) -> [[Float]] {
        (0..<3).map { row in (0..<3).map { col in m[col][row] } }
    }
    static func matrix4x3(_ m: simd_float4x3, translationScale: Float) -> [[Float]] {
        (0..<3).map { row in
            (0..<4).map { col in m[col][row] * (col == 3 ? translationScale : 1) }
        }
    }
    static func format(_ format: AVCaptureDevice.Format) -> [String: Any] {
        let description = format.formatDescription
        let dimensions = CMVideoFormatDescriptionGetDimensions(description)
        return [
            "width": dimensions.width, "height": dimensions.height,
            "pixel_format": CMFormatDescriptionGetMediaSubType(description),
            "multicam_supported": format.isMultiCamSupported,
            "field_of_view_degrees": format.videoFieldOfView,
            "frame_rate_ranges": format.videoSupportedFrameRateRanges.map {
                ["min": $0.minFrameRate, "max": $0.maxFrameRate]
            }
        ]
    }
    static func calibration(_ value: AVCameraCalibrationData) -> [String: Any] {
        [
            "intrinsics": matrix3(value.intrinsicMatrix),
            "reference_width": value.intrinsicMatrixReferenceDimensions.width,
            "reference_height": value.intrinsicMatrixReferenceDimensions.height,
            "extrinsics_reference_from_camera_m": matrix4x3(value.extrinsicMatrix, translationScale: 0.001),
            "distortion_center": [value.lensDistortionCenter.x, value.lensDistortionCenter.y],
            "distortion_lookup_f32le_base64": value.lensDistortionLookupTable?.base64EncodedString() as Any? ?? NSNull(),
            "inverse_distortion_lookup_f32le_base64": value.inverseLensDistortionLookupTable?.base64EncodedString() as Any? ?? NSNull(),
            "pixel_size_mm": value.pixelSize
        ]
    }
    static func intrinsics(_ sample: CMSampleBuffer) -> Any {
        guard let data = CMGetAttachment(sample, key: kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix,
                                         attachmentModeOut: nil) as? Data,
              data.count == MemoryLayout<simd_float3x3>.size else { return NSNull() }
        return matrix3(data.withUnsafeBytes { $0.loadUnaligned(as: simd_float3x3.self) })
    }
    static func cameraState(_ device: AVCaptureDevice) -> [String: Any] {
        ["exposure_duration_s": device.exposureDuration.seconds, "iso": device.iso,
         "lens_position": device.lensPosition, "zoom_factor": device.videoZoomFactor,
         "geometric_distortion_correction": device.isGeometricDistortionCorrectionEnabled,
         "focus_mode": device.focusMode.rawValue, "exposure_mode": device.exposureMode.rawValue]
    }
    static func deviceIdentity() -> [String: Any] {
        var system = utsname()
        uname(&system)
        let machine = withUnsafeBytes(of: &system.machine) { raw in
            String(decoding: raw.prefix(while: { $0 != 0 }), as: UTF8.self)
        }
        return ["hardware": machine, "os": ProcessInfo.processInfo.operatingSystemVersionString,
                "app_version": Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"]
    }
}

enum CaptureBatchTiming {
    /// Secondary cameras/depth may arrive in callbacks with no primary-camera sample.
    /// Pick a real timestamp from any delivered output rather than dropping that callback.
    static func earliest(_ timestamps: [CMTime?]) -> CMTime? {
        timestamps.compactMap { $0 }.filter { $0.isValid && $0.isNumeric }
            .min { CMTimeCompare($0, $1) < 0 }
    }
}

enum CapabilityProbe {
    static let types: [AVCaptureDevice.DeviceType] = [.builtInLiDARDepthCamera, .builtInWideAngleCamera, .builtInUltraWideCamera]
    static func discover() -> AVCaptureDevice.DiscoverySession {
        AVCaptureDevice.DiscoverySession(deviceTypes: types, mediaType: .video, position: .back)
    }
    static func report() -> [String: Any] {
        let discovery = discover()
        let motion = CMMotionManager()
        #if targetEnvironment(simulator)
        let environment = "simulator"
        let recordingSupported = false
        #else
        let environment = "physical_device"
        let recordingSupported = AVCaptureMultiCamSession.isMultiCamSupported
        #endif
        return [
            "schema": "iphone-rsa.capabilities/1", "created_at": ISO8601DateFormatter().string(from: Date()),
            "runtime_environment": environment, "recording_supported": recordingSupported,
            "device": CaptureMetadata.deviceIdentity(), "multicam_supported": AVCaptureMultiCamSession.isMultiCamSupported,
            "accelerometer_available": motion.isAccelerometerAvailable, "gyroscope_available": motion.isGyroAvailable,
            "supported_device_sets": discovery.supportedMultiCamDeviceSets.map { $0.map(\.uniqueID).sorted() },
            "devices": discovery.devices.map { device -> [String: Any] in
                ["id": device.uniqueID, "name": device.localizedName, "type": device.deviceType.rawValue,
                 "virtual": device.isVirtualDevice,
                 "formats": device.formats.enumerated().map { index, value -> [String: Any] in
                     var item = CaptureMetadata.format(value)
                     item["index"] = index
                     item["depth_formats"] = value.supportedDepthDataFormats.map(CaptureMetadata.format)
                     return item
                 }]
            },
            "limitations": ["No ARKit world pose is recorded.", "Factory camera-to-IMU calibration is not available.",
                            "A successful probe is not a sustained recording test."]
        ]
    }
}
