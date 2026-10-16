import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

function source(node, input, visited = new Set()) {
    if (input?.link == null) return null;
    const graph = node.graph;
    const link = graph.links.get?.(input.link) ?? graph.links[input.link];
    if (!link || visited.has(link.origin_id)) throw new Error("无法解析输入连线。");
    visited.add(link.origin_id);
    const upstream = graph.getNodeById(link.origin_id);
    if (upstream?.type === "Reroute") return source(upstream, upstream.inputs[0], visited);
    return upstream;
}
function value(node, name, fallback) {
    const input = node.inputs?.find(i => i.name === name);
    if (input?.link != null) {
        const upstream = source(node, input);
        if (upstream?.type === "PrimitiveNode") return upstream.widgets?.[0]?.value ?? fallback;
        throw new Error(`${name} 由上游计算，请运行后查看准确规划。`);
    }
    return node.widgets?.find(w => w.name === name)?.value ?? fallback;
}
function inputs(node, group, prefix) {
    return (node.inputs || []).filter(i => i.link != null && new RegExp(`^(?:${group}\\.)?${prefix}_\\d+$`).test(i.name))
        .sort((a, b) => Number(a.name.split("_").pop()) - Number(b.name.split("_").pop()));
}
function continuous(list) {
    if (list.some((i, n) => Number(i.name.split("_").pop()) !== n + 1)) {
        throw new Error("素材请从编号 1 开始连续连接，不要留空编号。");
    }
    return list;
}

function labelInputs(node) {
    for (const input of node.inputs || []) {
        const match = input.name.match(/^(?:reference_(?:videos|images|audios)\.)?(video|image|audio)_(\d+)$/);
        if (match) input.label = `${{video: "参考视频", image: "参考图片", audio: "参考音频"}[match[1]]} ${match[2]}`;
    }
}

app.registerExtension({
    name: "H3.SocketPlanner",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "H3SocketPlanner") return;
        const created = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = created?.apply(this, arguments);
            const status = document.createElement("textarea");
            status.readOnly = true;
            status.value = "参考片段数：未更新\n连接素材后点击更新分段；执行时也会自动重新计算。";
            Object.assign(status.style, { width: "100%", height: "180px", resize: "none", boxSizing: "border-box", background: "#202020", color: "#ddd", fontSize: "12px" });
            this._h3Report = status;
            this.addWidget("button", "更新分段", null, async () => {
                try {
                    status.value = "正在读取视频信息…";
                    const videos = continuous(inputs(this, "reference_videos", "video")).map((input, index) => {
                        const upstream = source(this, input);
                        if (upstream?.type !== "LoadVideo") throw new Error("预览支持原生 Load Video 直接连线；其他上游视频节点请运行后查看规划。");
                        return { file: value(upstream, "file", ""), mode: value(this, `video_mode_${index + 1}`, "可编辑参考") };
                    });
                    const data = {
                        videos,
                        images: continuous(inputs(this, "reference_images", "image")).length,
                        audios: continuous(inputs(this, "reference_audios", "audio")).length,
                        width: value(this, "width", 768), height: value(this, "height", 1024),
                        reference_seconds: value(this, "reference_seconds", 0),
                        video_audio: value(this, "video_audio", false),
                        global_prompt: value(this, "global_prompt", ""),
                        prompt_index: value(this, "prompt_index", null),
                        segment_config: value(this, "segment_config", ""),
                    };
                    const response = await api.fetchApi("/h3_socket_planner/preview", {
                        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
                    });
                    const body = await response.json();
                    if (!response.ok) throw new Error(body.error || response.statusText);
                    status.value = body.text;
                } catch (error) {
                    status.value = `未更新：${error.message}`;
                }
            }, { serialize: false });
            const display = this.addDOMWidget("reference_report", "text", status, { serialize: false });
            display.computeSize = () => [400, 180];
            this.size[0] = Math.max(this.size[0], 430);
            queueMicrotask(() => labelInputs(this));
            return result;
        };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = executed?.apply(this, arguments);
            if (this._h3Report && message?.text) this._h3Report.value = message.text.join("\n");
            return result;
        };
        const changed = nodeType.prototype.onConnectionsChange;
        nodeType.prototype.onConnectionsChange = function () {
            const result = changed?.apply(this, arguments);
            queueMicrotask(() => labelInputs(this));
            if (this._h3Report) this._h3Report.value = "连线已变化，请更新分段。运行时会自动重算。";
            return result;
        };
        const widgetChanged = nodeType.prototype.onWidgetChanged;
        nodeType.prototype.onWidgetChanged = function () {
            const result = widgetChanged?.apply(this, arguments);
            if (this._h3Report) this._h3Report.value = "参数已变化，请更新分段。运行时会自动重算。";
            return result;
        };
    }
});
