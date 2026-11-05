# 原生节点字典与实机验证

真实字典位于 [`dictionaries/`](../dictionaries/)，不再使用示例节点名代替软件 API。当前覆盖本机已核验的 Blender Cycles、Cinema 4D Redshift 和 C4DtoA，版本精确匹配；尚未覆盖所有 DCC、其他版本或第三方节点。

精确匹配指渲染器版本、宿主版本与节点系统。C4DtoA 4.8.3.1 是此次实机核验条件，核心目前没有独立的 C4DtoA 插件版本匹配字段。

## 文件和覆盖范围

| 宿主／渲染器 | 完整探测目录 | 可转换字典 | 实机结果 |
| --- | --- | --- | --- |
| Blender 5.2.3 LTS／Cycles 5.2.3 | [目录](../dictionaries/catalogs/blender-cycles-5.2.3.json) | [字典](../dictionaries/blender-cycles-5.2.3.json) | 发现 100 种 ShaderNode 类型，99 种可实例化 |
| Cinema 4D 2025.3.2／Redshift 2025.3.0 | [目录](../dictionaries/catalogs/cinema4d-redshift-2025.3.0.json) | [字典](../dictionaries/cinema4d-redshift-2025.3.0.json) | 154 个节点资产可实例化 |
| Cinema 4D 2025.3.2／Arnold 7.4.3.0，C4DtoA 4.8.3.1 | [目录](../dictionaries/catalogs/cinema4d-arnold-7.4.3.0.json) | [字典](../dictionaries/cinema4d-arnold-7.4.3.0.json) | 131 个内置 shader／custom shader 可实例化 |

目录记录类型名称、字符串 ID 或整数 code、端口、默认值及探测状态。Blender 的 `ShaderNodeCustomGroup` 不能直接实例化，保留失败原因。动态节点组、用户资产、第三方程序生成节点和不同模式下新增的端口不属于此次完整性范围。个别 Arnold 类型的头文件没有可提取的参数表，个别 Redshift 端口的 Python 值不可读；目录明确保留空列表或 `unreadable`，不猜测数值。

**收录 385 个类型不等于支持 385 个节点互换。** 每套可转换字典目前包含标准表面、图片、切线空间法线三类节点，共 9 个原生实现。目录用于查 code、补充映射和发现缺口；核心只加载 `dictionaries/` 顶层字典，不把 `catalogs/` 当成转换规则。

## 真实名称与 code 示例

Redshift 下表中的 `RS.` 表示完整前缀 `com.redshift3d.redshift4c4d.nodes.core.`，JSON 中保存的是完整 ID。

| 含义 | Blender | C4D Redshift | C4DtoA |
| --- | --- | --- | --- |
| 标准表面节点 | `ShaderNodeBsdfPrincipled` | `RS.standardmaterial` | `standard_surface` → `314733630` |
| 底色输入 | `Base Color` | `RS.standardmaterial.base_color` | `base_color` → `1044225467` |
| 粗糙度输入 | `Roughness` | `RS.standardmaterial.refl_roughness` | `specular_roughness` → `1876347704` |
| 图片节点 | `ShaderNodeTexImage` | `RS.texturesampler` | `image` → `262700200` |
| 图片资源 | `image` 属性，协议别名 `@image` | `RS.texturesampler.tex0/path` | `filename` → `1737748425` |
| 法线节点 | `ShaderNodeNormalMap` | `RS.bumpmap`，`inputtype=1` | `normal_map` → `1512478027` |
| 法线颜色输入 | `Color` | `RS.bumpmap.input` | `input` → `2075543287` |

显示名称不作为可靠键：Blender 使用 socket identifier；Redshift 使用资产／端口 ID；C4DtoA 还需要节点整数 code 和参数整数 code。`native.id` 保存实际读写标识，字典键保留可读名称。Redshift 图片路径和色彩空间是 `tex0` 下的子端口，不能当成平级端口查找。

## 已实现的转换边界

标准表面映射底色、金属度、反射粗糙度／IOR／权重／颜色／各向异性、漫反射粗糙度、清漆、Sheen、自发光、透明度和法线等语义。不同 BSDF、层间能量分配及切线约定有差异，因此报告会出现 `SHADING_MODEL_APPROXIMATION`，不宣称视觉等价。

- Cycles 的 Specular IOR Level 使用声明式 `scale` 映射到反射权重，仅是基础近似。Alpha 可展开成灰度颜色；反向遇到非灰度透明颜色时阻断。
- 未建立共同语义的输入只在等于原生默认值且未连接时允许省略，并保留源快照和 `NATIVE_DEFAULT_OMITTED` 记录。修改后的 SSS、透射、特殊采样等参数不会静默丢弃；目标不能表达时阻断。
- 非恒等变换驱动的连接仍然阻断，尚未自动插入反相、缩放等节点。未使用的已知输出允许省略；参与连接或作为最终输出的端口不能丢弃。
- 图片验证限定存在的外部单张文件、明确色彩空间和默认 UV。Blender 适配器拒绝内嵌图、序列、UDIM、非默认投影／采样配置及未知节点。Redshift 的部分二维 UV 控件尚不能可靠读回，不能据此声称支持任意现有图片节点导出。
- 法线节点当前验证了常量颜色、强度以及连接至标准表面的链路；尚未验证任意法线贴图的方向、切线重建和视觉结果。

