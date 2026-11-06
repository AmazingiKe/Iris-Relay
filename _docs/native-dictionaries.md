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

**收录 385 个类型不等于支持 385 个节点互换。** 每套可转换字典目前包含标准表面、图片、切线空间法线、高度凹凸与显式 R 通道提取五类功能，共 15 个实现配置。Redshift 的高度凹凸与切线法线共用节点资产，模式不同，因此实现配置数不等于原生节点类型数。目录用于查 code、补充映射和发现缺口；核心只加载 `dictionaries/` 顶层字典，不把 `catalogs/` 当成转换规则。

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
| 高度凹凸 | `ShaderNodeBump`，`invert=false` | `RS.bumpmap`，`inputtype=0` | `bump2d` → `184848913` |
| 高度信号输入 | `Height` | `RS.bumpmap.input`，标量以灰度三通道读写 | `bump_map` → `1722542866` |
| 高度幅度 | `Distance` | `RS.bumpmap.scale` | `bump_height` → `149592681` |
| 显式 R 通道提取 | `ShaderNodeSeparateColor`，RGB 模式、Red 输出 | `RS.rscolorsplitter`，outr 输出 | `rgb_to_float` → `420837449`，Mode=R |

Arnold `rgb_to_float` 没有对应独立参数头文件；Input code `1582091381`、Mode code `1609892874` 及枚举 `5 → r` 由实机 `GetDescription` 动态描述取得，保存在探测目录的 `runtime_description` 记录中。探测脚本包含这一明确节点的补充读取；其他空参数目录不据此推断为支持。

## 通用功能如何定义和识别

集中定义在 [`iris_relay/semantics.py`](../iris_relay/semantics.py)，插件通过 `semantics` 查询，不需要导入核心内部模块。原生字典声明 `semantic_schema_version: "1"`，加载时校验功能、端口和类型，不再只依赖分散的语义名称。

| 通用功能 | 含义 | 主要输入 → 输出 |
| --- | --- | --- |
| `texture.image` | 图片采样，资源保留色彩解释和坐标 | 图片资源 → RGB / 可选 Alpha |
| `normal.tangent` | 切线空间编码法线解码 | 法线颜色、强度 → 法线向量 |
| `bump.height` | 标量高度场扰动法线 | 高度、幅度、可选基础法线 → 法线向量 |
| `channel.red` | 显式通道选择 | RGB → R 标量 |

来源适配器从 `describe` 获取需要读取的原生属性，把实际 ID 和模式发给 `identify`；核心返回实现键与通用功能。多个原生实现可以归入同一功能；一个原生 ID 也可以按模式拆成不同功能。目标有多个实现时必须指定唯一 `target_default`，否则报告歧义并保留源材质，不按列表顺序选择。

例如 Redshift 图片类型保持 `texture.image`，其 `bumpmap` 模式 0 对应 `bump.height`、模式 1 对应 `normal.tangent`。高度实现键为 `com.redshift3d.redshift4c4d.nodes.core.bumpmap.height`，但真正创建的资产 ID 仍为不带 `.height` 的 `bumpmap`。添加宿主只需补充本宿主与这些通用功能的绑定，无需编写每对宿主之间的转换表。

USD/UsdShade 与 MaterialX 的早期职责评估见 [ADR-0004](adr/0004-统一节点功能定义与原生实现选择.md)。0.4.0 按 [ADR-0005](adr/0005-MaterialX材质图核心与自定义节点扩展.md) 新增 MaterialX 文档核心和受限原生桥接，见[调用与边界](materialx-core.md)；尚无 USD 文件导入导出，不宣称各渲染器执行语义相同。

显示名称不作为可靠键：Blender 使用 socket identifier；Redshift 使用资产／端口 ID；C4DtoA 还需要节点整数 code 和参数整数 code。`native.id` 保存实际读写标识，字典键保留可读名称。Redshift 图片路径和色彩空间是 `tex0` 下的子端口，不能当成平级端口查找。

## 已实现的转换边界

标准表面映射底色、金属度、反射粗糙度／IOR／权重／颜色／各向异性、漫反射粗糙度、清漆、Sheen、自发光、透明度和法线等语义。不同 BSDF、层间能量分配及切线约定有差异，因此报告会出现 `SHADING_MODEL_APPROXIMATION`，不宣称视觉等价。

- Cycles 的 Specular IOR Level 使用声明式 `scale` 映射到反射权重，仅是基础近似。Alpha 可展开成灰度颜色；反向遇到非灰度透明颜色时阻断。
- 未建立共同语义的输入只在等于原生默认值且未连接时允许省略，并保留源快照和 `NATIVE_DEFAULT_OMITTED` 记录。修改后的 SSS、透射、特殊采样等参数不会静默丢弃；目标不能表达时阻断。
- 非恒等变换驱动的连接仍然阻断，尚未自动插入反相、缩放等节点。未使用的已知输出允许省略；参与连接或作为最终输出的端口不能丢弃。
- 图片验证限定存在的外部单张文件、明确色彩空间和默认 UV。Blender 适配器拒绝内嵌图、序列、UDIM、非默认投影／采样配置及未知节点。Redshift 的部分二维 UV 控件尚不能可靠读回，不能据此声称支持任意现有图片节点导出。
- 切线法线与高度凹凸分开定义。高度幅度是有近似警告的基础映射，不证明各宿主单位和空间导数等价；Blender Strength、Filter Width 等无共同映射的非默认参数继续阻断。
- 高度输入要求标量。显式 `channel.red` 可以形成图片 → R 通道 → 高度凹凸的连接；直接 RGB → 高度返回类型错误，不隐式取平均。当前不支持自动插入节点、其他通道或颜色计算。数据纹理保留 `Raw` 等解释，不修改场景色彩管理。
- 尚未验证任意法线贴图的方向、切线重建和视觉结果，连接正确不等于外观一致。

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

