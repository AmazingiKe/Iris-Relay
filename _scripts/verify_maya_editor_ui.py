"""仅在本工具新启动的 Maya GUI 进程检查原生节点窗口和参数控件。"""

import json
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    """通过私有启动标记读取测试副本，捕获真实 Qt 窗口后只退出本工具的 GUI 进程。"""
    from maya import cmds
    from PySide2.QtCore import QTimer

    job_path = os.environ.get("IRIS_EDITOR_UI_JOB")
    if not job_path:
        raise RuntimeError("此入口只能通过独立验证进程启动")
    job = json.loads(Path(job_path).read_text("utf8"))
    output = Path(job["out"])
    output.mkdir(parents=True, exist_ok=False)
    try:
        cmds.loadPlugin("mtoa", quiet=True)
        cmds.file(job["scene"], open=True, force=True, prompt=False)
        from plugins.maya.ui import show_editable_graph

        state = show_editable_graph(job["created"])
        cmds.nodeEditor(state["editor"], edit=True, enableOpenGL=False)

        def capture():
            """等待真实布局完成，记录显示节点与接口，再截取窗口内容。"""
            try:
                from maya.OpenMayaUI import MQtUtil
                from PySide2.QtWidgets import QWidget
                from shiboken2 import wrapInstance

                widget = wrapInstance(int(MQtUtil.findWindow(state["window"])), QWidget)
                widget.setStyleSheet('QWidget { font-family: "Microsoft YaHei UI"; }')
                assert widget.grab().save(str(output / "editor.png"))
                nodes = cmds.nodeEditor(state["editor"], query=True, getNodeList=True) or []
                (output / "summary.json").write_text(
                    json.dumps(
                        {
                            "status": "captured",
                            "displayed_nodes": nodes,
                            "window": state["window"],
                            "published_controls": sum(
                                len(g["published"]) for g in job["created"].get("containers", [])
                            ),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf8",
                )
                assert job["created"]["material"] in nodes
            except Exception:
                (output / "error.txt").write_text(traceback.format_exc(), encoding="utf8")
            finally:
                cmds.quit(force=True)

        QTimer.singleShot(5000, capture)
    except Exception:
        (output / "error.txt").write_text(traceback.format_exc(), encoding="utf8")
        cmds.quit(force=True)


if __name__ == "__main__":
    main()