## 颜色约定

核心颜色值使用线性数值。Blender RGBA 颜色输入通过 `rgb` 编码处理；非默认 Alpha 不会静默丢弃。C4DtoA 的颜色参数在此次 Basic／sRGB 输入／Linear Workflow 文档中按 sRGB 编码读写，使用 `srgb` 编码转换；Redshift 节点值使用线性数值。

实机证据中保存 C4D 文档上下文：`DOCUMENT_COLOR_MANAGEMENT=0`、`DOCUMENT_COLORPROFILE=0`、`DOCUMENT_LINEARWORKFLOW=1`。这不是对 OCIO／ACES 场景的兼容声明，也不允许插件更改用户色彩管理来匹配结果。接入其他颜色上下文前必须补转换与验证。资源的 `color_space` 仍由宿主解释；核心不会处理贴图像素。

## 插件如何获取字典

插件通过同一 `RelayService.handle` API 的 `describe` 操作获取绑定，不自行加载或复制转换规则：

```python
from iris_relay import RelayAPI

api = RelayAPI.from_directory("dictionaries")
response = api.handle({
    "api_version": "1", "request_id": "查询原生绑定", "operation": "describe",
    "payload": {"renderer": {
        "id": "arnold", "version": "7.4.3.0", "host": "cinema4d",
        "host_version": "2025.3.2", "node_system": "arnold.gvnodes"
    }}
})
dictionary = response["result"]["dictionary"]
fingerprint = response["result"]["fingerprint"]
```

返回 `dictionary` 和内容 `fingerprint`。参考实现 [`plugins/blender.py`](../plugins/blender.py) 使用该操作完成受限节点图的读写、创建新材质及失败清理；目前是脚本模块，没有菜单安装器或完整交互式撤销。C4D 读写代码目前用于独立集成验证，不是现成的 C4D 场景转换插件。

## 复现实机验证

安装开发依赖后，将路径替换为本机实际可执行文件；输出目录必须不存在：

```powershell
uv run python scripts/verify_native.py --blender "你的路径/blender.exe" --c4dpy "你的路径/c4dpy.exe" --out .local/native-check-新编号
```

查看 `--out` 目录中的 `summary.json`，其中包含本次 `run_id`、总体 `status`、宿主退出码及宿主报告。日志为 `blender.log`、`cinema4d.log`，每个方向还保存独立的核心转换报告。

脚本运行独立 Blender 和 c4dpy 进程，不打开用户场景，不修改软件配置或授权。测试根据字典构造夹具，经过统一 API 转换，在实际宿主中创建节点、设置值、连接图片／法线、保存 `.blend`／`.c4d`，重新打开并核对输入、资源和输出连接。它不等于从任意现有 C4D 材质完整导出。

2026-11-04 本机验证结果：三个目标各接收三个来源，共 9 组（6 个跨渲染器方向及 3 个同配置流程）通过；重开后检查共 759 个输入、27 条含材质出口的连接。Blender 还验证了源材质未变和缺失资源时清理新建数据。结果必须同时满足进程退出码、此次 `run_id` 以及报告 `status=ok`，不能只根据 c4dpy 退出码判断成功。

本轮未进行可比条件下的渲染对照，未训练神经网络，`appearance_verified` 保持 `false`。下一阶段需要真实材质样本、固定场景渲染与联合校正实验。

## 更新目录

[`probe_blender.py`](../scripts/probe_blender.py) 在 Blender 中运行；[`probe_cinema4d.py`](../scripts/probe_cinema4d.py) 在 c4dpy 中运行。设置环境变量 `IRIS_RELAY_OUTPUT` 指向不存在的 JSON 文件。探测器从已安装 API／资源头文件获取标识，并实际实例化节点；新版本应重新探测，不能复制旧版本号。

```powershell
uv run python scripts/build_native_dictionaries.py --blender 本次Blender探测.json --cinema4d 本次C4D探测.json --out 新字典目录
```

审核生成内容后再更新仓库字典，重新执行离线测试与实机验证。源码包附带字典、插件模块及脚本；wheel 仅提供独立核心，部署时需另行提供字典目录与宿主脚本。

实现依据包括 [Blender ShaderNode API](https://docs.blender.org/api/current/bpy.types.ShaderNode.html)、[Maxon GraphNode API](https://developers.maxon.net/docs/py/2025_3_1/modules/maxon_generated/frameworks/graph/datatype/maxon.GraphNode.html)、[C4DtoA Linear Workflow 说明](https://help.autodesk.com/cloudhelp/ENU/AR-Cinema4D/files/ci-tutorials/ci-rendering-tutorials/arnold_for_cinema_4d_ci_Rendering_Tutorials_ci_Linear_Workflow_html.html)，以及目录记录的本机资源与运行时结果。节点 code 和版本以此次本机探测为准。
