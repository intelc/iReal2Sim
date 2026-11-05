// Small dataset adapter; odometry, loop closure and graph optimization are RTAB-Map.
#include <rtabmap/core/CameraModel.h>
#include <rtabmap/core/Odometry.h>
#include <rtabmap/core/OdometryInfo.h>
#include <rtabmap/core/Parameters.h>
#include <rtabmap/core/Rtabmap.h>
#include <rtabmap/core/SensorData.h>
#include <rtabmap/core/Version.h>
#include <rtabmap/utilite/ULogger.h>
#include <opencv2/imgcodecs.hpp>
#include <Eigen/Core>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

namespace fs = std::filesystem;

void writePose(std::ostream &out, int id, double stamp, const rtabmap::Transform &pose) {
    out << id << '\t' << std::setprecision(16) << stamp;
    const auto matrix = pose.toEigen4f();
    for (int row = 0; row < 4; ++row) for (int col = 0; col < 4; ++col) out << '\t' << matrix(row, col);
    out << '\n';
}

int main(int argc, char **argv) {
    try {
        if (argc != 3) throw std::runtime_error("Usage: iphone-rsa-map DATASET EMPTY_OUTPUT_DIRECTORY");
        const fs::path dataset = fs::absolute(argv[1]), output = fs::absolute(argv[2]);
        if (fs::exists(output) && !fs::is_empty(output)) throw std::runtime_error("Output directory is not empty");
        fs::create_directories(output);
        double fx, fy, cx, cy;
        int width, height;
        std::ifstream camera(dataset / "camera.txt");
        if (!(camera >> fx >> fy >> cx >> cy >> width >> height) || fx <= 0 || fy <= 0 || width <= 0 || height <= 0)
            throw std::runtime_error("Invalid camera.txt");
        rtabmap::CameraModel model("main", fx, fy, cx, cy, rtabmap::Transform::getIdentity(), 0, cv::Size(width, height));
        rtabmap::ParametersMap parameters = {
            {"Rtabmap/WorkingDirectory", output.string()}, {"Rtabmap/DetectionRate", "0"},
            {"Rtabmap/ImagesAlreadyRectified", "true"}, {"Mem/GenerateIds", "false"},
            {"Mem/RehearsalSimilarity", "1.0"}, {"Mem/STMSize", "30"},
            {"RGBD/LinearUpdate", "0"}, {"RGBD/AngularUpdate", "0"},
            {"RGBD/CreateOccupancyGrid", "false"}, {"RGBD/ProximityBySpace", "true"},
            {"Odom/Strategy", "0"}, {"Reg/Strategy", "0"},
            {"Vis/FeatureType", "8"}, {"Kp/DetectorStrategy", "8"},
            {"Vis/MaxFeatures", "1500"}, {"Kp/MaxFeatures", "1000"},
            {"Vis/MinInliers", "20"}, {"Vis/MaxDepth", "5"},
            {"Optimizer/Strategy", "1"}, {"g2o/Solver", "3"}
        };
        ULogger::setType(ULogger::kTypeConsole);
        ULogger::setLevel(ULogger::kWarning);
        std::unique_ptr<rtabmap::Odometry> odometry(rtabmap::Odometry::create(parameters));
        rtabmap::Rtabmap mapping;
        mapping.init(parameters, (output / "map.db").string());
        std::ifstream frames(dataset / "frames.tsv");
        std::ofstream poses(output / "odometry.tsv"), states(output / "tracking.tsv"), params(output / "parameters.tsv");
        if (!frames || !poses || !states) throw std::runtime_error("Cannot open mapper input/output files");
        for (const auto &item : parameters) params << item.first << '\t' << item.second << '\n';
        poses << "# id timestamp_s world_from_camera_row_major_4x4\n";
        states << "# id timestamp_s tracked map_accepted loop_closure_id inliers matches\n";
        std::map<int, double> stamps;
        int id, tracked = 0, total = 0, loops = 0;
        double stamp;
        std::string rgbPath, depthPath;
        while (frames >> id >> stamp >> rgbPath >> depthPath) {
            cv::Mat rgb = cv::imread((dataset / rgbPath).string(), cv::IMREAD_COLOR);
            cv::Mat depth = cv::imread((dataset / depthPath).string(), cv::IMREAD_UNCHANGED);
            if (rgb.empty() || depth.empty() || depth.type() != CV_16UC1 || rgb.size() != depth.size() || rgb.size() != cv::Size(width, height))
                throw std::runtime_error("Invalid RGB-D payload at frame " + std::to_string(id));
            // RTAB-Map's uint16 depth convention is millimetres. No 5000-unit TUM scaling.
            rtabmap::SensorData data(rgb, depth, model, id, stamp);
            rtabmap::OdometryInfo info;
            const rtabmap::Transform pose = odometry->process(data, &info);
            bool mapped = false;
            int loop = 0;
            if (!pose.isNull()) {
                ++tracked;
                stamps[id] = stamp;
                writePose(poses, id, stamp, pose);
                mapped = mapping.process(data, pose, info.reg.covariance);
                loop = mapping.getLoopClosureId();
                if (loop > 0) ++loops;
            }
            states << id << '\t' << std::setprecision(16) << stamp << '\t' << !pose.isNull() << '\t' << mapped << '\t' << loop
                   << '\t' << info.reg.inliers << '\t' << info.reg.matches << '\n';
            if (++total % 25 == 0) std::cout << "Processed " << total << ", tracked " << tracked << ", loop closures " << loops << std::endl;
        }
        std::map<int, rtabmap::Transform> optimized;
        std::multimap<int, rtabmap::Link> links;
        mapping.getGraph(optimized, links, true, true);
        std::ofstream graph(output / "optimized.tsv");
        graph << "# id timestamp_s world_from_camera_row_major_4x4\n";
        for (const auto &node : optimized) if (stamps.count(node.first)) writePose(graph, node.first, stamps.at(node.first), node.second);
        mapping.close(true);
        std::ofstream summary(output / "backend.json");
        summary << "{\"schema\":\"iphone-rsa.rtabmap-run/1\",\"backend\":\"RTAB-Map\",\"version\":\"" << RTABMAP_VERSION << "\",\"frames\":" << total
                << ",\"tracked\":" << tracked << ",\"optimized_nodes\":" << optimized.size()
                << ",\"loop_closures\":" << loops
                << ",\"world_frame\":\"initial main-camera optical frame\",\"dynamic_masking\":false,\"imu_fusion\":false}\n";
        std::cout << "Finished: " << tracked << '/' << total << " frames tracked, " << optimized.size() << " optimized nodes" << std::endl;
        return tracked > 0 ? 0 : 2;
    } catch (const std::exception &error) {
        std::cerr << "Mapper error: " << error.what() << std::endl;
        return 1;
    }
}
