# Maya 2022：MaterialX 导入与原生图再导出

2026-11-11 最新批次 50 / 50 个材质完成导入、保存重开、当前原生回读再导出，全部回到 Blender 并进入 C4D Redshift。已增加有界 RGBA、命名 UV／静态矩阵、功能容器、共享参数及独立未标记原生图读取。最新数量、实际采样和边界见[完善报告](editable-materialx-20261111.md)。下文 A1 的 31 / 89 与 422 项测试是初次导入阶段的历史记录。

2026-11-10 已在本机 Maya 2022.5、Python 3.7.7、MtoA 5.3.5.3、Arnold 7.2.5.3 完成受限导入验证。输入是上一轮从 A1 导出的 89 份 `.mtlx`；31 份创建成功并通过保存重开核对，58 份明确阻断。本轮只验证节点结构、参数、贴图引用和连接，没有渲染对照或神经网络校正。

## 在脚本编辑器启动

仓库先执行 `uv sync --extra dev`。Maya 使用 Python 3 模式，在脚本编辑器的 **Python** 页签运行下面两行，替换为本机仓库路径：

```python
import runpy

runpy.run_path(r"仓库绝对路径/_examples/maya_demo_gui.py", run_name="__main__")
```

当前面板的连接选择、导入／导出页与报告布局见[三宿主统一界面](unified-ui.md)。

1. 在导入页添加 `.mtlx` 文件或目录；目录只扫描直属文件，可多选。
2. 点击“检查材质”。此时只调用核心、读取资源信息，不创建节点。
3. 查看报告；接受列出的近似时勾选“允许带警告导入”。错误不能绕过。
4. 点击“导入为新材质”。每次重新检查文件，逐个创建独立节点网络及 shadingEngine，失败不影响其他文件。
5. 点击“选中已导入材质”，在 Hypershade 查看上游连接，按需手动绑定对象。
6. 点击“查看节点与共享参数”，在实际 Node Editor 查看图；左侧控件直接编辑原生发布属性。修改后可选中 `aiStandardSurface`，导出当前图。

默认不替换对象绑定、不修改已有材质。每份创建使用独立撤销块，面板批操作可能被 Maya 合并为一次撤销；支持撤销／重做。撤销、重做或打开场景后清除过期报告。重复运行入口只重建面板，保留已创建的材质。关闭面板即可结束，不需要安装 `.mll` 或修改用户启动文件。

