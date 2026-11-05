import AVFoundation
import XCTest
@testable import SceneCapture

final class RecordingTests: XCTestCase {
    func testPreviewRateGateRejectsThirtyFPSWhenSixtyIsRequested() {
        let profile = CaptureProfile.requested
        XCTAssertFalse(profile.ratesMeetTarget(["main": 30, "ultra": 30, "depth": 30]))
        XCTAssertFalse(profile.ratesMeetTarget(["main": 60, "ultra": 0, "depth": 30]))
        XCTAssertFalse(profile.ratesMeetTarget(["main": 60, "ultra": 60, "depth": 15]))
        XCTAssertTrue(profile.ratesMeetTarget(["main": 59.94, "ultra": 59.94, "depth": 29.97]))
    }

    func testSixtyFPSVideoPreservesBothStreamsPTSAndEncoderMetadata() async throws {
        let queue = DispatchQueue(label: "recording.60fps.test")
        let recorder = try SessionRecorder(configuration: ["requested_rgb_fps": 60, "video_bitrate_bps": 24_000_000], queue: queue)
        defer { try? FileManager.default.removeItem(at: recorder.url) }
        try queue.sync {
            for frame in 0..<12 {
                let time = CMTime(value: 6000 + Int64(frame), timescale: 60)
                let host = try CaptureFiles.hostNanoseconds(time)
                try recorder.beginBatch(hostNS: host)
                for stream in ["main", "ultra"] {
                    try recorder.video(sample(time: time, fps: 60), stream: stream, timestamp: time,
                                       hostNS: host, cameraState: [:])
                }
                Thread.sleep(forTimeInterval: 0.018)
            }
        }
        let done = expectation(description: "60 fps movies finalized")
        queue.async { recorder.finish(reason: "test", error: nil) { _ in done.fulfill() } }
        await fulfillment(of: [done], timeout: 30)
        let folder = recorder.url.appendingPathComponent("segment-0000")
        let metadata = try json(folder.appendingPathComponent("segment.json"))
        let videos = try XCTUnwrap(metadata["videos"] as? [String: [String: Any]])
        for stream in ["main", "ultra"] {
            XCTAssertEqual(videos[stream]?["expected_source_fps"] as? Int, 60)
            XCTAssertEqual(videos[stream]?["target_bitrate_bps"] as? Int, 24_000_000)
            XCTAssertEqual(videos[stream]?["frame_count"] as? Int, 12)
            let asset = AVURLAsset(url: folder.appendingPathComponent("\(stream).mov"))
            let tracks = try await asset.loadTracks(withMediaType: .video)
            let reader = try AVAssetReader(asset: asset)
            let output = AVAssetReaderTrackOutput(track: try XCTUnwrap(tracks.first), outputSettings: [
                kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
            reader.add(output)
            XCTAssertTrue(reader.startReading())
            for frame in 0..<12 {
                let decoded = try XCTUnwrap(output.copyNextSampleBuffer())
                XCTAssertEqual(CMSampleBufferGetPresentationTimeStamp(decoded).seconds, Double(frame) / 60, accuracy: 0.001)
            }
            XCTAssertNil(output.copyNextSampleBuffer())
            XCTAssertEqual(reader.status, .completed)
        }
    }

    func testSecondaryOnlyCallbacksHaveUsableBatchTimestamps() throws {
        let time = CMTime(value: 1005, timescale: 1000)
        XCTAssertEqual(try XCTUnwrap(CaptureBatchTiming.earliest([nil, time, nil])), time)
        XCTAssertEqual(try XCTUnwrap(CaptureBatchTiming.earliest([nil, nil, time])), time)
        XCTAssertEqual(try XCTUnwrap(CaptureBatchTiming.earliest([.invalid, time, nil])), time)
        XCTAssertNil(CaptureBatchTiming.earliest([nil, .invalid, .indefinite]))
    }

    func testMixedBatchUsesEarliestDeliveredTimestamp() throws {
        let main = CMTime(value: 1000, timescale: 1000)
        let ultra = CMTime(value: 1005, timescale: 1000)
        XCTAssertEqual(try XCTUnwrap(CaptureBatchTiming.earliest([main, ultra, main])), main)
        XCTAssertEqual(try XCTUnwrap(CaptureBatchTiming.earliest([ultra, main, nil])), main)
    }

    func testCalibrationConversionKeepsRowsAndConvertsMillimetres() {
        let source = simd_float4x3(SIMD3(1, 2, 3), SIMD3(4, 5, 6), SIMD3(7, 8, 9), SIMD3(10, 20, 30))
        let result = CaptureMetadata.matrix4x3(source, translationScale: 0.001)
        XCTAssertEqual(result[0][0], 1)
        XCTAssertEqual(result[0][1], 4)
        XCTAssertEqual(result[2][2], 9)
        XCTAssertEqual(result[0][3], 0.01, accuracy: 1e-6)
        XCTAssertEqual(result[2][3], 0.03, accuracy: 1e-6)
    }

    func testHostClockNanosecondsPreserveIntegerPrecision() throws {
        XCTAssertEqual(try CaptureFiles.hostNanoseconds(CMTime(value: 12345678901234567, timescale: 1_000_000_000)),
                       "12345678901234567")
        XCTAssertThrowsError(try CaptureFiles.hostNanoseconds(.invalid))
    }

    func testSimulatorProbeReportsUnavailableHardware() async throws {
        #if targetEnvironment(simulator)
        let service = CaptureService()
        let done = expectation(description: "Unsupported device reported")
        service.onStatus = { value in
            if value.phase == "failed" {
                XCTAssertTrue(value.message.contains("Simulator capture is disabled"))
                done.fulfill()
            }
        }
        service.prepare()
        await fulfillment(of: [done], timeout: 15)
        let report = try json(CaptureFiles.probe)
        XCTAssertEqual(report["schema"] as? String, "iphone-rsa.capabilities/1")
        XCTAssertEqual(report["recording_supported"] as? Bool, false)
        XCTAssertEqual(report["runtime_environment"] as? String, "simulator")
        #endif
    }

    func testDepthPackingExcludesPaddingAndPreservesInvalidPixels() throws {
        var raw: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 3, 2, kCVPixelFormatType_DepthFloat32,
            [kCVPixelBufferBytesPerRowAlignmentKey: 64] as CFDictionary, &raw), kCVReturnSuccess)
        let buffer = try XCTUnwrap(raw)
        CVPixelBufferLockBaseAddress(buffer, [])
        let base = try XCTUnwrap(CVPixelBufferGetBaseAddress(buffer))
        let stride = CVPixelBufferGetBytesPerRow(buffer)
        let rows: [[Float32]] = [[1, .nan, 0], [2, 3, 4]]
        for row in 0..<2 {
            memset(base.advanced(by: row * stride), 0xFF, stride)
            let pointer = base.advanced(by: row * stride).assumingMemoryBound(to: Float32.self)
            for column in 0..<3 { pointer[column] = rows[row][column] }
        }
        CVPixelBufferUnlockBaseAddress(buffer, [])
        let packed = try DepthPayload.pack(buffer)
        XCTAssertEqual(packed.count, 24)
        let values = packed.withUnsafeBytes { Array($0.bindMemory(to: Float32.self)) }
        XCTAssertEqual(values[0], 1)
        XCTAssertTrue(values[1].isNaN)
        XCTAssertEqual(values[2], 0)
        XCTAssertEqual(Array(values[3...]), [2, 3, 4])
    }

