# MaterialX 核心

Iris Relay 0.4.0 使用官方 MaterialX 1.39.5 SDK，提供材质文档校验、节点定义查询、自定义节点保存和受限原生转换。插件仍只调用公开 Python API，不需要 HTTP，也不需要每次经过磁盘文件。

实现集中在 `iris_relay/materialx/`：`core.py` 负责文档与 SDK，`bridge.py` 负责原生桥接，其余模块按贴图、坐标、子图和元数据划分。共用名称处理位于 `iris_relay/tools/naming.py`。宿主仍通过 `RelayService.handle` 调用；模块地图见[核心模块职责](core-layout.md)。

通过 [Maya 2022 Arnold 入口](maya-materialx-import.md) 及 C4D Redshift 适配器创建可编辑节点，再从当前原生图导出。最新 [2026-11-11 完善报告](editable-materialx-20261111.md)记录 997 份标准／内联图、50 个材质多向保存重开、RGBA／命名 UV、功能分组和共享控制，以及外部官方文档验证。结构、实际采样和外观结论分别报告。

## 最小调用

核心位于仓库根目录的 `iris_relay/`，无需安装项目自身。在仓库根目录运行 `uv sync --extra dev` 安装第三方依赖，然后：

```python
from pathlib import Path
from iris_relay import RelayAPI

api = RelayAPI.from_directory("dictionaries")
document = Path("_examples/materialx-example.mtlx").read_text(encoding="utf-8")
response = api.handle(
    {
        "api_version": "1",
        "request_id": "检查材质",
        "operation": "materialx.normalize",
        "payload": {"document": document},
    }
)
if response["status"] != "error":
    normalized = response["result"]["document"]
```

只处理 MaterialX 文档时使用 `RelayAPI()` 即可，不需要宿主字典。仅 `export` 和 `convert` 需要相应字典。调用方应保留响应中的 `source` 和诊断，只有目标材质非空且符合自身警告策略时才创建宿主材质。

完整示例：

