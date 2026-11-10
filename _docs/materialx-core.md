# MaterialX 核心

Iris Relay 0.4.0 使用官方 MaterialX 1.39.5 SDK，提供材质文档校验、节点定义查询、自定义节点保存和受限原生转换。插件仍只调用公开 Python API，不需要 HTTP，也不需要每次经过磁盘文件。

## 最小调用

核心位于仓库根目录的 `iris_relay/`，无需安装项目自身。在仓库根目录运行 `uv sync --extra dev` 安装第三方依赖，然后：

```python
from pathlib import Path
from iris_relay import RelayAPI

api = RelayAPI.from_directory("dictionaries")
document = Path("_examples/materialx-example.mtlx").read_text(encoding="utf-8")
response = api.handle({
    "api_version": "1",
    "request_id": "检查材质",
    "operation": "materialx.normalize",
    "payload": {"document": document},
})
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
| `materialx.export` | `{"material": 既有原生JSON材质图}` | 标准 MaterialX `document`、源图 `source` 和 `report` |
| `materialx.convert` | `{"document": XML字符串, "target": 渲染器引用}`；可选 `material_name` | 目标 JSON `material`、原文 `source` 和 `report`；多材质文档必须选择顶层 surfacematerial 名称 |

文档校验失败时 `valid=false`，规范化输出为 null；目标转换失败时 `material=null`。输入结构错误或字典缺失时遵循既有 API，`result=null`。合法专有节点没有实现时，文档仍可保存，返回 `MATERIALX_IMPLEMENTATION_MISSING` 警告；缺少目标转换规则则阻断转换。

`capabilities.result.materialx` 给出 SDK 版本、标准 NodeDef 数量、是否可用及执行边界。标准库查询返回的默认值是 MaterialX 字符串，不是另一套自定义参数定义。没有给定 `category` 时返回全部定义。

## 文档与自定义节点

支持内联 NodeDef、标准类型端口、节点图、连接、资源路径和自定义属性。通过 SDK 读写，原始 XML 同时保留在响应 `source`；规范化可能改变空白和属性顺序，不是字节级复制。

自定义节点可用标准／自定义节点子图实现，或仅保存针对某目标的代码实现声明。核心不会执行外部实现，也不因此宣称目标渲染器能执行该节点。子图递归、连接环、类型冲突、未知定义和非有限数值明确报错。输入文档限 8 MiB、20,000 个 XML 元素、64 层；展开前预估规模并检查展开后规模。

自定义库须内联，不能重定义标准库同名元素或未加命名空间的标准类别。输入中的 DTD、实体声明、XInclude 被拒绝；不从文档指定路径加载代码或库。图片路径仅保存为数据，其存在性由宿主适配器检查。

## 当前目标转换边界

Blender 导出新增一项受限规则：字典明确声明颜色到标量连接使用场景线性亮度时，宿主提供实际系数，核心通过官方 `ND_luminance_color3` 与 `ND_extract_color3(index=0)` 显式表达。它不把 RGB 直接当 R，也不使用 MaterialX 的默认亮度系数替代 Blender 上下文。目前只核验 Linear Rec.709；其他上下文拒绝。

该亮度链现可转入 Blender 5.2.3：保留文档的常量 `lumacoeffs`，用 `ShaderNodeVectorMath / DOT_PRODUCT` 计算 `R*w0 + G*w1 + B*w2`。不读取目标场景的默认亮度系数，也不自动归一化；输入省略系数时使用官方 NodeDef 默认值。再次导出时表示为 `convert(color3→vector3) → dotproduct(vector3)`。这是对受限字典桥接的一项补充，标准节点定义仍来自 SDK。

当前只接受上述 `extract(index=0)` 组合和规范点积形式；动态系数、亮度颜色输出参与其他计算或未支持的向量来源会阻断。Redshift／Arnold 尚无此功能绑定，转到这些目标会保留源文档并报告缺失能力。公式依据为 MaterialX 1.39.5 的 [luminance 实现](https://github.com/AcademySoftwareFoundation/MaterialX/blob/v1.39.5/libraries/stdlib/genglsl/mx_luminance_color3.glsl)，原生端口依据为 [Blender Vector Math 实现](https://github.com/blender/blender/blob/v5.2.3/source/blender/nodes/shader/nodes/node_shader_vector_math.cc)及本机保存重开验证。

依据为 Blender 5.2.3 的 [隐式转换实现](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/kernel/svm/convert.h)、[工作空间 XYZ 矩阵](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/util/colorspace.cpp)及 [RGB → Y 初始化](https://github.com/blender/blender/blob/v5.2.3/intern/cycles/scene/shader.cpp)。适配器通过当前 OCIO 配置读取 ACES AP0 → 工作空间的矩阵，并按该版本 Cycles 的 XYZ D65 常量求出系数，不更改色彩设置。

GUI 可明确选择仅导出表面，排除的位移会产生警告，宿主顶层节点、属性、输入、图片引用和连接保留于完整报告的 `source_snapshot`。该快照用于诊断，不是递归节点组备份，也不会嵌入 `.mtlx`；原 `.blend` 始终保留。未连接活动表面的节点有信息提示；非恒等 Mapping、体积、活动图中的未知节点和节点组仍阻断。

文档可保存的范围大于当前目标可转换的范围。桥接支持三套字典共有的六类功能：

| MaterialX | 既有字典语义 | 限制 |
| --- | --- | --- |
| `standard_surface`，默认版本 1.0.1 | `surface.standard` | 已映射基础参数；其他参数仅允许官方默认值，非默认或连接阻断 |
| `image`，color3 | `texture.image` | 非空外部路径、默认 UV／采样；文件输入明确 `raw` 或 `srgb_texture`；不处理 Alpha |
| `normalmap`，float scale | `normal.tangent` | 颜色连接须显式 `convert(color3→vector3)`；几何法线／切线使用默认值 |
| `bump`，vector3 | `bump.height` | 标量高度；高度幅度和空间导数只作近似对应 |
| `extract`，color3→float | `channel.red` | 仅 index=0 |
| `luminance → extract(index=0)`；或 `dotproduct(vector3)` | `color.weighted_sum` | 三套字典；常量系数；点积的颜色连接须先经 `convert(color3→vector3)` |

自定义子图展开后全部相关节点落在以上范围内才可转换；否则保留原文并报告不支持。标准库中的其他节点可查询、校验和保存，但没有自动获得目标转换能力。未知原生节点导出失败时保留原始 JSON，不自动猜测并编造自定义 NodeDef。

### 官方 NodeDef 直接绑定

新增原生数学节点使用 `materialx.ND_add_float`、`materialx.ND_subtract_float`、`materialx.ND_multiply_float` 作为字典引用，端口语义直接使用官方 `in1`、`in2`、`out`。不再为每种标准运算建立另一套通用功能名称。
`materialx.export` 和 `materialx.convert` 按 MaterialX 1.39.5 SDK 核对定义是否存在、端口名和类型是否一致，错误返回 `MATERIALX_BINDING_INVALID` 并保留源信息。
宿主中的 `describe`／`identify` 只需读取原生声明，无需在宿主进程加载 SDK；官方契约校验在统一服务的 MaterialX 操作中执行。

目前直接映射覆盖 Blender 未钳制的 Math / ADD、SUBTRACT、MULTIPLY，以及 Redshift 对应标量数学资产。
这些节点可与既有表面、图片和亮度链连接，也可承接自定义子图展开后的同类标准节点。没有目标绑定的标准节点仍阻断；Arnold 尚无这三个标量绑定。
直接绑定首版只接受单个、已支持数据类型的输出；不会因引用 NodeDef 就自动实现全部官方库、任意多输出节点或专有节点。

独立宿主夹具由 `_scripts/verify_blender_math.py -- --out "新目录"` 导出，再分别交给 `verify_blender_batch.py --documents` 和 `verify_materialx_targets.py --documents` 回读。
三组夹具含负数、非对称输入、共享输出，Blender 和 Redshift 均完成保存重开核对；钳制模式及未支持运算继续阻断。未做渲染对照。

当前桥接拒绝尚未处理的单位、文件前缀、通道重排和非图片输入色彩空间上下文。只转换选定材质可达的图；未选用的顶层节点有信息报告，完整内容保留在源文档。资源、节点 metadata 等原生专用信息也保留在响应源快照中，不保证仅凭标准 `.mtlx` 重建所有原生信息。

旧 `convert` 继续使用旧语义链路；新路径显式使用 `materialx.export`／`materialx.convert`。Maya Arnold 的原生读取和字典尚未实现，不应把 C4DtoA 字典套用到 Maya。当前不会生成 USD、自动训练网络或证明外观一致。

## 验证与复现

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
