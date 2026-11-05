"""Pair Main/Ultra context on exact replay IDs, preserving measured exposure offsets."""
import json
from pathlib import Path
import subprocess

import cv2
import numpy as np

from .rgbd import Recording,VideoReader,output_directory,read_rows,write_json


def verify_timing(actual,expected):
    if len(actual)!=len(expected)or not len(expected):raise ValueError('Paired preview changed frame count.')
    error=float(np.max(np.abs(np.asarray(actual)-np.asarray(expected))))
    if not np.isfinite(error)or error>.002:raise ValueError('Paired preview changed source presentation timing.')
    return error


def attach(output):
    output=Path(output);dataset=output/'export';map_dir=output/'map'
    cfg=json.loads((dataset/'calibration.json').read_text());scene=json.loads((map_dir/'scene.json').read_text())
    frames=scene['timeline']['frames'];rows=read_rows(dataset/'frames.jsonl');recording=Recording(Path(cfg['source']))
    target=output_directory(dataset,map_dir/'paired',recording.path);(target/'images').mkdir()
    width,height=cfg['width'],cfg['height'];reader=VideoReader(recording,'ultra');records=[];concat=['ffconcat version 1.0']
    try:
        for index,frame in enumerate(frames):
            row=rows[frame['id']-1]
            if abs(float(row['timestamp_s'])-1-frame['seconds'])>1e-8:raise ValueError('Replay/source frame identity mismatch.')
            ultra=recording.nearest('ultra',int(row['host_ns']));main=cv2.imread(str(dataset/row['rgb']))
            wide=cv2.resize(reader.frame(ultra),(width,height),interpolation=cv2.INTER_AREA)
            image=np.concatenate([cv2.rotate(main,cv2.ROTATE_90_CLOCKWISE),cv2.rotate(wide,cv2.ROTATE_90_CLOCKWISE)],axis=1)
            path=target/'images'/f'{index:06d}.jpg'
            if not cv2.imwrite(str(path),image,[cv2.IMWRITE_JPEG_QUALITY,92]):raise OSError('Unable to save paired preview.')
            duration=frames[index+1]['seconds']-frame['seconds']if index+1<len(frames)else .034
            concat.extend(["file '"+str(path.resolve()).replace("'","'\\''")+"'",'option framerate 1000',f'duration {duration:.9f}'])
            records.append({'source_frame_id':frame['id'],'main_host_ns':row['host_ns'],'ultra_host_ns':ultra['host_ns'],'ultra_segment':ultra['segment'],'ultra_frame_index':ultra['frame_index'],'ultra_minus_main_ms':(int(ultra['host_ns'])-int(row['host_ns']))/1e6})
    finally:reader.close()
    (target/'preview.ffconcat').write_text('\n'.join(concat)+'\n')
    subprocess.run(['ffmpeg','-v','error','-safe','0','-f','concat','-i',str(target/'preview.ffconcat'),'-c:v','libx264','-threads','4','-crf','18','-pix_fmt','yuv420p','-fps_mode','vfr','-video_track_timescale','1000000','-movflags','+faststart',str(target/'preview.mp4')],check=True)
    probe=json.loads(subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(target/'preview.mp4')],capture_output=True,text=True,check=True).stdout)
    error=verify_timing([float(f['best_effort_timestamp_time'])for f in probe['frames']],[f['pts']for f in frames])
    limits=['Reduced context preview; both displayed crops use one decoded movie to prevent decoder drift.','Ultra uses nearest measured exposures; displayed offsets are preserved, not a claim of simultaneous exposure.','Ultra is raw/unrectified context and is not fused into geometry. Original source movies remain intact.']
    write_json(target/'provenance.json',{'schema':'scene-capture.paired-preview/1','frames':records,'max_presentation_error_ms':error*1000,'limitations':limits})
    scene['ultra_context']={'video':'paired/preview.mp4','exposure_offsets_ms':[r['ultra_minus_main_ms']for r in records],'limitations':limits}
    scene['paired_preview']={'video':'paired/preview.mp4','provenance':'paired/provenance.json'}
    write_json(map_dir/'scene.json',scene)
