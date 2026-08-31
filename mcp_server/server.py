"""P1 MCP Server：供真实模型侧调用的工具集。

工具收敛为三个（§2.5），不做一个组件一个 tool——工具数量爆炸会显著降低
模型的工具选择准确率：

  render(component_type, params)          呈现型
  ask(card_id | component_type, params)   采集型，阻塞等待用户输入
  confirm(action_desc, risk_level)        控制型确认闸门

本文件是薄适配层：把工具调用转成平台组件协议信封，经平台 API 下发给渲染器。
运行（需安装 mcp 包：pip install mcp）：
  python -m mcp_server.server
在 Claude Code / 其他 MCP 宿主中注册为 stdio server 即可。

注意：本项目的对话演示（web/chat.html）不经过本文件——演示环境中模型是
Mock 的，工具调用由服务端直接模拟。本文件用于对接真实模型时的生产形态。
"""
import json
import urllib.request

PLATFORM = "http://127.0.0.1:8787"

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    FastMCP = None


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        PLATFORM + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


if FastMCP:
    mcp = FastMCP("rich-interaction-components")

    @mcp.tool()
    def render(component_type: str, params: dict) -> str:
        """在对话流中渲染一个呈现型组件（图表、表格、时间线、对比矩阵等）。
        只描述信息结构与数据，不要包含任何颜色、尺寸、位置等视觉表现。
        component_type 取值见组件协议 Schema 的呈现型清单。"""
        envelope = {"schema_version": "1.0.0", "component_type": component_type,
                    "semantic_category": "present", "trigger_source": "model_tool_call",
                    "params": params}
        return json.dumps({"rendered": True, "envelope": envelope}, ensure_ascii=False)

    @mcp.tool()
    def ask(component_type: str, params: dict, card_id: str = None) -> str:
        """向用户发起一次结构化采集（单选、多选、表单、方案比选等），阻塞等待用户输入。
        缺什么信息、何时需要用户决策，由你判断；组件长什么样由平台卡片配置决定。"""
        envelope = {"schema_version": "1.0.0", "component_type": component_type,
                    "semantic_category": "collect", "trigger_source": "model_tool_call",
                    "card_ref": {"card_id": card_id} if card_id else None, "params": params}
        return json.dumps({"awaiting_user": True, "envelope": envelope}, ensure_ascii=False)

    @mcp.tool()
    def confirm(action_desc: str, risk_level: str = "high") -> str:
        """高风险动作（写操作、外发、付费调用）前的确认闸门。未获用户确认前不得执行该动作。"""
        envelope = {"schema_version": "1.0.0", "component_type": "control.confirm",
                    "semantic_category": "control", "trigger_source": "model_tool_call",
                    "params": {"action_desc": action_desc, "risk_level": risk_level}}
        return json.dumps({"awaiting_confirmation": True, "envelope": envelope}, ensure_ascii=False)

    if __name__ == "__main__":
        mcp.run()
else:
    if __name__ == "__main__":
        print("未安装 mcp 包。运行 pip install mcp 后重试。")
