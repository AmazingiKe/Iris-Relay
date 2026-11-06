# Iris Relay

Iris Relay 探索跨渲染器的材质转换：以字典建立材质语义对应，以小型神经网络联合校正材质在目标渲染器中的整体外观，通过最小侵入的 DCC 接入层读取和应用材质。

系统只修改材质，不通过改变灯光、相机、曝光或色彩管理来补偿差异。“整体校正”指材质内部多种因素的关联调整，不是一个统一系数，也不是整幅图像的后期处理。

当前已完成初步核心：统一 API、材质图协议、声明式字典、转换与诊断报告、联合校正扩展接口，以及通过同一 API 运行的参考插件。Python 核心不依赖 DCC 运行时；0.4.0 引入官方 MaterialX 1.39.5 SDK。

新的 [MaterialX 核心](_docs/materialx-core.md) 支持官方节点定义查询、`.mtlx` 校验与保存、自定义 NodeDef／子图，以及与现有五类功能的受限原生桥接。文档保存范围大于目标转换范围；专有节点没有目标规则时保留源信息并阻断。Maya Arnold → Blender Cycles 是下一条宿主接入方向，Maya 读取尚未实现。

现已提供 Blender Cycles、C4D Redshift 和 C4DtoA 的真实节点目录与基础字典，包含节点名称、资产 ID、整数 code 和端口绑定。本轮 6 组源材质完成保存重开和原生识别，随后 18 组转换完成目标创建、保存重开及参数／连接核对。详见[原生字典与验证范围](_docs/native-dictionaries.md)。

每端目前支持标准表面、图片、切线法线、高度凹凸与显式 R 通道提取五类功能。统一语义层集中定义功能和端口，通过原生 ID 与配置识别具体实现；多个来源实现可归入同一功能，多个目标实现必须指定唯一默认选择。Blender 提供实验性脚本适配器，C4D 提供独立验证脚本。尚无完整生产插件、训练模型或跨渲染器视觉一致性证明。示例中的 `reference.gloss` 和 `reference.rough` 仍仅用于协议测试。

## 快速开始

需要 Python 3.11+ 和 uv。在仓库根目录执行：

```powershell
uv sync --extra dev
uv run python -m iris_relay --dictionaries _examples/dictionaries --request _examples/convert-request.json
uv run python _examples/plugin_demo.py --transport python
uv run python _examples/plugin_demo.py --transport process
uv run python _examples/materialx_demo.py --out .local/materialx-demo-新编号
```

第一条转换命令输出完整 JSON 报告；两种参考插件调用都会新建内存中的目标材质，并验证源材质保持不变。示例将光泽度 `0.8` 转成粗糙度约 `0.2`，保留底色贴图连接、色彩空间和 UV 描述；这是参考规则验证，不是神经校正或视觉等价性证明。