Blender 中可直接运行 [GUI demo](../_examples/blender_demo_gui.py)，经宿主适配器读取所选材质并调用本页的导出／校验 API。操作步骤、警告接受策略和核心环境要求见 [README](../README.md#blender-gui-demo)。下文分别说明导出和目标转换范围。

```powershell
uv run python _examples/materialx_demo.py --out .local/materialx-demo-新编号
```

输出规范化 `.mtlx` 和转换响应 JSON，目录必须不存在。示例中的自定义表面节点通过标准 `standard_surface` 子图实现；演示生成 Blender 原生节点数据，不自动启动 Blender。

## 操作

所有操作使用同一 API v1 请求外层及 `ok / warning / error` 响应。

| operation | payload | 结果 |
| --- | --- | --- |
| `materialx.definitions` | `{}`；可选 `category` 和 `document` | 官方 NodeDef，以及指定文档内联的自定义 NodeDef；端口类型、默认值字符串及原始属性 |
| `materialx.validate` | `{"document": XML字符串}` | `source`、`valid`、`issues`；`document` 为 null，不执行着色 |
| `materialx.normalize` | `{"document": XML字符串, "flatten": false}` | 校验后输出规范化 XML；可选 `flatten=true` 展开自定义节点子图，保留标准着色节点 |
| `materialx.preserve` | `{"snapshot": 原生图快照}` | 带 `preservation_only` 标记的自定义声明文档；`conversion_supported=false`，不能计入转换成功 |
| `materialx.export` | `{"material": 既有原生JSON材质图}` | 标准 MaterialX `document`、源图 `source` 和 `report` |
| `materialx.convert` | `{"document": XML字符串, "target": 渲染器引用}`；可选 `material_name`、`calibrate`、`calibration_context` | 目标 JSON `material`、原文 `source`、`intermediate` 和 `report`；多材质文档必须选择顶层 surfacematerial 名称 |
| `materialx.resources` | `{"document": XML字符串}` | filename 端口、原 URI、前缀解析 URI 与颜色解释；不读取文件 |
| `materialx.rewrite_resources` | 文档与所有 filename 端口的原值／新值映射 | 独立新文档，原值和完整映射不符即拒绝 |
| `materialx.diff` | `before`、`after`；可选显式 `node_mapping` 与数值 `tolerance` | 计算字段差异；不证明像素或任意图同构 |

文档校验失败时 `valid=false`，规范化输出为 null；目标转换失败时 `material=null`。输入结构错误或字典缺失时遵循既有 API，`result=null`。合法专有节点没有实现时，文档仍可保存，返回 `MATERIALX_IMPLEMENTATION_MISSING` 警告；缺少目标转换规则则阻断转换。

`capabilities.result.materialx` 给出 SDK 版本、标准 NodeDef 数量、是否可用及执行边界。标准库查询返回的默认值是 MaterialX 字符串，不是另一套自定义参数定义。没有给定 `category` 时返回全部定义。

## 节点名称与原名备份

原生导出与目标转换共用核心命名工具：中文转无声调拼音，删除标点、空白、表情和开头连续数字，保留英文与名称中间、末尾的数字。无法转写的其他文字使用合法 ID；清理为空则使用节点类型名。名称限制为 63 个 ASCII 字符，重名或截断使用稳定后缀并检查冲突，连接端点同步更新。

每个导出节点（含生成的辅助节点和内联子图节点）都保存一个 `iris_source_name`，即使名称无需转换也保存。特殊 XML 字符使用 `iris_source_name_encoding="base64-utf8"` 编码。目标协议中对应 `metadata.source_name`；备份随宿主保存重开和再次导出传递，不覆盖为当前名字，也不用于默认显示或连接。需要溯源时读取该字段，不能从拼音反推原文。

例如 `123木纹🔥` 生成 `mu_wen`，备份仍为完整的 `123木纹🔥`；`wood_02` 保持不变，也保存原名。合法名称遇到场景现有同名节点时，宿主仍须分配唯一实际名称，不能覆盖用户节点。规则与宿主存储方式见 [ADR-0011](adr/0011-统一节点命名与原名溯源.md)。

## 文档与自定义节点

支持内联 NodeDef、标准类型端口、节点图、连接、资源路径和自定义属性。通过 SDK 读写，原始 XML 同时保留在响应 `source`；规范化可能改变空白和属性顺序，不是字节级复制。

自定义节点可用标准／自定义节点子图实现，或仅保存针对某目标的代码实现声明。核心不会执行外部实现，也不因此宣称目标渲染器能执行该节点。子图递归、连接环、类型冲突、未知定义和非有限数值明确报错。输入文档限 8 MiB、20,000 个 XML 元素、64 层；展开前预估规模并检查展开后规模。

自定义库须内联，不能重定义标准库同名元素或未加命名空间的标准类别。输入中的 DTD、实体声明、XInclude 被拒绝；不从文档指定路径加载代码或库。图片路径仅保存为数据，其存在性由宿主适配器检查。

## 当前目标转换边界

Blender 导出新增一项受限规则：字典明确声明颜色到标量连接使用场景线性亮度时，宿主提供实际系数，核心通过官方 `ND_luminance_color3` 与 `ND_extract_color3(index=0)` 显式表达。它不把 RGB 直接当 R，也不使用 MaterialX 的默认亮度系数替代 Blender 上下文。目前只核验 Linear Rec.709；其他上下文拒绝。

该亮度链可转入 Blender 5.2.3：保留文档的常量 `lumacoeffs`，用 `ShaderNodeVectorMath / DOT_PRODUCT` 计算 `R*w0 + G*w1 + B*w2`。不读取目标场景的默认亮度系数，也不自动归一化；输入省略系数时使用官方 NodeDef 默认值。再次导出时表示为 `convert(color3→vector3) → dotproduct(vector3)`。这是对受限字典桥接的一项补充，标准节点定义仍来自 SDK。

当前只接受上述 `extract(index=0)` 组合和规范点积形式；动态系数、亮度颜色输出参与其他计算或未支持的向量来源会阻断。Redshift／Arnold 现已补充加权求和绑定，具体版本与宿主证据见[原生字典文档](native-dictionaries.md)。公式依据为 MaterialX 1.39.5 的 [luminance 实现](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/stdlib/genglsl/mx_luminance_color3.glsl)，原生端口依据为 [Blender Vector Math 实现](https://github.com/blender/blender/blob/v5.2.3/source/blender/nodes/shader/nodes/node_shader_vector_math.cc)及本机保存重开验证。

依据为 Blender 5.2.3 的 [隐式转换实现](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/convert.h)、[工作空间 XYZ 矩阵](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/util/colorspace.cpp)及 [RGB → Y 初始化](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/scene/shader.cpp)。适配器通过当前 OCIO 配置读取 ACES AP0 → 工作空间的矩阵，并按该版本 Cycles 的 XYZ D65 常量求出系数，不更改色彩设置。

GUI 可明确选择仅导出表面，排除的位移会产生警告，宿主顶层节点、属性、输入、图片引用和连接保留于完整报告的 `source_snapshot`。该快照用于诊断，不是递归节点组备份，也不会嵌入 `.mtlx`；原 `.blend` 始终保留。未连接活动表面的节点有信息提示；动态坐标链、体积和活动图中的未知节点仍阻断。节点组、静态 POINT 与命名 UV 的范围见下文。

文档可保存的范围大于当前目标可转换的范围。桥接支持三套字典共有的六类功能：

| MaterialX | 既有字典语义 | 限制 |
| --- | --- | --- |
| `standard_surface`，默认版本 1.0.1 | `surface.standard` | 已映射基础参数；其他参数仅允许官方默认值，非默认或连接阻断 |
| `image`，color3／color4 及显式辅助运算 | `texture.image` | 非空外部路径、默认采样；文件输入明确 `raw` 或 `srgb_texture`；UV 和 Alpha 能力按目标检查，见下文 |
| `normalmap`，float scale | `normal.tangent` | 颜色连接须显式 `convert(color3→vector3)`；几何法线／切线使用默认值 |
| `bump`，vector3 | `bump.height` | 标量高度；高度幅度和空间导数只作近似对应 |
| `extract`，color3→float | `channel.red` | 仅 index=0 |
| `luminance → extract(index=0)`；或 `dotproduct(vector3)` | `color.weighted_sum` | 三套字典；常量系数；点积的颜色连接须先经 `convert(color3→vector3)` |

上表是基础共同语义；各目标另有官方数学绑定及有界组合展开。自定义子图展开后全部相关节点落在目标能力内才可转换，否则保留原文并报告不支持。可变 ColorRamp／Mix 配方、功能元数据及实际回导见[最新报告](editable-materialx-20261111.md#c1配方与源覆盖)与 [ADR-0008](adr/0008-可编辑图与贴图语义交换.md)。标准库节点可查询、校验和保存，并不自动获得目标转换能力。未知原生节点正常导出仍失败，可另用 `materialx.preserve` 保存不可转换的原生声明。

### 官方 NodeDef 直接绑定

新增原生数学节点使用 `materialx.ND_add_float`、`materialx.ND_subtract_float`、`materialx.ND_multiply_float` 作为字典引用，端口语义直接使用官方 `in1`、`in2`、`out`。不再为每种标准运算建立另一套通用功能名称。
`materialx.export` 和 `materialx.convert` 按 MaterialX 1.39.5 SDK 核对定义是否存在、端口名和类型是否一致，错误返回 `MATERIALX_BINDING_INVALID` 并保留源信息。
宿主中的 `describe`／`identify` 只需读取原生声明，无需在宿主进程加载 SDK；官方契约校验在统一服务的 MaterialX 操作中执行。

最初直接映射覆盖 Blender 未钳制的 Math / ADD、SUBTRACT、MULTIPLY，以及 Redshift 对应标量数学资产；当前字典另补充受限数学、条件、组合与颜色实现，准确清单通过 `describe` 查询。
这些节点可与既有表面、图片和亮度链连接，也可承接自定义子图展开后的同类标准节点。没有目标绑定的标准节点仍阻断；C4DtoA 尚无这三个标量绑定；Maya Arnold 的数学绑定见其独立文档。
直接绑定支持明确声明的单输出及有限的多输出选择，连接始终保留实际输出名称；可变 ColorRamp 使用受校验配方。不会因引用 NodeDef 就自动实现全部官方库、任意多输出节点或专有节点。

Blender 字典 `0.3.7` 增加 `ShaderNodeInvert → materialx.ND_invert_color3`，保留颜色输入及共享输出，允许后接显式亮度链。
仅支持未连接且等于 1 的 `Fac`；部分反相、连接系数，以及 MaterialX 非默认 `amount` 仍阻断。
Blender 的 RGB 运算为 `mix(color, 1-color, Fac)`，MaterialX 则为 `amount-in`，二者参数不能直接对应。依据为 [Blender 5.2.3 反相实现](https://github.com/blender/blender/blob/v5.2.3/source/blender/gpu/shaders/material/gpu_shader_material_invert.glsl)与 [MaterialX 1.39.5 标准实现](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/stdlib/genglsl/stdlib_genglsl_impl.mtlx)。
`_scripts/verify_blender_invert.py` 覆盖常量、图片及组内反相；三份文档完成独立 Blender 保存重开，核对 112 个输入、14 条连接。C4D 两套字典尚无该反相绑定，三份均明确阻断。

独立宿主夹具由 `_scripts/verify_blender_math.py -- --out "新目录"` 导出，再分别交给 `verify_blender_batch.py --documents` 和 `verify_materialx_targets.py --documents` 回读。
三组夹具含负数、非对称输入、共享输出，Blender 和 Redshift 均完成保存重开核对；钳制模式及未支持运算继续阻断。未做渲染对照。

当前桥接拒绝尚未处理的单位、通道重排和未知颜色上下文；已知全局 `lin_rec709` 与文件资源前缀可解析。只转换选定材质可达的图；未选用顶层节点有信息报告，完整内容保留在源文档。原生专用信息保留于来源记录，不保证仅凭标准 `.mtlx` 重建全部宿主状态。

旧 `convert` 继续使用旧语义链路；新路径显式使用 `materialx.export`／`materialx.convert`。Maya Arnold 已有独立版本字典及受限原生读取，不套用 C4DtoA 的整数绑定。当前不会生成 USD、自动训练网络或证明外观一致。

### 完整 HSV 与内联标准子图

Cycles 字典 `0.3.8` 增加 `ShaderNodeHueSaturation` 的五个输入（Hue、Saturation、Value、Fac、Color）及共享输出绑定。文档内联 `ND_iris_blender_hsv_color3` 和对应 NodeGraph，计算由官方数学、比较、组合与混合 NodeDef 构成；没有依赖 Blender 私有着色代码。目标绑定通过完整实现比对后，回导为一个可编辑的原生 HSV 节点，保留有效参数及连接；节点组仍按实例展开。

直接映射 `hsvadjust` 会漏掉 Fac 与饱和度钳制，直接使用标准 RGB／HSV 转换也存在近灰阈值和负值分支差异。因此按 [Cycles HSV 运算](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/hsv.h)和 [Cycles 颜色函数](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/util/color.h)构造标准子图，包含 Hue 中点／循环、饱和度 `[0,1]` 钳制、Value 乘法、不钳制的 Fac 混合及最终 RGB 下限 0。分支除法使用安全分母，避免后端提前求值造成零除。对照依据包括 [MaterialX 1.39.5 HSV 实现](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/stdlib/genglsl/lib/mx_hsv.glsl)；当前声明专用于 Cycles，不据此扩大到 EEVEE。

独立公式与实际 XML 标准节点解释器核对 524 组边界和随机输入；五组真实 Blender 夹具覆盖常量、近灰、图片、四个标量输入连接及组内 HSV，独立导入、保存重开核对 209 个输入、27 条连接。修改子图计算的文档被拒绝。测试覆盖有效参数和计算关系，不承诺保存已连接端口的闲置默认值、布局、组层级或渲染外观。

没有原生包装绑定的目标可以展开标准子图，但还需要支持展开后的每个功能。初次阶段 C4D 两端因绑定缺失阻断；A12 后续已补入 Redshift 组合展开与实机证据，C4DtoA 的范围不随之扩大；内联标准图可校验不等于任意 DCC 导入器已兼容。显式规范化展开后也不保证能恢复 Blender 的单个 HSV 节点。扩展库协议与实现比对见 [API 文档](api.md#字典)。

## 验证与复现

### Blender 节点组

读取层按组实例展开活动表面上游的节点，不复制、修改或调用解除编组操作。接口按 Blender 的稳定 `identifier` 对应；多个实例即使共用一个组定义，也分别保留输入覆盖值、连接及内部节点身份。支持嵌套组、接口旁路、共享输出、组内图片与整个表面位于组内的情形，最终功能仍须满足核心字典及 MaterialX 桥接的能力范围。

展开后的原生记录用 `metadata.source_group_path` 保存组实例和内部节点的来源路径；该信息用于报告追踪。标准 `.mtlx` 保存实际计算节点与连接，回导只依赖文档和引用资源，创建可编辑功能节点。当前不重建 Blender 原组层级、布局或共用组数据块，不把展开称为原始界面的逐项复制；报告包含 `NODE_GROUPS_EXPANDED`。

缺失或空的组定义、非唯一活动输出、静音节点、递归与接口循环明确阻断。同一接口的内外声明必须一致，沿连接发生的一次类型转换保留在展开后的真实端点上；核心再按字典决定能否转换，例如颜色转标量生成显式亮度节点。
途中发生多次转换时不能只连接两端，即使两端类型相同也返回 `GROUP_SOCKET_TYPE_UNSUPPORTED`；当前不消除 `RGBA→VALUE→RGBA` 或 `VALUE→RGBA→VALUE` 中的中间步骤。
浮点组默认值接颜色端口时按原生约定读作 `[v,v,v,1]`，保持各实例覆盖值；其他未实现的常量类型转换仍阻断。依据为 [Blender 5.2.3 默认值转换](https://github.com/blender/blender/blob/v5.2.3/source/blender/nodes/shader/node_shader_util.cc)及本版本实机夹具。
常量向量连接与图片的默认 UV、表面的隐式法线不同，因此普通功能节点上未支持的常量向量接口返回 `GROUP_DEFAULT_UNSUPPORTED`。组内未知节点仍按其实际类型报告。限制为 64 层和 20,000 个读取节点。

静态坐标求值器可显式读取组向量字面量，并作为常量参与后续运算；这不会放宽普通功能节点对隐式几何输入的限制。最终图片坐标仍须追溯到受支持的 UV 来源。

验证入口 `_scripts/verify_blender_groups.py -- --out "新夹具目录"` 创建共享／嵌套数学组、命名 UV 驱动的组内图片双输出及组内表面。先按手工预期核对实例输入与连线，再通过 `verify_blender_batch.py --documents` 在另一个 Blender 中导入、保存重开。测试同时检查原材质及全部组定义保持不变；具体全量结果见 [A1 记录](a1-materialx-roundtrip.md)。接口标识依据为 [Blender NodeTreeInterfaceSocket API](https://docs.blender.org/api/5.1/bpy.types.NodeTreeInterfaceSocket.html)，实际行为在本机 5.2.3 核验。

`_scripts/verify_blender_group_casts.py` 补充组输入、组输出、嵌套接口的颜色转标量，以及浮点组默认值接颜色的四组夹具。
先核对原生端点类型、实例值及 XML 中的实际亮度系数，再独立 Blender 导入、保存重开，核对 148 个输入、17 条连接；多次转换负例保持阻断。
C4D 两端各有三份因图片资源能力缺失、一份因反相绑定缺失阻断，未产生新的跨宿主还原证据。

### 图片 Alpha 与透明度连接

图片资源可携带 `metadata.image_alpha: {"mode":"STRAIGHT","channels":4}`。模式为 `STRAIGHT`、`PREMUL`、`CHANNEL_PACKED` 或 `NONE`，通道描述为 1、3、4；缺省仍按旧 RGB 桥接处理，但不能据此猜测 Alpha 输出。
Blender 读取源 Image 的 Alpha 模式，优先检查 PNG／EXR 文件头的真实通道，其他已接受格式再用临时 Image 探测解码色彩模式，不加载或改变源 Image 的像素缓存。`Image.channels` 与 `depth` 可能只反映缓存展开格式：A12 中单通道 EXR 曾被误判为 RGB，本轮按 EXR `chlist` 修正；PNG 额外检查 `tRNS`。灰度加 Alpha 的双通道 PNG、未保存的像素修改和未支持的图片类型明确阻断。

导出使用官方节点，无私有 NodeDef：RGBA 通过 `image(color4)` 与 `extract(index=3)` 分别提供颜色与 Alpha；无 Alpha 的文件或 `NONE` 模式用 `constant(float,1)` 表达 Alpha。灰度颜色通过 `image(color3) → extract(index=0) → convert(float→color3)` 明确复制到 RGB，保留颜色管理；不能以 `image(float)` 替代颜色采样。MaterialX 的自动颜色变换只接受 color3／color4，依据为 [1.39.5 着色图实现](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/source/MaterialXGenShader/ShaderGraph.cpp)。

非数据 RGBA 的 STRAIGHT 模式且 Alpha 未使用时补 `premult`；PREMUL 模式且 Alpha 使用时补 `unpremult`。其他组合不补关联运算。颜色取舍依据 [Cycles 图片节点](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/scene/shader_nodes.cpp)及[图片加载与预乘处理](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/util/image_metadata.cpp)。不同加载器的预乘、颜色转换和过滤顺序仍可能不同，报告保留 `ALPHA_FILTERING_APPROXIMATION`，不宣称透明边缘外观一致。

字典声明的 `scalar_to_color` 连接现在显式写为 `convert(float→color3)`；Blender 反向只在目标输入确实要求该规则且标准图提供对应转换时还原标量连接。非灰度颜色不能直接压成 Alpha，其他缩放／反相连接仍阻断。

XML 的 `iris_alpha_mode`、`iris_image_channels`、`iris_alpha_used` 及辅助节点来源／角色属性用于还原原生解释和合并同一图片的两个输出。回导会核验实际标准节点、常量及连线；修改运算或错误标记不会被忽略。计算本身由标准节点表达，附加属性不执行着色。当前原生合并只识别这一规范组合；任意外部 RGBA 图或丢失这些属性的图尚不保证可以转回原生图片，不能将此能力表述为所有 MaterialX 图片图的导入支持。

目标字典通过 `texture_alpha_modes`、`texture_image_channels` 和按格式的 `texture_alpha_behavior` 声明能力；Blender、Maya 与 Redshift 已接入 1／3／4 通道的有界路径，宿主必须再核验实际文件。Arnold PNG 与 Raw RGBA EXR 的实际关联不同，核心增加显式数学；非 Raw RGBA EXR 仍阻断，零 Alpha 隐藏 RGB 和透明过滤不宣称完全恢复。Redshift 关联尚未渲染验证，固定报告 `RS_RGBA_SAMPLING_UNVERIFIED`。C4DtoA 未接入这些新能力，旧 RGB 兼容结论不能自动扩大。

独立夹具入口为 `_scripts/verify_blender_alpha.py -- --out "新夹具目录"`，随后交给 `verify_blender_batch.py --documents` 在另一 Blender 中还原、保存重开。夹具覆盖灰度／RGB／RGBA、四种模式、两种色彩解释和 Alpha 使用／未使用的 48 种组合，以及同一图片共享输出、源数据保护和失败清理。详细运行证据见 [A1 记录](a1-materialx-roundtrip.md)。

### UV 与静态 Mapping

默认 UV 的旧文档保持不变。命名 UV 使用官方 `geompropvalue(vector2)`，显式默认 UV 使用 `texcoord(vector2,index=0)`。
静态 POINT Mapping 链表示为 `vector2 → vector3 → transformmatrix(vector3,matrix44) 序列 → vector2`，每步对应一个矩阵；只在进入图片采样前舍弃 Z。
这些都是 SDK 标准节点，输出不依赖 Iris 私有 NodeDef。矩阵按 MaterialX 行向量顺序保存，平移位于最后一行。

图片资源的 `coordinates` 可带 `transforms`：最多 64 个按应用顺序排列的 16 元素仿射矩阵。Blender 支持命名 UV 和逐步非退化 TRS；Maya 使用当前 `aiUvTransform` 链，已有命名 UV 加两步变换的实际采样对照。Redshift 支持二维采样器结构读写，次序／旋转中心未渲染核验，报告 `UV_SAMPLER_CONVENTION_UNVERIFIED`。未声明能力的其他目标仍阻断。
完整三维静态 POINT 链可以保存，剪切和退化矩阵不能强行分解为 Blender Mapping。
动态矩阵、实例 UV、Generated／Object 坐标、其他 Mapping 模式和非零 UV 索引目前没有对应目标规则。

Blender 回读创建 UV 和可编辑 Mapping 节点，不依赖原生源图快照。矩阵的分解不保证还原原来的欧拉角数值，但必须重建同一变换；节点名称、布局、恒等节点和多个图片之间的共享坐标节点身份不作为该桥接的保留项。
命名 UV 保留属性名称，目标几何体需提供对应 UV；本轮不创建或修改几何体。

读取层还支持 UV 与静态常量组成的仿射表达式：Vector Math 的加、减、逐分量乘和 SCALE，以及 Vector Rotate 的轴角、X／Y／Z 轴、XYZ 欧拉旋转，含旋转中心和反向选项。
常量分支支持 Value、Combine XYZ、Math 加减乘及角度／弧度转换；读取原生钳制开关和 float→vector 广播，不按节点组名称推断行为。
每个仿射操作保留为独立矩阵，源常量计算节点折算为矩阵参数；回导创建可编辑 Mapping，不重建原来的常量计算图或图片间共享坐标节点身份。
旋转顺序、非单位轴和零轴处理依据 [Blender 5.2.3 Vector Rotate 实现](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/vector_rotate.h)，广播依据[原生类型转换](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/convert.h)。

坐标所在节点树或组实例祖先节点树含 Action、驱动或 NLA 时返回 `UV_DYNAMIC_UNSUPPORTED`；当前按整棵涉及的树检查，即使动画驱动其他分支也会保守阻断。
同一计算节点同时驱动图片坐标和其他材质分支时返回 `UV_SHARED_EXPRESSION_UNSUPPORTED`，避免仅冻结坐标一侧而拆开共享依赖。
两个 UV 场之间的运算、未知节点和不支持的类型转换仍阻断；不能用常量图片坐标冒充默认 UV。

`_scripts/verify_blender_uv_expressions.py` 覆盖五种旋转模式的正反向、每组两个实例、非均匀／负缩放与中文 UV，先独立核对 60 个采样点，再导出给批量回读入口。
10 份文档完成独立 Blender 创建、保存重开，核对 360 个输入及 30 条功能连接，坐标另按矩阵核对；动画及共享依赖负例也通过。
该早期夹具运行中 C4D 两端因尚未声明命名 UV／矩阵能力而各阻断 10 份；后续 Maya 与 Redshift 的新增范围和证据见[最新报告](editable-materialx-20261111.md#c2rgba坐标与便携资源)，不把旧失败改写为新通过。

坐标夹具覆盖中文 UV 名称、连续变换、非均匀与负缩放、三个采样点、共享坐标输入、失败清理及源图／图片保护。
运行 `_scripts/verify_blender_coordinates.py -- --out "新夹具目录"` 后，将该目录交给下面的独立回读脚本；三份文档已通过创建、保存重开和 108 个输入、9 条功能连接核对。坐标节点连接由读回资源矩阵与来源名称另行核对，不计入这 9 条功能连接。

```powershell
& "Blender程序路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_coordinates.py -- --out "新夹具目录"
& "Blender程序路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_batch.py -- --documents "夹具目录" --out "新回读目录"
```

### 既有验证入口

```powershell
uv run pytest _tests/test_materialx_core.py _tests/test_materialx_bridge.py -q
uv run python _scripts/verify_materialx.py --blender "本机blender.exe路径" --out .local/materialx-check-新编号
uv run python _scripts/verify_materialx.py --blender "本机blender.exe路径" --input "已有材质.mtlx" --out .local/materialx-import-新编号
```

独立验证脚本自行生成小型纹理，以字典夹具导出两条标准 MaterialX 链路，再转入 Blender，创建、保存、重开并检查参数、连接、资源及失败清理。最后将真实读回结果再次导出 `.mtlx`。脚本只创建独立文件，不打开用户场景。通过条件同时检查宿主退出码、此次 run_id 和报告状态。

指定 `--input` 时直接验证已有文档，图片需使用存在的本机绝对路径。验证器接受并保存近似警告，创建新材质而不替换用户场景绑定；记录输入 `.mtlx` 和引用图片的前后 SHA-256。输出含 `host.blend`、`summary.json`、转换报告和读回再导出的文档。

Blender GUI 的“导入”页也通过 `materialx.validate`、`materialx.convert` 完成检查与新材质创建，支持导出后自动填入路径。GUI 默认阻止带警告的创建，明确接受后仍需重新检查；目标版本由当前 Blender 提供，未知节点或缺图继续阻断。GUI 控制层将相对图片路径按 `.mtlx` 目录解析，独立验证脚本仍要求绝对路径。新材质保留以供保存，不替换对象绑定；完整操作见 [README](../README.md#blender-gui-demo)。

2026-11-10 GUI 木地板往返：在 Blender 5.2.3 LTS 中从源材质重新导出表面文档，经 GUI 导入创建新材质、保存重开并再次导出；42 个输入和 6 条连接核对通过。原 `.blend` 与四张图片哈希、对象绑定和记录的场景条件未变。报告位于忽略目录 `.local/wood-gui-roundtrip-01/summary.json`；该结果不含位移或渲染外观验证。

2026-11-09 木地板回读验证：Blender 5.2.3 LTS，`run_id=3a7c9646-bad4-4b57-ae01-a8acdc59e615`。用户样本先前导出的表面文档生成 6 个功能节点，保存重开后核对 42 个输入、6 条含材质出口的连接；底色、粗糙度、法线图片及亮度系数保留。输入文档与引用图片哈希未变，重新导出的点积文档通过 SDK 校验。报告位于忽略目录 `.local/wood-roundtrip-01/summary.json`；未导入位移，也没有新增渲染证据。

2026-11-06 本机验证：Blender 5.2.3 LTS，`run_id=805f2ecd-60aa-48cd-8b3a-da61de23764a`，两条链路通过。忽略目录 `.local/materialx-verify-03/summary.json` 保存报告。没有新增 Maya 验证或渲染图像，所有结果 `appearance_verified=false`。

同日完整自动测试 160 项通过，总覆盖率 91%；MaterialX 文档核心与桥接分别达到 92% 和 88%。检查包含 1,100 层节点连接链、标准库覆盖拒绝、输入限制及本地 JSON 子进程。Ruff 检查通过，公开 API 往返曾在独立 Python 3.11 环境验证；开发环境为 Python 3.13。当前直接使用仓库源码，示例 `.mtlx` 位于 `_examples/`，宿主字典位于 `dictionaries/`；不再生成或验证发行包。

完整架构取舍见 [ADR-0005](adr/0005-MaterialX材质图核心与自定义节点扩展.md)。

## A12 后续：目标组合与原生结构保留

目标字典可通过 `materialx_lowerings` 引用内联计算子图，核心检查官方接口、静态选择条件、定义冲突、展开规模及收敛后进行替换。Redshift 的相等／大于、clamp、分量和广播等已完成组合接入；图片辅助节点继续整组核验，不能被通用替换破坏。目标扩展与源文档重名而不等价时拒绝。

`materialx.preserve` 是单独的结构保存操作：输出节点接口、无实现声明、显式连接及原生记录。`iris_preservation_only=true` 的文件在目标转换时以 `MATERIALX_PRESERVED_ONLY` 拒绝。快照使用显式 Base64 编码，源图非有限值仅以标签保存，标准数值校验没有放宽。完整实例、实测范围与未解决项见 [A12 报告](a12-materialx-interoperability.md)和 [ADR-0006](adr/0006-未知原生图保存与目标组合转换.md)。


## 功能接口与目标配方

ADR-0012 的首条链路已接入 HSV／颜色校正。新导出使用继承官方 `ND_colorcorrect_color3` 的通用接口；`mix`、`hue_center`、`saturation_max`、`output_min` 是有类型的细节输入，允许连接，文件不携带 HSV 实现子图。通用接口的声明默认值不等同于任意 DCC 的默认值，源实例会写入其实际参数。

目标字典使用以下字段，所有规则均为声明式数据：

| 字段 | 内容 | 执行阶段 |
| --- | --- | --- |
| `materialx_function_exports` | 源定义到通用定义的端口映射与显式细节常量 | 导出时 |
| `materialx_function_implementations` | 通用定义到目标定义的候选、端口映射、常量条件、实现种类与依据 | 导入时 |
| `materialx_function_recipes` | 可变配置的已知编译器及可选原生绑定；不接受任意代码 | 导入配置功能时 |
| `materialx_extensions` | 接口定义、原生绑定定义和目标配方图 | 核心加载字典时 |
| `materialx_lowerings` | 目标基础运算缺失时的有条件组合 | 选中目标配方后 |

实现种类为 `native`、`hybrid`、`recipe`，依次优先。没有原生绑定时继续查询数学后备；相同优先级匹配多个候选则阻断。条件遇到动态连接不会当作常量匹配；映射保持当前值、输出选择和连接。参数类型或已知接口被篡改时拒绝转换，历史内联文档仍核验实际实现。

未知细节暂不应用，报告 `MATERIALX_DETAIL_UNAPPLIED`；完整来源仍在 API 响应的 `source` 中。目标材质的 `metadata.deferred_details` 存档保留端口、来源文档及动态上游；Blender、Maya 和 C4D 已在颜色校正夹具中验证保存重开和再导出。再次导出时存档写入 `iris_deferred_details` 元数据并报告 `MATERIALX_DETAILS_ARCHIVED`。存档按文档摘要去重，不嵌套旧存档，不参与着色或覆盖用户当前编辑；尚未实现自动重新应用。

没有可用主功能实现时报告 `MATERIALX_FUNCTION_UNSUPPORTED`，不使用输入文件里伪装成声明的数学图作为后备。被忽略细节的独占支路也保存在存档中；若同一上游仍供主功能使用，则照常核验其目标支持。文件校验可能提示 `MATERIALX_IMPLEMENTATION_MISSING`：这是只有功能声明而没有外部可执行实现的实际限制，不能据此声称任意 MaterialX 渲染器可直接渲染。

目前 Blender HSV 使用原生映射；Maya Arnold 与 C4D Redshift 使用原生颜色校正加少量辅助节点，仍有近似警告。各目标可以引用相同的已验证配方生成器，再声明自己的原生绑定和基础运算替代，无需复制相同公式；不能仅因渲染器同名而跨宿主共用原生 ID。

混合、Gamma、亮度对比度、比较及分量原语已接入功能导出。Mix／MixRGB 的相同行为共用接口，目标原生布局可能不同。渐变和命名法线通过 `configuration` 字符串保存经过校验的 JSON 配置；例如渐变的控制点、颜色和插值，或法线的 UV 集和基底约定。动态因子、颜色及强度仍使用独立的有类型输入及连接。目标未声明配置编译器时阻断，不从文件中借用公式。真实作者创建的数学节点继续作为材质内容保存。

四种渐变插值及命名法线已在 Blender、Maya Arnold、C4D Redshift 通过保存重开、参数／连线核对和当前图再导出。未支持配置若仅供忽略细节使用，则随该细节归档；若主要输入也依赖它，转换仍阻断。源文件不因配方选择或配置规范化被改写。

纯官方颜色校正的默认实例当前可转 Maya Arnold／C4D Redshift；Blender 仅有本次明确登记的 HSV 扩展规则，不能推定支持官方颜色校正的所有参数。当前版本 837 项完整回归通过，核心覆盖率为 90.43%，完整验证范围记录于 [ADR-0012](adr/0012-功能交换与目标实现字典分离.md)。新接口没有通用可执行实现，不能把可保存的文件称为任意 MaterialX 渲染器均可直接渲染；本轮未验证渲染外观等价。
