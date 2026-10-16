# MiniMax H3 素材规划（连线版）

普通连线界面，复用已安装的 `ComfyUI-MiniMaxH3-TimelineDirector` 的规划、原时间戳解码、参考切片和编号实现。无需修改原插件。内部节点名：`H3SocketPlanner`。

## 安装和连接

将整个 `ComfyUI-H3-SocketPlanner` 文件夹放入 `ComfyUI/custom_nodes`，安装原 TimelineDirector（需包含 `MiniMaxH3TimelinePlanner`），重启 ComfyUI。需要支持 `io.Autogrow` 和原生 `LoadVideo` 的 ComfyUI；本机已在 ComfyUI 0.34.6、前端 1.49.6 验证。使用 ComfyUI 自带 PyAV、Pillow、NumPy、soundfile，无新增运行依赖。

- 视频：**原生 `Load Video` 的 `VIDEO` 输出**接参考视频。携带原文件和原声，按原时间戳读取并由原解码器转为 24 fps。不是 VHS `Load Video (Upload)` 的 `IMAGE` 输出；不支持把图像帧或 Create Video 的合成视频当作原文件视频。
- 图片：`Load Image` 的 `IMAGE` 输出，每个端口一张。
- 音频：`Load Audio` 的 `AUDIO` 输出，作为独立参考音频。
- 连接后出现下一同类输入。最多 3 个视频、9 张图片、3 条独立音频；请从 1 开始连续连接，不留空号。
- 每个视频独立选择可编辑参考、固定 Guide、仅固定边界。视频原声由“使用视频原声”开关控制，默认关闭，和截图中的原规划台设置一致。

## 时长和分段

单视频、参考时长 = 0：读取实际有效全长。有明确正数则按填写时长。没有视频或有多个视频时必须填写正数。时长范围遵循原节点 5/24 至 150 秒，H3 latent 帧数沿用原对齐规则，显示中同时给出秒数与合法帧数。

视频均从目标时间 0 秒开始并行参考，不自动拼接。源素材短于生成时长时，视频 clip 仍使用实际时长，不改速、不循环。多个固定 Guide/边界不允许同时覆盖同一目标起点。

“更新分段”读取原生 Load Video 的文件信息，显示**当前选区参考片段数**和素材编号；它不会修改下游的生成分段数。实际运行不需要点按钮，仍会重算。通过 Video Slice 等其他上游节点连接时，按钮不能提前执行该节点：请运行后查看准确规划。

保留原规则：长视频参考按最多 15 秒切片；当前选区最多使用 3 个视频参考片段。超过上限会显示原规则限制，不把候选总数伪装成实际参考数。下游的生成分段数量和参考片段数不是同一设置。

`<Picture N>` 顺序对应图片连接顺序；`<Video N>` 按原规则的实际切片顺序；`<Audio N>` 先独立参考音频，再视频原声。编号明细来自原节点 `_reference_manifest`，同时包含在 Omni 素材包中，可供提示词编写器使用。不能假定长视频的输入端口号永远等于切片后的 `<Video N>`。

“分段提示词编号”保留原行为：仅在提供分段配置时选取该段；默认不接。“分段配置（可选）”接受原节点 `segmentConfig` 格式 JSON；普通工作流不用连接，由下游有限分段展开处理。未提供配置时不创建隐式生成分段。使用者可在 API 中传入原结构的窗口、每段提示词及素材分配。

## 输出不变

1. 素材规划：`MINIMAX_H3_TIMELINE_PLAN`，接原有限分段展开或 Plan Encoder。
2. Omni 素材包：`MINIMAX_H3_OMNI_MEDIA_BUNDLE`，接原 Omni Media Prompt Bridge。
3. 分段规划：`MINIMAX_H3_FINITE_SEGMENT_PLAN`，内容与原规划台第三输出一致；直接用于有限分段采样时仍需按原规则填写全局/分段提示词。

不会替换模型、LoRA、SLA、下游采样器，也不承诺生成结果逐像素相同。已验证计划与原节点相等，未进行 GPU 成片质量比较。

## 脚本使用和文件

`examples/plan_api.json` 是只预览计划的 API 示例；替换视频/图片文件名即可，不加载模型。实际生成时把 `PreviewAny` 换成既有下游链。动态输入的 API 键例如 `reference_videos.video_1`、`reference_images.image_1`。

原视频已经位于 input 时不会重编码或复制。来自 output/temp 的文件会原样复制到 `input/h3_socket_planner`，保留压缩码流与时间戳。图片和独立音频连线会写入该目录的内容哈希缓存：PNG / float WAV。不会覆盖原素材，删除该目录可清理运行缓存，后续运行会重新生成。

`tests/check_planner.py` 使用合成媒体在 CPU 对照原实现；`tests/check_browser.py` 在隔离测试服务器验证前端、自动扩展、保存重载和 API，仅开发测试需 Playwright。请勿在用户生产页面运行测试脚本。
