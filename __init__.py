from .planner import H3SocketPlanner
from .vhs_video import H3VHSReferenceVideo
from . import preview

NODE_CLASS_MAPPINGS = {
    "H3SocketPlanner": H3SocketPlanner,
    "H3VHSReferenceVideo": H3VHSReferenceVideo,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3SocketPlanner": "MiniMax H3 素材规划（连线版）",
    "H3VHSReferenceVideo": "VHS帧转参考视频",
}
WEB_DIRECTORY = "./js"