图片路径以 `.mtlx` 所在目录解析，支持现存绝对路径及相对路径；缺图明确阻断。面板保留引用；可先运行[单文档资源打包工具](editable-materialx-20261111.md#c2rgba坐标与便携资源)，再导入资源包中的文档。`aiImage.autoTx` 关闭，避免本次材质导入请求生成派生 `.tx`；实际渲染器的纹理缓存另由对应测试目录记录。

## 节点与架构

调用链为 `ui → controller → RelayService.handle(materialx.convert) → MayaAdapter.create_material`。Maya 只通过公开 `describe`／原生编码辅助接口解释核心返回的目标图。语义映射集中在 Maya 专用字典；不复用 C4DtoA 的整数 code，也不从 Blender 源快照恢复隐藏信息。

Maya 的 Python 3.7 默认使用公开 `iris_relay.transport.JsonSessionService` 调用仓库 `.venv` 中的 Python 3.11+ 核心和 MaterialX 1.39.5；也可在面板选择 [HTTP 服务](http.md)。核心仍不导入 `maya`，不向 Maya 安装 SDK。`iris_relay.plugin.JsonCommandService` 旧导入路径保留。

| MaterialX 功能 | Maya / Arnold 原生实现 |
| --- | --- |
| 标准表面、图片 | `aiStandardSurface`、`aiImage` |
| 切线法线、高度凹凸、显式加权和 | `aiNormalMap`、`aiBump2d`、`aiDot` |
| RGB 分量提取 | `aiColorToFloat`，索引 0/1/2 对应模式 5/6/7 |
| 标量加减乘除、最小／最大值 | `aiAdd`、`aiSubtract`、`aiMultiply`、`aiDivide`、`aiMin`、`aiMax` 的标量子端口 |
| floor、clamp | `aiFloatToInt` 的 floor 模式、`aiClamp` |
| 等于／大于条件、颜色混合 | Maya `condition`、`blendColors` |
| 三分量组合、标量广播 | `aiFloatToRgba`、`aiAdd` 配置；标量到颜色连接显式分发到三个子端口 |
| 标量 power、静态 UV 上下文 | 实测 `aiPow`、`aiUvTransform.uvwMatrix`；RGB 与 Alpha 使用同一 UV 链 |

Blender HSV、ColorRamp、Mix 等功能在核心生成标准数学图，再创建上述节点；共享上游保留一份。功能来源映射到原生容器，共享作者参数发布为可编辑属性；不承诺复刻原 Blender 的原生组类型或像素排版。材质原名保存在 `irisMaterialName`，原生名称使用安全前缀及 Maya 唯一命名。

Autodesk 提供的 [MaterialX Shader](https://help.autodesk.com/cloudhelp/ENU/AR-Maya/files/am-Arnold_for_Maya_User_Guide/shaders/surface/arnold_for_maya_surface_am_MaterialX_Shader_html.html) 可引用 MaterialX 文件。本入口选择逐节点创建，便于检查和编辑；没有据此宣称该插件可以导入所有 Arnold MaterialX 文档。其他 DCC 仍须有各自的字典与宿主证据。

## 初次 A1 实机结果（历史记录）

| 样本 | 尝试／成功 | 原生功能节点 | 有效未连接输入 | 图连接（含表面出口） | 图片节点 |
| --- | --- | --- | --- | --- | --- |
| A1 上一轮导出的文件 | 89 / 31 | 2004 | 1681 | 3771 | 56 |
| HSV 独立夹具 | 5 / 5 | 322 | 275 | 606 | 3 |

计数来自成功项；节点数不含 shadingEngine，连接数按目标逻辑图统计，标量广播的三条物理线计为一个逻辑连接。每条实际物理连线都查询核验。检查原生节点类型、固定模式、有效常量输入、图片文件／色彩空间／UV 集及材质出口，再保存 `.ma`、新建场景、打开该文件重复检查。没有把 JSON 报告本身当成宿主还原证据。

58 份阻断的首要原因：

| 数量 | 错误 | 处理 |
| --- | --- | --- |
| 36 | `MAYA_IMAGE_CHANNELS_UNSUPPORTED` | 四通道图片或实际通道数与文档不一致；未验证 Alpha 解释，整份材质不创建 |
| 17 | `INVALID_VALUE` | 反射权重超过 `aiStandardSurface.specular` 的 0–1 范围；由字典提前阻断，不强行钳制 |
| 5 | `MATERIALX_RESOURCE_UNSUPPORTED` | 图片使用尚未实现的 UV 矩阵变换，保留错误，不改为默认坐标 |

这些是当时检查顺序遇到的原因。新版本已补部分四通道和 UV 能力，但没有重新把这份 A1 旧批次标记通过；不能据首错推算剩余材质修复后的数量。

事务验证通过：单份导入撤销后节点集恢复、重做后图可再次核对、第二次参数写入时注入失败后清理新增节点、已有对象绑定及选择保留。前台 Maya 面板完成两份真实文件的检查、允许警告和导入；撤销后旧成功报告清空。

422 项自动测试通过；核心及 Maya 控制层合计覆盖率 93%，Maya 控制层 97%。覆盖率不包含只能在宿主运行的 adapter / UI；其证据来自上述实机检查。88 份新增导出文件及 171 条贴图路径与上轮哈希清单一致。

本机大文件不入库。结果目录名为 `iris-relay-maya2022-20261110`，其中 `a1-final/` 包含逐文件 JSON、`summary.json` 和 31 份成功材质的 `restored.ma`；`fixtures-release/` 保存独立夹具及事务检查；`source-integrity.json` 记录哈希复核。场景未绑定测试物体，打开后在 Hypershade 查看。

## 遇到的问题与解决方式

| 问题 | 证据与处理 |
| --- | --- |
| Maya 3.7 无法加载现有 Python 3.11 核心 | 拆出无 SDK 依赖的公开协议与子进程服务，核心入口延迟加载；实际 mayapy 调通同一 API |
| Maya 设置的 `PYTHONHOME` / `PYTHONPATH` 污染子进程 | 核心子进程启动失败；仅对子进程环境移除这两个变量，保留宿主环境 |
| MtoA 加载报 DLL 过程找不到 | 在独立测试进程的 `PATH` 前置对应 Maya 2022 / Arnold bin，设置其模块目录；未修改系统 PATH 或安装插件 |
| Maya 将文件路径反斜杠转为正斜杠 | 写入前规范化绝对 URI，避免回读比较将等价路径误报为丢失 |
| RGB 索引 1/2 缺少标准 integer 绑定 | 核心桥接支持整数端口；JSON 数值必须为整数，写 MaterialX 时使用 integer 序列化；原生枚举由字典明确列出 |
| Alpha 标量接颜色需要广播 | 添加标准 `convert float → color3` 的目标实现，并按公开 gray 编码连接三个子端口 |
| 反射权重超出 Maya 硬范围 | 实机 `setAttr` 拒绝；字典新增范围，检查阶段报告，避免创建到一半才失败 |
| 四通道图、UV 变换尚无可信映射 | 保持阻断并记录，未静默忽略、不烘焙、不伪造成功 |

通常已能正常使用 Arnold 的 Maya 会话直接加载 `mtoa` 即可。若出现 DLL 加载错误，应检查 Maya 2022 对应的 MtoA 安装与进程搜索路径；入口只报告错误，不自动修改本机安装或全局配置。

## 验证复现与限制

在已配置 Maya 2022 / MtoA 环境的 PowerShell 中运行，输出目录必须尚不存在：

```powershell
& "本机Maya2022/bin/mayapy.exe" _scripts/verify_maya_import.py --documents "已有mtlx目录" --out "新的验证输出目录" --transactions
```

字典生成入口是 `_scripts/build_maya_dictionary.py`，输入 `dictionaries/catalogs/maya-arnold-7.2.5.3.json`。目录现有 19 种实际创建、查询过的原生类型，字典含 32 个实现配置；不是完整 Maya 节点大全。Maya 与 MtoA 版本由适配器检查，Arnold 版本由字典精确匹配。

目前图片接受实际 1／3 通道，以及具有明确格式／Alpha 说明的有界 4 通道路径；文档声明必须与实际文件相符。PNG 与 Raw RGBA EXR 分别声明关联行为；非 Raw RGBA EXR、当前解码器不能读取的 AVIF／WebP 明确阻断。PNG 零 Alpha 隐藏颜色与过滤差异不能完全恢复。floor 使用 32 位整数输出，极大数值、动态枚举索引和未支持坐标链不宣称等价。窗口不调整工作色彩空间，当前上下文作为警告输出。

已实现受限 Maya 原生材质读取／导出：在场景选中 `aiStandardSurface`，点击面板新增导出按钮并选择输出目录；警告默认阻止写入，文件不覆盖。读取当前参数及连线，经核心生成新文档，不复用导入 XML。`irisImplementation` 提示由当前原生类型和配置重新核验；无提示且匹配歧义、未知节点、多余连接或未支持配置仍拒绝。

最新 50 个材质已通过 Maya 当前原生再导出 → Blender 保存重开；四个功能组夹具还验证发布参数修改后的再次导出。节点、资源与保存重开通过仍不等于 Arnold 与 Cycles 外观一致；首轮真实神经实验单独记录，不扩大此处结构范围。
