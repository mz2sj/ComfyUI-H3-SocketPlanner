"""Socket adapter for TimelineDirector; all H3 planning stays in the original plugin."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image
import soundfile as sf

import folder_paths
import nodes
from comfy_api.latest import io
from comfy_api.input_impl import VideoFromFile

MODES = {"可编辑参考": "edit", "固定 Guide": "guide", "仅固定边界": "boundary"}
CACHE_FOLDER = "h3_socket_planner"


def original():
    cls = nodes.NODE_CLASS_MAPPINGS.get("MiniMaxH3TimelinePlanner")
    if cls is None:
        raise ValueError("请安装并启用 ComfyUI-MiniMaxH3-TimelineDirector（含素材规划台）。")
    return sys.modules[cls.__module__]


def ordered(values, prefix, limit):
    items = sorted(((int(k.rsplit("_", 1)[1]), v) for k, v in (values or {}).items() if v is not None))
    if len(items) > limit or [i for i, _ in items] != list(range(1, len(items) + 1)):
        raise ValueError(f"{prefix} 请从 1 开始连续连接，最多 {limit} 个；不要留空编号。")
    return items


def input_relative(path):
    root = Path(folder_paths.get_input_directory()).resolve()
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("视频输入必须指向文件。")
    if path.is_relative_to(root):
        return path.relative_to(root).as_posix()
    # Preserve compressed file bytes and original frame timestamps for output/temp sources.
    if not any(path.is_relative_to(Path(r).resolve()) for r in (
        folder_paths.get_output_directory(), folder_paths.get_temp_directory()
    )):
        raise ValueError("请将参考视频放到 ComfyUI input、output 或 temp 目录，再用原生 Load Video 加载。")
    stat = path.stat()
    stamp = hashlib.sha256(f"{path}:{stat.st_size}:{stat.st_mtime_ns}".encode()).hexdigest()
    dest = root / CACHE_FOLDER / f"{stamp}{path.suffix}"
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as f:
            temporary = Path(f.name)
        try:
            shutil.copyfile(path, temporary)
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
    return dest.relative_to(root).as_posix()


def video_clip(index, video, mode, include_audio):
    if not isinstance(video, VideoFromFile) or not isinstance(video.get_stream_source(), str):
        raise ValueError("参考视频请连接原生 Load Video 的 VIDEO 输出；VHS 的 IMAGE 或由图片合成的 VIDEO 不保留原文件时间戳。")
    filename = input_relative(video.get_stream_source())
    backend = original()
    info = backend._media_info(backend._safe_input_path(filename), include_peaks=False)
    if not info["hasVideo"]:
        raise ValueError(f"{filename} 不包含视频轨道。")
    start, requested = video.get_active_trim_window()
    available = max(0.0, info["duration"] - start)
    duration = min(available, requested) if requested > 0 else available
    if not math.isfinite(duration) or duration < backend.MIN_REF_VIDEO_SECONDS:
        raise ValueError(f"参考视频 {index} 有效时长不足 {backend.MIN_REF_VIDEO_SECONDS:.3f} 秒。")
    return {"id": f"video_{index}", "file": filename, "name": info["name"],
            "start": 0.0, "duration": duration, "trimStart": start,
            "sourceDuration": info["duration"], "hasAudio": bool(include_audio and info["hasAudio"]),
            "referenceMode": MODES[mode]}


def store_image(index, image):
    if image.shape[0] != 1:
        raise ValueError(f"参考图片 {index} 请接一张图片；多张图片请分别连接，保证 <Picture N> 编号明确。")
    pixels = (image[0, ..., :3].detach().cpu().float().clamp(0, 1).numpy() * 255).round().astype(np.uint8)
    digest = hashlib.sha256(str(pixels.shape).encode() + pixels.tobytes()).hexdigest()
    path = Path(folder_paths.get_input_directory()) / CACHE_FOLDER / f"{digest}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as f:
            temporary = Path(f.name)
        try:
            Image.fromarray(pixels).save(temporary)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"id": f"image_{index}", "file": f"{CACHE_FOLDER}/{path.name}", "name": f"参考图片 {index}"}


def store_audio(index, audio):
    waveform = audio["waveform"]
    if waveform.shape[0] != 1:
        raise ValueError(f"参考音频 {index} 请接单条音频。")
    pcm = waveform[0].detach().cpu().float().transpose(0, 1).contiguous().numpy()
    rate = int(audio["sample_rate"])
    digest = hashlib.sha256(str((rate, pcm.shape)).encode() + pcm.tobytes()).hexdigest()
    path = Path(folder_paths.get_input_directory()) / CACHE_FOLDER / f"{digest}.wav"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".wav", delete=False) as f:
            temporary = Path(f.name)
        try:
            sf.write(temporary, pcm, rate, subtype="FLOAT")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {"id": f"audio_{index}", "file": f"{CACHE_FOLDER}/{path.name}",
            "name": f"独立参考音频 {index}", "trimStart": 0.0, "duration": len(pcm) / rate}


def make_plans(clips, images, audios, width, height, reference_seconds, global_prompt="", prompt_index=None, segment_config=""):
    backend = original()
    seconds = float(reference_seconds)
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("参考时长必须为有限的非负数。")
    if seconds == 0:
        if len(clips) != 1:
            raise ValueError("没有视频或连接多个视频时，必须填写大于 0 的参考时长（秒）。")
        seconds = clips[0]["duration"]
    if not backend.MIN_REF_VIDEO_SECONDS <= seconds <= 150:
        raise ValueError("参考时长必须在 5/24 秒至 150 秒之间；较长视频请填写本次参考时长。")
    if width % 32 or height % 32 or min(width, height) < 32:
        raise ValueError("宽度、高度必须是至少 32 的 32 倍数。")
    if sum(c["referenceMode"] in {"guide", "boundary"} for c in clips) > 1:
        raise ValueError("所有视频从 0 秒开始。多个固定 Guide/固定边界会重叠，请只保留一个，其余设为可编辑参考。")
    config = json.loads(segment_config) if segment_config.strip() else {"count": 0, "segments": []}
    if not isinstance(config, dict):
        raise ValueError("分段配置必须是原素材规划台 segmentConfig 格式的 JSON 对象。")
    timeline = {"version": 5, "fps": backend.FPS, "selection": {"start": 0, "duration": seconds},
                "videoClips": clips, "images": images, "audios": audios, "videoAudioEnabled": True,
                "globalPrompt": global_prompt, "segmentConfig": config}
    encoded = json.dumps(timeline, ensure_ascii=False)
    complete = backend._create_timeline_plan(encoded, width, height, seconds, None)
    selected = complete
    if prompt_index is not None:
        selected = backend._create_timeline_plan(encoded, width, height, seconds, prompt_index)
    elif config.get("mode") == "timeline" and int(config.get("count", 0)) > 0:
        selected = backend._create_timeline_plan(encoded, width, height, seconds, int(config.get("activeIndex", 0)) + 1)
    return selected, complete


def report(plan):
    backend = original()
    timeline = plan["timeline"]
    specs = backend._video_reference_specs(timeline)
    start = timeline["selection"]["start"]
    end = start + timeline["selection"]["duration"]
    candidate_count = sum(len(list(backend._windows(max(start, c["start"]), max(0, min(end, c["start"] + c["duration"]) - max(start, c["start"]))))) for c in timeline["videoClips"])
    lines = [f"参考片段数：{len(specs)}（当前选区，非生成分段数）"]
    if candidate_count > backend.MAX_REF_VIDEOS:
        lines.append(f"原规则限制：当前选区有 {candidate_count} 个候选片段，仅前 {backend.MAX_REF_VIDEOS} 个作为视频参考；请检查下游分段设置。")
    lines.append(backend._reference_manifest(plan))
    return "\n".join(lines)


def grow(group, kind, prefix, maximum, label):
    return io.Autogrow.Input(group, display_name=label, optional=True,
        template=io.Autogrow.TemplateNames(kind(prefix), names=[f"{prefix}_{i}" for i in range(1, maximum + 1)], min=0))


class H3SocketPlanner(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="H3SocketPlanner", display_name="MiniMax H3 素材规划（连线版）",
            category="model/conditioning/minimax", description="复用 TimelineDirector 原规则；视频接原生 Load Video 的 VIDEO，保留原时间戳和原声。",
            inputs=[
                io.Int.Input("prompt_index", display_name="分段提示词编号", optional=True, force_input=True,
                             tooltip="与原节点一致，仅在提供分段配置时选择某段素材；不是 Picture/Video 标签编号。"),
                grow("reference_videos", io.Video.Input, "video", 3, "参考视频"),
                grow("reference_images", io.Image.Input, "image", 9, "参考图片"),
                grow("reference_audios", io.Audio.Input, "audio", 3, "参考音频"),
                io.Int.Input("width", display_name="宽度", default=768, min=32, max=16384, step=32),
                io.Int.Input("height", display_name="高度", default=1024, min=32, max=16384, step=32),
                io.Float.Input("reference_seconds", display_name="参考时长（秒）", default=0, min=0, max=150, step=0.01,
                               tooltip="0 仅限单视频：使用完整有效时长。无视频或多视频必须填写正数。"),
                *[io.Combo.Input(f"video_mode_{i}", display_name=f"视频 {i} 用途", options=list(MODES), default="可编辑参考") for i in range(1, 4)],
                io.Boolean.Input("video_audio", display_name="使用视频原声", default=False),
                io.String.Input("global_prompt", display_name="全局提示词", default="", multiline=True),
                io.String.Input("segment_config", display_name="分段配置（可选）", default="", optional=True, force_input=True,
                                tooltip="原节点 segmentConfig JSON；不接时由下游有限分段展开管理生成分段。"),
            ], outputs=[io.Custom("MINIMAX_H3_TIMELINE_PLAN").Output(display_name="素材规划"),
                        io.Custom("MINIMAX_H3_OMNI_MEDIA_BUNDLE").Output(display_name="Omni 素材包"),
                        io.Custom("MINIMAX_H3_FINITE_SEGMENT_PLAN").Output(display_name="分段规划")])

    @classmethod
    def execute(cls, width, height, reference_seconds, video_mode_1="可编辑参考", video_mode_2="可编辑参考",
                video_mode_3="可编辑参考", video_audio=False, global_prompt="", prompt_index=None,
                reference_videos=None, reference_images=None, reference_audios=None, segment_config=""):
        modes = [video_mode_1, video_mode_2, video_mode_3]
        clips = [video_clip(i, value, modes[i-1], video_audio) for i, value in ordered(reference_videos, "视频", 3)]
        images = [store_image(i, value) for i, value in ordered(reference_images, "图片", 9)]
        audios = [store_audio(i, value) for i, value in ordered(reference_audios, "音频", 3)]
        selected, complete = make_plans(clips, images, audios, width, height, reference_seconds, global_prompt, prompt_index, segment_config)
        bundle = original()._create_prompt_media_bundle(selected)
        return io.NodeOutput(selected, bundle, complete, ui={"text": [report(selected)]})