    func testRGBIntrinsicsAttachmentRetainsMatrixLayout() throws {
        let buffer = try sample(time: CMTime(value: 1, timescale: 30))
        var intrinsics = simd_float3x3(SIMD3(500, 0, 0), SIMD3(0, 510, 0), SIMD3(320, 240, 1))
        let bytes = withUnsafeBytes(of: &intrinsics) { Data($0) }
        CMSetAttachment(buffer, key: kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix,
                        value: bytes as CFData, attachmentMode: kCMAttachmentMode_ShouldPropagate)
        let result = try XCTUnwrap(CaptureMetadata.intrinsics(buffer) as? [[Float]])
        XCTAssertEqual(result, [[500, 0, 320], [0, 510, 240], [0, 0, 1]])
    }

    func testSegmentRotationFinalizesBothMoviesAndPreservesPTS() async throws {
        let queue = DispatchQueue(label: "recording.test")
        let recorder = try SessionRecorder(configuration: [:], queue: queue)
        defer { try? FileManager.default.removeItem(at: recorder.url) }
        try queue.sync {
            for origin in [100.0, 131.0] {
                for frame in 0..<3 {
                    let time = origin + Double(frame) / 30
                    let host = String(Int64(time * 1e9))
                    try recorder.beginBatch(hostNS: host)
                    for (stream, delta) in [("main", 0.0), ("ultra", 0.005)] {
                        let timestamp = CMTime(seconds: time + delta, preferredTimescale: 1_000_000_000)
                        let buffer = try sample(time: timestamp)
                        try recorder.video(buffer, stream: stream, timestamp: timestamp,
                                           hostNS: try CaptureFiles.hostNanoseconds(timestamp), cameraState: [:])
                    }
                    try recorder.motion(sensor: "accelerometer", timestamp: time, xyz: [0, 0, 1])
                    try recorder.motion(sensor: "gyroscope", timestamp: time, xyz: [0, 0, 0])
                }
            }
        }
        let done = expectation(description: "All segments finalized")
        queue.async { recorder.finish(reason: "test", error: nil) { _ in done.fulfill() } }
        await fulfillment(of: [done], timeout: 30)
        let manifest = try json(recorder.url.appendingPathComponent("manifest.json"))
        // This fixture deliberately has no LiDAR. Missing required data must not be called complete.
        XCTAssertEqual(manifest["status"] as? String, "failed")
        XCTAssertTrue((manifest["error"] as? String ?? "").contains("depth"))
        XCTAssertEqual(manifest["segment_count"] as? Int, 2)
        for index in 0..<2 {
            let folder = recorder.url.appendingPathComponent(String(format: "segment-%04d", index))
            let metadata = try json(folder.appendingPathComponent("segment.json"))
            XCTAssertEqual(metadata["status"] as? String, "complete")
            let lines = try String(contentsOf: folder.appendingPathComponent("frames.jsonl"), encoding: .utf8).split(separator: "\n")
            XCTAssertEqual(lines.count, 6)
            for stream in ["main", "ultra"] {
                let asset = AVURLAsset(url: folder.appendingPathComponent("\(stream).mov"))
                let tracks = try await asset.loadTracks(withMediaType: .video)
                XCTAssertEqual(tracks.count, 1)
                let reader = try AVAssetReader(asset: asset)
                let output = AVAssetReaderTrackOutput(track: try XCTUnwrap(tracks.first), outputSettings: [
                    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA
                ])
                reader.add(output)
                XCTAssertTrue(reader.startReading())
                for frame in 0..<3 {
                    let decoded = try XCTUnwrap(output.copyNextSampleBuffer())
                    XCTAssertEqual(CMSampleBufferGetPresentationTimeStamp(decoded).seconds, Double(frame) / 30, accuracy: 0.002)
                }
                XCTAssertNil(output.copyNextSampleBuffer())
                XCTAssertEqual(reader.status, .completed)
            }
        }
    }

