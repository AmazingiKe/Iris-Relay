"""三宿主共享的界面用语、只读摘要和连接检查；不依赖宿主或转换实现。"""

TITLE = "Iris Relay"
DEFAULT_URL = "http://127.0.0.1:8765"
TRANSPORT_ITEMS = (("AUTO", "自动"), ("LOCAL", "本地核心"), ("HTTP", "HTTP 服务"))


def status_label(status, created=False, saved=False):
    """区分检查通过和实际完成；阻断优先于残留的创建或保存标记。"""
    if status == "error":
        return "已阻断"
    suffix = " · 有警告" if status == "warning" else ""
    if created:
        return "已导入" + suffix
    if saved:
        return "已导出" + suffix
    return {"ok": "检查通过", "warning": "需要确认", "busy": "处理中"}.get(status, "未检查")


def issue_counts(issues):
    """汇总展示等级，未知等级仍计入提示，不影响原始报告保留。"""
    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues or []:
        severity = issue.get("severity", "info")
        counts[severity if severity in counts else "info"] += 1
    return counts


def format_report(result):
    """将宿主结果转为可读摘要，保留全部问题、位置及文件信息，不重写完整报告。"""
    if not isinstance(result, dict):
        from dataclasses import asdict, is_dataclass

        result = asdict(result) if is_dataclass(result) else {}
    created = result.get("created_material") or result.get("created")
    saved = result.get("saved_path")
    lines = [status_label(result.get("status"), bool(created), bool(saved))]
    if result.get("message"):
        lines.append(str(result["message"]))
    for key, label in (("source_path", "输入"), ("saved_path", "输出")):
        if result.get(key):
            lines.append(label + "：" + str(result[key]))
    if created:
        name = created.get("material", "") if isinstance(created, dict) else created
        if name:
            lines.append("新材质：" + str(name))
    issues = result.get("issues", [])
    counts = issue_counts(issues)
    if issues:
        lines.extend(
            [
                "",
                "问题：{} 项阻断 · {} 项警告 · {} 项提示".format(
                    counts["error"], counts["warning"], counts["info"]
                ),
            ]
        )
    for issue in issues:
        severity = {"error": "阻断", "warning": "警告", "info": "提示"}.get(
            issue.get("severity"), "提示"
        )
        code = str(issue.get("code") or "")
        lines.append("[{}] {} {}".format(severity, code, issue.get("message") or "").rstrip())
        location = " / ".join(str(issue[key]) for key in ("node", "port") if issue.get(key))
        if location:
            lines.append("  位置：" + location)
    return "\n".join(lines)


def close_service(service):
    """只关闭面板持有的本地会话；HTTP 客户端没有远程停服行为。"""
    if service is not None and hasattr(service, "close"):
        service.close()


def connect_service(factory, mode="AUTO", url=DEFAULT_URL):
    """创建选定服务并查询真实能力，失败时释放新会话，不产生假连接状态。"""
    from iris_relay.native import request
    from iris_relay.protocol import ValidationError

    service = factory(transport=mode, url=url)
    try:
        response = request(service, "capabilities")
        if response["status"] == "error":
            error = response.get("error") or {}
            raise ValidationError(
                error.get("message", "服务能力查询失败"), error.get("code", "CONNECTION_FAILED")
            )
        capabilities = response.get("result") or {}
        materialx = capabilities.get("materialx") or {}
        if not materialx.get("available") or materialx.get("version") != "1.39.5":
            raise ValidationError("所选核心需要 MaterialX 1.39.5", "MATERIALX_UNAVAILABLE")
        return service, capabilities
    except Exception:
        close_service(service)
        raise