返回 `dictionary` 和内容 `fingerprint`。参考实现 [`plugins/blender/adapter.py`](../plugins/blender/adapter.py) 使用该操作完成受限节点图的读写、创建新材质及失败清理；包入口保持原有 `BlenderAdapter` 导入兼容。另有 [GUI demo](../_examples/blender_demo_gui.py) 验证界面交互，尚未接入真实导出，没有菜单安装器或完整交互式撤销。C4D 读写代码目前用于独立集成验证，不是现成的 C4D 场景转换插件。

## 复现实机验证

安装开发依赖后，将路径替换为本机实际可执行文件；输出目录必须不存在：

```powershell
uv run python _scripts/verify_native.py --blender "你的路径/blender.exe" --c4dpy "你的路径/c4dpy.exe" --out .local/native-check-新编号
```

查看 `--out` 目录中的 `summary.json`，其中包含本次 `run_id`、总体 `status`、宿主退出码及宿主报告。日志为 `blender.log`、`cinema4d.log`，每个方向还保存独立的核心转换报告。

脚本运行独立 Blender 和 c4dpy 进程，不打开用户场景，不修改软件配置或授权。先根据字典构造源夹具，在宿主创建、保存、重开，读取真实节点 ID、配置、输入、资源和连接并通过 `identify` 识别。再把读回材质经统一 API 转换到三个目标，在目标端创建、保存、重开核对。它不等于从任意现有 C4D 材质完整导出。

2026-11-04 本机验证结果：三个目标各接收三个来源，共 9 组（6 个跨渲染器方向及 3 个同配置流程）通过；重开后检查共 759 个输入、27 条含材质出口的连接。Blender 还验证了源材质未变和缺失资源时清理新建数据。结果必须同时满足进程退出码、此次 `run_id` 以及报告 `status=ok`，不能只根据 c4dpy 退出码判断成功。

2026-11-05 统一语义层验证：`run_id=e8313ea8-7435-4bc3-8b9f-3d441ae6ff93`。本机报告保存在忽略目录 `.local/semantic_verify_04/summary.json`；可用上述命令在新目录复现，报告不提交 Git。

| 阶段 | 用例 | 核对输入 | 核对连接（含材质出口） |
| --- | --- | --- | --- |
| 源材质保存重开与原生识别 | 3 个来源 × 2 条链路 = 6 | 505 | 21 |
| 用读回源图转换并在目标保存重开 | 6 组源材质 × 3 个目标 = 18 | 1515 | 63 |

两条链路为“Raw 图片 → 切线法线 → 标准表面法线”和“Raw 图片 → 显式 R 通道 → 高度凹凸 → 标准表面法线”。验证包括模式区分、实际资源引用和图连接；Blender 继续验证源材质不变及缺失资源失败清理。C4D 读取仅覆盖本次独立夹具中已声明的节点配置，不代表任意用户材质的无损导出。

Redshift 图根的子项包含端口列表，读取时只枚举 `NODE_KIND.NODE`；资产 ID 从 `maxon.nodes.AssetId` 返回的 ID／版本对读取首项。C4DtoA 的 shader 类型从已安装 `gvarnoldshader.h` 定义的 `C4DAI_GVSHADER_TYPE=200` 读取。这两者和配置一起交给核心 `identify`，不以预期夹具类型代替实机识别。

本轮未进行可比条件下的渲染对照，未训练神经网络，`appearance_verified` 保持 `false`。下一阶段需要真实材质样本、固定场景渲染与联合校正实验。

## 更新目录

[`probe_blender.py`](../_scripts/probe_blender.py) 在 Blender 中运行；[`probe_cinema4d.py`](../_scripts/probe_cinema4d.py) 在 c4dpy 中运行。设置环境变量 `IRIS_RELAY_OUTPUT` 指向不存在的 JSON 文件。探测器从已安装 API／资源头文件获取标识，并实际实例化节点；新版本应重新探测，不能复制旧版本号。

```powershell
uv run python _scripts/build_native_dictionaries.py --blender 本次Blender探测.json --cinema4d 本次C4D探测.json --out 新字典目录
```

审核生成内容后再更新仓库字典，重新执行离线测试与实机验证。当前直接使用仓库中的核心源码、字典、插件模块和脚本；宿主接入时显式指定字典目录与宿主脚本路径。

实现依据包括 [Blender ShaderNode API](https://docs.blender.org/api/current/bpy.types.ShaderNode.html)、[Maxon GraphNode API](https://developers.maxon.net/docs/py/2025_3_1/modules/maxon_generated/frameworks/graph/datatype/maxon.GraphNode.html)、[C4DtoA Linear Workflow 说明](https://help.autodesk.com/cloudhelp/ENU/AR-Cinema4D/files/ci-tutorials/ci-rendering-tutorials/arnold_for_cinema_4d_ci_Rendering_Tutorials_ci_Linear_Workflow_html.html)，以及目录记录的本机资源与运行时结果。节点 code 和版本以此次本机探测为准。
