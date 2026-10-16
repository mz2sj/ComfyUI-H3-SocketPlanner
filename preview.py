"""Read-only planning preview; never queues sampling or loads model weights."""
import asyncio
from pathlib import Path

from aiohttp import web
from comfy_api.input_impl import VideoFromFile
import folder_paths
from server import PromptServer

from .planner import make_plans, original, report, video_clip


def preview_plan(data):
    clips = []
    for index, item in enumerate(data.get("videos", []), 1):
        if index > 3:
            raise ValueError("最多连接 3 个参考视频。")
        path = Path(folder_paths.get_annotated_filepath(str(item["file"]))).resolve()
        if not any(path.is_relative_to(Path(root).resolve()) for root in (
            folder_paths.get_input_directory(), folder_paths.get_output_directory(), folder_paths.get_temp_directory()
        )):
            raise ValueError("视频必须位于 ComfyUI 的媒体目录中。")
        clips.append(video_clip(index, VideoFromFile(str(path)), item.get("mode", "可编辑参考"), bool(data.get("video_audio", False))))
    counts = [("images", 9, "image", "参考图片"), ("audios", 3, "audio", "独立参考音频")]
    assets = {}
    for key, maximum, prefix, title in counts:
        count = int(data.get(key, 0))
        if not 0 <= count <= maximum:
            raise ValueError(f"{title} 数量超过上限 {maximum}。")
        assets[key] = [{"id": f"{prefix}_{i}", "file": f"preview-{prefix}-{i}", "name": f"{title} {i}"} for i in range(1, count + 1)]
    plan, _ = make_plans(clips, assets["images"], assets["audios"], int(data.get("width", 768)), int(data.get("height", 1024)),
                         data.get("reference_seconds", 0), str(data.get("global_prompt", "")), data.get("prompt_index"), str(data.get("segment_config", "")))
    return {"text": report(plan), "count": len(original()._video_reference_specs(plan["timeline"]))}


@PromptServer.instance.routes.post("/h3_socket_planner/preview")
async def preview(request):
    try:
        result = await asyncio.to_thread(preview_plan, await request.json())
        return web.json_response(result)
    except (ValueError, KeyError, TypeError, OSError) as error:
        return web.json_response({"error": str(error)}, status=400)