没有 uv 时可使用独立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install "materialx==1.39.5"
.\.venv\Scripts\python.exe -m iris_relay --dictionaries _examples/dictionaries --request _examples/convert-request.json
```

## Blender GUI demo

脚本入口为 [`_examples/blender_demo_gui.py`](_examples/blender_demo_gui.py)，现已接入真实 MaterialX 导出。在 Blender 文本编辑器打开它，按需填写顶部 `REPO_ROOT` 为仓库根目录的绝对路径，然后点击“运行脚本”。从仓库原位置打开时可以留空自动定位；复制到未保存文本块或移到别处时需要填写路径。本机路径只在 Blender 中修改，不提交；不需要安装 Blender 插件。

运行后在文本编辑器或 3D 视图的 `N` 侧栏选择 **Iris Relay** 标签，侧栏过窄时可拖动左边界加宽：

1. 选择材质，或点击“使用活动材质”。
2. 点击“检查材质”，读取当前节点图，调用 `materialx.export` 和 `materialx.validate`，查看下方诊断。此操作不写文件。
3. 选择尚不存在的 `.mtlx` 文件绝对路径，父目录须已存在。
4. 若有近似转换警告，查看诊断后按需勾选“允许带警告导出”；默认不保存有警告的结果，阻断错误不能通过勾选绕过。
5. 点击“导出 .mtlx”。每次导出重新读取并校验当前材质，成功后明确显示“已导出”。可通过“复制完整报告”取得核心响应、源图快照和诊断。

导出采用排他创建，不覆盖现有文件；写入失败会尝试清理本次创建的残留。源材质、绑定和贴图保持不变，不改变场景渲染条件。贴图保留原文件路径引用，不复制或打包资源，因此输出不是独立资源包。文件可保存、目标可转换和外观一致是不同结论。

界面位于 `plugins/blender/ui.py`，`controller.py` 负责核心连接、检查和保存流程，`adapter.py` 负责 Blender 材质读写；`from plugins.blender import BlenderAdapter` 保持兼容。首次操作时优先使用仓库已有 `.venv` 通过公开 `JsonCommandService` 调用核心，不向 Blender 安装依赖。不存在 `.venv` 时尝试进程内服务，两种方式都要求 MaterialX 1.39.5；依赖缺失或版本不同会明确报错。本机 Blender 5.2.3 自带 SDK 为 1.39.4，因此本轮使用仓库环境的 1.39.5。

GUI 按需加载核心；重复运行入口会重新加载宿主模块、清空输入和诊断。关闭 Blender 后不会自动启用。需要在当前会话移除界面时，在 Blender Python 控制台执行 `from plugins.blender import ui; ui.unregister()`。

当前仍受既有五类节点及精确版本字典限制，未知节点、节点组、未支持的参数或连接会阻断。建议先用简单 Principled BSDF 材质测试，再加入默认 UV 的外部图片、法线或高度链路，具体范围见[原生字典文档](_docs/native-dictionaries.md)。

独立宿主交互逻辑验证（不会操作当前打开的 Blender 场景）：

```powershell
& "本机blender.exe路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_gui.py
```

该检查覆盖入口重复运行、真实导出及文档校验、警告策略、已有文件保护、未知节点阻断、源材质保护、图片法线／高度链路、注册卸载和导入兼容；真实面板绘制与文件浏览器还需在 Blender 前台检查。初版 GUI 的界面验证记录保留在 Git 历史中；本轮未扩大转换规则，也没有新增渲染外观证据。

2026-11-06 验证：180 项自动测试、15 组 Blender 5.2.3 LTS 宿主检查通过，核心及导出控制层合计覆盖率 91%（控制层 95%）。独立前台实例完成检查、警告确认和实际保存，保存后的文件重新读回并通过 MaterialX 校验。

## 统一接口

所有插件依赖 `RelayService.handle(request) -> response`。进程内使用 `RelayAPI`；跨进程使用同一 JSON 协议，Python 侧可使用 `JsonCommandService`。插件只读写宿主材质，转换和校正规则集中在核心。

API 提供能力查询、通用功能定义查询（`semantics`）、原生模式识别（`identify`）、原生绑定查询、材质校验和转换。真实配置同时匹配宿主、渲染器与节点系统的版本。版本不匹配、未知节点、无法表达的连接和非法数值均有明确错误；缺少模型时可返回基础转换结果及警告。插件辅助接口默认不应用带警告的方案，也不会自动替换场景中的材质绑定。

## 目录

```text
iris_relay/            核心、公开 API、插件辅助接口与命令行
dictionaries/          真实转换字典；catalogs/ 保存原生节点探测目录
plugins/               宿主接入层，按实际宿主分目录
  blender/             GUI（ui.py）、导出控制（controller.py）、材质读写（adapter.py）
_scripts/              节点探测、字典生成和独立宿主验证
_examples/             可执行参考插件、GUI 测试入口、请求和示例字典
_tests/                图转换、接口、失败回退和跨进程测试
_docs/                 API 文档和架构决策
```

核心 `iris_relay/` 与宿主接入 `plugins/` 在根目录并列，不使用 `src/`。示例和验证脚本自行定位仓库根目录；第三方插件在导入核心前将仓库根目录加入 Python 搜索路径。按实际内容创建目录，不预建空目录。字典的具体 code 与支持边界见[原生字典文档](_docs/native-dictionaries.md)。

## 开发检查

```powershell
uv run --extra dev pytest -q
uv run --extra dev coverage run -m pytest -q
uv run --extra dev coverage report
uv run --extra dev ruff check iris_relay _tests _examples _scripts plugins
uv run --extra dev ruff format --check iris_relay _tests _examples _scripts plugins
```

测试包含单元、API 集成及 JSON 子进程流程；覆盖率门槛为 80%。`uv.lock` 固定开发依赖。当前直接从仓库根目录导入 `iris_relay/`。`uv sync` 只安装第三方依赖，不安装项目自身；开发检查不生成源码发行包或 wheel，不需要 `dist/`。

## 文档与后续工作

- [文档索引](_docs/README.md)
- [ADR-0001：材质转换与神经校正架构](_docs/adr/0001-材质转换与神经校正架构.md)
- [ADR-0002：统一插件接口与初步核心实现](_docs/adr/0002-统一插件接口与初步核心实现.md)
- [统一插件 API v1](_docs/api.md)
- [MaterialX 核心与调用示例](_docs/materialx-core.md)
- [原生字典与实机验证](_docs/native-dictionaries.md)
- [ADR-0003：原生节点绑定与宿主验证](_docs/adr/0003-原生节点绑定与宿主验证.md)
- [ADR-0004：统一节点功能定义与原生实现选择](_docs/adr/0004-统一节点功能定义与原生实现选择.md)
- [ADR-0005：MaterialX 材质图核心与自定义节点扩展](_docs/adr/0005-MaterialX材质图核心与自定义节点扩展.md)
- [协作与开发约定](AGENTS.md)

下一步扩展真实材质导出与节点覆盖，建立固定场景的渲染对照基线，再训练和验证联合校正。当前核心支持字典声明的图连接，但非恒等变换遇到贴图连接时仍会阻断，尚不自动插入变换节点。
