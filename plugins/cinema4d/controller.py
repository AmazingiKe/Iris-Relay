"""Cinema 4D 的窗口状态与当前选择；材质转换和文件操作复用公共控制层。"""

from iris_relay.protocol import ValidationError
from plugins import core
from plugins.presentation import close_service, connect_service


def make_adapter(document, service):
    """为当前文档建立适配器；实际版本和节点能力由既有适配器核验。"""
    from .adapter import Cinema4DAdapter

    if document is None:
        raise ValidationError("请先打开一个 Cinema 4D 文档", "DOCUMENT_REQUIRED")
    return Cinema4DAdapter(document, service)


def stop_threads():
    """在主线程修改用户文档前，按宿主 SDK 要求停止场景后台计算。"""
    import c4d

    c4d.StopAllThreads()


def document_signature(document):
    """记录当前文档、选择和图修改戳；撤销、切换场景或编辑后清除旧报告。"""
    import c4d

    if document is None:
        return None
    from .adapter import SPACE

    signatures = []
    for material in document.GetMaterials():
        stamp = None
        # 场景可以混合标准、其他渲染器及 Redshift 材质；无目标图不是窗口错误。
        try:
            reference = material.GetNodeMaterialReference()
            graph = reference.GetGraph(SPACE) if reference is not None else None
            if graph is not None and not graph.IsNullValue():
                stamp = str(graph.GetModificationStamp())
        except Exception:
            pass
        signatures.append(
            (
                material,
                material.GetName(),
                # Redshift 后台预览也递增 DATA，语义编辑以实际图修改戳为准。
                None if stamp is not None else material.GetDirty(c4d.DIRTYFLAGS_DATA),
                stamp,
            )
        )
    return (
        document,
        document.GetDirty(c4d.DIRTYFLAGS_DATA),
        document.GetActiveMaterial(),
        tuple(signatures),
    )


def failure(exc):
    """将接入层异常转换成可复制的报告，不把宿主对象带入 JSON。"""
    return {
        "status": "error",
        "message": str(exc),
        "issues": [
            {"severity": "error", "code": getattr(exc, "code", "HOST_ERROR"), "message": str(exc)}
        ],
        "created": None,
        "saved_path": None,
        "appearance_verified": False,
    }


class PanelController:
    """持有本窗口自己的服务和报告，不修改全局环境变量或材质绑定。"""

    def __init__(self):
        self.service = None
        self.capabilities = None
        self.result = None
        self.signature = None
        self._track_selection = True

    def reset_connection(self):
        """切换或关闭窗口时释放自己持有的服务，并使旧报告失效。"""
        service, self.service = self.service, None
        self.capabilities = None
        self.clear_report()
        close_service(service)

    def connect(self, mode, url):
        """以真实能力查询确认连接；失败不退回其他传输，也不保留旧成功。"""
        self.reset_connection()
        self.service, self.capabilities = connect_service(core.create_service, mode, url)
        return self.capabilities

    def clear_report(self):
        """输入、连接或场景改变后，显式取消旧报告的当前有效性。"""
        self.result = None
        self.signature = None

    def _capture_signature(self, document):
        """导入结果关联创建图而非当前选择；导出报告额外关联所选源材质。"""
        signature = document_signature(document)
        if not self._track_selection and signature is not None and len(signature) >= 4:
            return signature[:2] + (None,) + signature[3:]
        return signature

    def record_result(self, result, document, *, track_selection=True):
        """成功和失败都关联当前场景，避免轮询立即抹掉连接失败等新诊断。"""
        self.result = result
        self._track_selection = track_selection
        try:
            self.signature = self._capture_signature(document)
        except Exception:
            self.signature = None
        return result

    def invalidate_if_changed(self, document):
        """只在存在报告时核验场景快照，返回本次是否清除了过期结果。"""
        if self.result is not None and self.signature != self._capture_signature(document):
            self.clear_report()
            return True
        return False

    def perform(self, action, document, path="", *, accept_warnings=False):
        """从当前场景重新读取并执行；任何失败生成独立诊断，不沿用历史检查。"""
        self.clear_report()
        try:
            if self.service is None:
                raise ValidationError("请先检测连接", "SERVICE_NOT_CONNECTED")
            adapter = make_adapter(document, self.service)
            if action == "check_import":
                result = core.check_import(adapter, self.service, path, adapter.target)
            elif action == "import":
                stop_threads()
                result = core.import_material(
                    adapter, self.service, path, adapter.target, accept_warnings=accept_warnings
                )
            elif action in {"check_export", "export"}:
                material = document.GetActiveMaterial()
                if material is None:
                    raise ValidationError("请在材质管理器中选中一个材质", "MATERIAL_NOT_SELECTED")
                result = (
                    core.check_export(adapter, self.service, material)
                    if action == "check_export"
                    else core.export_material(
                        adapter, self.service, material, path, accept_warnings=accept_warnings
                    )
                )
            else:
                raise ValidationError("未知界面操作", "UNKNOWN_ACTION")
        except Exception as exc:
            result = failure(exc)
        return self.record_result(
            result, document, track_selection=action in {"check_export", "export"}
        )
