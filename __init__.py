from .planner import H3SocketPlanner
from . import preview

NODE_CLASS_MAPPINGS = {"H3SocketPlanner": H3SocketPlanner}
NODE_DISPLAY_NAME_MAPPINGS = {"H3SocketPlanner": "MiniMax H3 素材规划（连线版）"}
WEB_DIRECTORY = "./js"
