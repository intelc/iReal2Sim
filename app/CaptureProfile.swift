import Foundation

struct CaptureProfile {
    let width: Int32
    let height: Int32
    let rgbFPS: Int32
    let depthFPS: Int32
    let videoBitrate: Int

    static let requested = CaptureProfile(width: 1920, height: 1080, rgbFPS: 60, depthFPS: 30,
                                          videoBitrate: 24_000_000)

    func ratesMeetTarget(_ rates: [String: Double]) -> Bool {
        ["main", "ultra"].allSatisfy { rates[$0, default: 0] >= Double(rgbFPS) * 0.9 }
            && rates["depth", default: 0] >= Double(depthFPS) * 0.9
    }
}
