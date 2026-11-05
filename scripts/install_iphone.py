#!/usr/bin/env python3
"""Build with the user's signing team and install on a specified paired iPhone."""
import argparse
from pathlib import Path
import subprocess


def commands(root,team,device,bundle):
    build=root/'build/self-signed'
    return [
        ['xcodebuild','-project',str(root/'SceneCapture.xcodeproj'),'-scheme','SceneCapture','-configuration','Release',
         '-destination','id='+device,'-derivedDataPath',str(build),'-allowProvisioningUpdates',
         'DEVELOPMENT_TEAM='+team,'PRODUCT_BUNDLE_IDENTIFIER='+bundle,'CODE_SIGN_STYLE=Automatic','build'],
        ['xcrun','devicectl','device','install','app','--device',device,str(build/'Build/Products/Release-iphoneos/SceneCapture.app')],
    ]


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--team',required=True,help='Your Apple development team ID');p.add_argument('--device',required=True,help='Identifier from xcrun devicectl list devices');p.add_argument('--bundle-id',required=True,help='Unique identifier such as com.yourname.iReal2Sim');p.add_argument('--dry-run',action='store_true');a=p.parse_args()
    root=Path(__file__).resolve().parents[1]
    for command in commands(root,a.team,a.device,a.bundle_id):
        if a.dry_run:print(subprocess.list2cmdline(command))
        else:subprocess.run(command,check=True)
