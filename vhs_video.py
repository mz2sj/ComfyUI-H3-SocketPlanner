"""Write VHS frames to a file-backed video the socket planner can read."""
import hashlib
import math
import os
from fractions import Fraction
from pathlib import Path
import tempfile

import av
import numpy as np
import torch

import folder_paths
from comfy_api.latest import io
from comfy_api.input_impl import VideoFromFile

CACHE_FOLDER = "h3_socket_planner"


def _rate(fps):
    fps = float(fps)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("帧率必须是大于 0 的有限数字。请接 VHS Video Info (Loaded) 的 loaded_fps。")
    return Fraction(fps).limit_denominator(100000)


def _even(images):
    """yuv420p needs even width and height. Replicate the edge instead of scaling."""
    height, width = int(images.shape[1]), int(images.shape[2])
    pad_h, pad_w = height % 2, width % 2
    if pad_h or pad_w:
        images = images.permute(0, 3, 1, 2)
        images = torch.nn.functional.pad(images, (0, pad_w, 0, pad_h), mode="replicate")
        images = images.permute(0, 2, 3, 1)
    return images.contiguous()


def _digest(images, rate, audio):
    digest = hashlib.sha256()
    digest.update(f"{tuple(images.shape)}:{rate}".encode())
    cpu = images.detach().cpu().contiguous()
    for frame in cpu:
        digest.update(frame.numpy().tobytes())
    if audio is not None:
        waveform = audio["waveform"].detach().cpu().contiguous()
        digest.update(str(int(audio["sample_rate"])).encode())
        digest.update(waveform.numpy().tobytes())
    else:
        digest.update(b"silent")
    return digest.hexdigest(), cpu


def _audio_clip(audio, frames, rate):
    if audio is None:
        return None, None, None
    waveform = audio["waveform"]
    if waveform.shape[0] != 1:
        raise ValueError("音频请接单条音轨，直接用 VHS Load Video 的 audio。")
    sample_rate = int(audio["sample_rate"])
    if sample_rate <= 0:
        raise ValueError("音频采样率无效。")
    channels = int(waveform.shape[1])
    layout = {1: "mono", 2: "stereo", 6: "5.1"}.get(channels)
    if layout is None:
        raise ValueError(f"音频声道数是 {channels}，只接受单声道、立体声或 5.1。")
    samples = math.ceil(float(frames) / float(rate) * sample_rate)
    pcm = waveform[0, :, :samples].detach().cpu().float().contiguous().numpy()
    return pcm, sample_rate, layout


def _encode(path, images, rate, pcm, sample_rate, layout):
    with av.open(path, mode="w", format="mp4", options={"movflags": "+faststart"}) as output:
        stream = output.add_stream("libx264", rate=rate)
        stream.width = int(images.shape[2])
        stream.height = int(images.shape[1])
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "0", "preset": "ultrafast"}
        audio_stream = None
        if pcm is not None:
            audio_stream = output.add_stream("aac", rate=sample_rate, layout=layout)
            audio_stream.bit_rate = 320000
        for index, frame in enumerate(images):
            pixels = (frame[..., :3].clamp(0, 1).numpy() * 255).round().astype(np.uint8)
            video_frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            video_frame.pts = index
            for packet in stream.encode(video_frame.reformat(format="yuv420p")):
                output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
        if audio_stream is not None:
            next_pts = 0
            for start in range(0, pcm.shape[1], 1024):
                piece = np.ascontiguousarray(pcm[:, start:start + 1024])
                audio_frame = av.AudioFrame.from_ndarray(piece, format="fltp", layout=layout)
                audio_frame.sample_rate = sample_rate
                audio_frame.pts = next_pts
                next_pts += piece.shape[1]
                for packet in audio_stream.encode(audio_frame):
                    output.mux(packet)
            for packet in audio_stream.encode(None):
                output.mux(packet)


def write_reference(images, fps, audio=None):
    if images.ndim != 4 or images.shape[0] < 1 or images.shape[-1] < 3:
        raise ValueError("图像必须是 VHS Load Video 输出的画面批次。")
    rate = _rate(fps)
    images = _even(images[..., :3])
    digest, cpu = _digest(images, rate, audio)
    folder = Path(folder_paths.get_input_directory()) / CACHE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{digest}.mp4"
    if not dest.is_file() or dest.stat().st_size == 0:
        pcm, sample_rate, layout = _audio_clip(audio, cpu.shape[0], rate)
        with tempfile.NamedTemporaryFile(dir=folder, suffix=".mp4", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            _encode(temporary, cpu, rate, pcm, sample_rate, layout)
            os.replace(temporary, dest)
        finally:
            temporary.unlink(missing_ok=True)
    return VideoFromFile(str(dest.resolve()))


class H3VHSReferenceVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="H3VHSReferenceVideo",
            display_name="VHS帧转参考视频",
            category="model/conditioning/minimax",
            description="把 VHS 的画面和原声写成接近无损的 H.264 文件，供素材规划的参考视频口读取。",
            inputs=[
                io.Image.Input("images", display_name="图像"),
                io.Audio.Input("audio", display_name="音频", optional=True),
                io.Float.Input(
                    "fps", display_name="帧率", default=24.0, min=0.01, max=240.0, step=0.001,
                    tooltip="接 VHS「Video Info (Loaded)」的 loaded_fps。force_rate 为 0 时这就是原片帧率。",
                ),
            ],
            outputs=[io.Video.Output(display_name="VIDEO")],
        )

    @classmethod
    def execute(cls, images, fps, audio=None):
        return io.NodeOutput(write_reference(images, fps, audio))