    private func json(_ url: URL) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])
    }

    private func sample(time: CMTime, fps: Int32 = 30) throws -> CMSampleBuffer {
        var pixels: CVPixelBuffer?
        XCTAssertEqual(CVPixelBufferCreate(kCFAllocatorDefault, 64, 64, kCVPixelFormatType_420YpCbCr8BiPlanarFullRange,
            [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &pixels), kCVReturnSuccess)
        let image = try XCTUnwrap(pixels)
        CVPixelBufferLockBaseAddress(image, [])
        for plane in 0..<CVPixelBufferGetPlaneCount(image) {
            if let base = CVPixelBufferGetBaseAddressOfPlane(image, plane) {
                memset(base, 128, CVPixelBufferGetBytesPerRowOfPlane(image, plane) * CVPixelBufferGetHeightOfPlane(image, plane))
            }
        }
        CVPixelBufferUnlockBaseAddress(image, [])
        var format: CMVideoFormatDescription?
        XCTAssertEqual(CMVideoFormatDescriptionCreateForImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: image,
                                                                  formatDescriptionOut: &format), noErr)
        var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: fps), presentationTimeStamp: time, decodeTimeStamp: .invalid)
        var result: CMSampleBuffer?
        XCTAssertEqual(CMSampleBufferCreateReadyWithImageBuffer(allocator: kCFAllocatorDefault, imageBuffer: image,
            formatDescription: try XCTUnwrap(format), sampleTiming: &timing, sampleBufferOut: &result), noErr)
        return try XCTUnwrap(result)
    }
}
