# Build and sign your own iPhone app

You can install Scene Capture on your own iPhone from source using Xcode and
your Apple Account. A prebuilt, unsigned `.ipa` from GitHub cannot be installed
directly on an ordinary iPhone. The source download is the supported route for
this release; there is no published TestFlight build yet.

## Before you start

- A Mac with Xcode that supports your phone's installed iOS version. Open Xcode
  once and finish its first-run setup and iOS platform installation.
- An iPhone 17 Pro or 18 Pro, a USB data cable and your own Apple Account.
  17 Pro short captures have been tested. 18 Pro must pass the app's runtime
  MultiCam/LiDAR/rate checks; physical validation is pending.
- At least 10 GB free on the phone for longer tests. Start with a 15-second clip.

## 1. Download and open the source

Download the source ZIP from the repository, or clone it:

```sh
git clone https://github.com/intelc/iReal2Sim.git
cd iReal2Sim
open SceneCapture.xcodeproj
```

Prefer a released version tag once one is published. The Xcode project is
included; XcodeGen is only needed if you edit `project.yml`.

## 2. Choose your signing identity

In **Xcode → Settings → Accounts**, add your Apple Account. Select the blue
**SceneCapture** project in the navigator, then the **SceneCapture app target**
and **Signing & Capabilities**. Enable **Automatically manage signing** and
select your own team. A free account appears as **Personal Team**.

Use a unique bundle identifier, such as `com.yourname.iReal2Sim`. If you run the
native test target, give it your team as well. No development team, certificate,
private key or provisioning profile is bundled with this repository.

## 3. Pair your iPhone

Connect and unlock it, accept the pairing/trust prompt on your own devices,
and select the physical iPhone beside the **SceneCapture** scheme in Xcode's
toolbar. If iOS requests Developer Mode, enable it in
**Settings → Privacy & Security → Developer Mode**, complete the restart and
confirm on the phone. Xcode's device details explain any missing platform support.

## 4. Build and run

Choose **Product → Run**. Xcode signs the app for your phone and installs it.
Follow any iOS developer trust prompts for your own signing identity. Open
Scene Capture, tap **Prepare cameras**, grant camera access and verify the
Main, Ultra Wide and depth frame-rate indicators. Recording is enabled only
after the required streams meet the preview-rate checks.

Record a short test. Stop, wait for **Complete**, then export the whole session
folder. Keep the app in the foreground during capture. Run `./start` on your Mac
to process that folder; the API key belongs in the desktop setup form.

## Renew a free-account installation

Apple's Personal Team provisioning expires after seven days. Rebuild and install
again from Xcode when it expires. Keep the same bundle ID and signing team, and
export your recordings before changing either or removing the app. Apple also
limits the active devices and apps associated with a Personal Team.
[Apple's current Personal Team limits](https://developer.apple.com/help/account/basics/about-your-developer-account/).

## Optional command-line installation

After setting up your Apple Account and pairing in Xcode:

```sh
xcrun devicectl list devices
python3 scripts/install_iphone.py \
  --team YOUR_TEAM_ID \
  --device YOUR_DEVICE_IDENTIFIER \
  --bundle-id com.yourname.iReal2Sim
```

The helper builds with automatic signing and installs the result. Add `--dry-run`
to inspect the exact commands. Credentials remain managed by Xcode.

## Troubleshooting

| Message | Action |
| --- | --- |
| Bundle identifier unavailable | Pick a unique identifier under your own name. |
| Signing requires a development team | Select your account's team on the app target. |
| Device is locked or unpaired | Unlock it and complete pairing in Xcode. |
| Developer Mode disabled | Follow the on-device Developer Mode flow. |
| Unsupported iOS version | Install Xcode/device support matching that iOS release. |
| App stops launching after a week | Renew the Personal Team build with Xcode. |
| Camera graph or rate check fails | Export the capability report; the exact 60/60/30 profile is not verified on that device. |

## Distributing a ready-to-install build

For a public TestFlight link, the maintainer needs an Apple Developer Program
membership, an App Store Connect app record and a signed uploaded build, with
Apple's beta distribution requirements completed. GitHub hosts the source and
release notes; it does not replace Apple's provisioning.
[Apple distribution guide](https://developer.apple.com/documentation/xcode/distributing-your-app-for-beta-testing-and-releases/).

## Documentation reference

The structure of this guide follows Cork's useful separation of prerequisites,
source download, Xcode signing settings and build/install steps.
[Cork's compilation instructions](https://github.com/buresdv/Cork#compiling-cork-yourself)
describe a macOS app. Its local ad-hoc signing settings do not apply to an iPhone;
this guide uses Apple's iOS development provisioning instead. No Cork source,
artwork or build machinery is included here.
