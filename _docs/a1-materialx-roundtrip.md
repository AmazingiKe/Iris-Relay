# A1 材质批量往返验证

- 日期：2026-11-10
- 状态：进行中；以下为已完成盘点、基线和分项修复，不代表全部材质已可往返。
- 最新完成阶段：节点组只读展开后完成 1,803 项全量复测；602 份成功文档与既有独立 Blender 回读文件逐字节相同，1,201 项仍阻断。
- 范围：文件中的全部材质，含未绑定对象的材质；验收节点、参数、连接及图片语义。
- 不包含：渲染外观验收、神经校正训练或推理。
- 架构依据：[ADR-0005](adr/0005-MaterialX材质图核心与自定义节点扩展.md)。

## 输入与验证条件

源文件为用户提供的 `A1.blend`，大小 1,416,105,317 字节。
初始 SHA-256 为 `31c9c14be388209cf046ff3200dbaf9aca890ae5e190dac27bed5696934376ce`。
基线代码为 `cd7046d`，宿主为 Blender 5.2.3 LTS，核心为 MaterialX 1.39.5。

源文件只读，禁用自动执行脚本。测试输出写入用户指定的外部测试目录，源场景及贴图不提交仓库。
批处理按材质分别记录结果，一个材质失败不会终止后续材质。
回读进程只使用导出的 `.mtlx` 和引用的资源，在空场景中新建材质。

## 全量盘点与初始基线

盘点得到 1,803 个材质、486 个递归引用的节点组、5,121 个图片数据块。
顶层材质共有 5,255 个图片节点、1,851 个 Principled 节点；节点组内部另有数学、混合、坐标、曲线和程序化节点。

初始导出成功 34 个，阻断 1,769 个。下表按每个材质最先出现的阻断分类；解决第一项之后可能继续暴露其他问题。

| 初始结果／首个阻断码 | 材质数 |
| --- | ---: |
| 导出成功（含已报告的近似警告） | 34 |
| `UNSUPPORTED_NODE_CONFIGURATION` | 1,157 |
| `DISPLACEMENT_UNSUPPORTED` | 301 |
| `UV_UNSUPPORTED` | 198 |
| `UNSUPPORTED_NODE` | 71 |
| `MATERIALX_COLORSPACE_UNSUPPORTED` | 23 |
| `LINK_TRANSFORM_UNSUPPORTED` | 9 |
| `INVALID_REQUEST` | 7 |
| `INVALID_LINK` | 1 |
| `OUTPUT_UNSUPPORTED` | 1 |
| `MUTED_NODE` | 1 |

证据文件为同一次运行目录中的 `inventory.json`、`source-before.json`、`baseline-export/summary.json` 和逐材质 JSON；未将大型源图快照放入仓库。

基线成功导出的 34 个文档已在独立空场景中全部创建材质，保存重开后核对 1,294 个目标输入、138 条含材质出口的连接，目标图核对全部通过。证据为 `baseline-import/summary.json`、逐材质读回 JSON 和 `restored.blend`。

## 已定位的问题与处理

### Principled 历史模式没有进入字典

样本包含 `MULTI_GGX + RANDOM_WALK_LEGACY`、`GGX + RANDOM_WALK_SKIN`、`MULTI_GGX + BURLEY`、`MULTI_GGX + RANDOM_WALK_SKIN`、`GGX + RANDOM_WALK_LEGACY` 五种此前未收录的组合。
这些名称同时存在于本版本实机节点目录中；所有已盘点的 Principled 节点均未启用 SSS。

在字典生成器中增加上述来源配置，Cycles 字典内容版本升为 `0.3.3`。
保留唯一目标默认实现；新增配置沿用活动 SSS 的阻断与未启用辅助参数的报告机制。
单元／API 回归先复现 10 个失败，再验证 12 个模式与 SSS 边界组合全部通过；完整已安装字典测试共 34 项通过。
此项只扩展来源识别，不宣称不同 BSDF 分布的外观相同，也不表示复杂材质的后续节点均已支持。
变更后的全部 240 项自动测试通过，核心和控制层合计覆盖率 92%；Ruff 静态及格式检查通过。

全量重新导出得到 447 份文档，另 1,356 个材质仍阻断，未跳过位移或其他参与输出的分支。
447 份文档在独立 Blender 中全部创建、保存重开及读回通过，核对 17,029 个输入、1,810 条含材质出口的连接。
证据为 `variants-export/summary.json`、`variants-import/summary.json` 及逐材质报告。

### C4D 缺少显式亮度加权节点绑定

447 份文档最初在 Arnold、Redshift 各有 232 份可转换，其余被 `color.weighted_sum` 缺少绑定或反射权重范围阻断。
实机重新探测 Redshift `rsmathdotvector` 和 Arnold `dot`；Arnold 没有独立参数头文件，改由动态描述取得 Input1、Input2 的整数 code、向量类型和默认值。
两端都使用未归一化点积，三个系数是原始向量数据，不套用颜色 UI 的 sRGB 编码。
点积含义依据 [Arnold 数学节点文档](https://help.autodesk.com/cloudhelp/ENU/AR-Core/files/ac-shading/arnold_user_guide_ac_shading_ac_math_shaders_html.html)，具体端口依据本版本运行时记录。

新增测试先复现 4 项失败，随后验证常规及负权重、贴图连接和再次导出后返回 Blender 的系数保留。
全部 244 项自动测试通过，核心和控制层合计覆盖率 92%；静态及格式检查通过。

| 对 447 份文档的验证 | Blender Cycles | C4DtoA | C4D Redshift |
| --- | ---: | ---: | ---: |
| 核心目标转换通过（含近似警告） | 447 | 374 | 374 |
| 独立宿主创建、保存重开及读回通过 | 447 | 374 | 374 |
| 仍被目标范围限制阻断 | 0 | 73 | 73 |

C4D 合计 748 个目标材质通过，核对 91,856 个输入、2,934 条连接；证据为 `weighted-targets/summary.json`、`host.json`、`host.c4d`，运行 ID 为 `e7af9cb6-bcdb-41ea-acb7-dfe0d3bebba5`。
剩余 73 份的 `reflection.weight` 超过目标字典支持范围；没有为提高通过数而截断或放宽。
这些结果验证的是统一 API 加宿主写入器，不代表 C4D 自带 MaterialX 导入器，也不代表渲染外观一致。

### 固定桥接表无法继续承载标准数学节点

原桥接只认识少量旧功能契约；继续为每种数学运算手工定义一套同义节点，会偏离 ADR-0005 复用官方 NodeDef 的方向。
增加 `materialx.ND_*` 直接绑定：原生字典只提供实际节点配置与端口对应，MaterialX 操作按官方 SDK 校验定义、端口和类型。
Blender 与 Redshift 首先接入标量加减乘，未钳制的 Blender 数学模式有实机枚举依据。钳制、除法及其他未声明模式仍阻断，Arnold 尚无这三个标量绑定。

11 项新增回归先复现失败，再验证标准节点往返、负数、输入顺序、共享输出、SDK 契约错误及缺失目标绑定。
完整 255 项自动测试通过，核心和控制层总覆盖率 92%；静态及格式检查通过。
三组独立 Blender 原生夹具先导出，再从文件还原、保存重开，核对 108 个输入、12 条连接；Redshift 另核对 255 个输入、12 条连接。
证据为 `math-fixtures/`、`math-import/`、`math-targets/`，C4D 运行 ID 为 `33d2e495-75da-40d1-a220-c834314ada23`。
A1 的 1,803 个材质已完成本轮复测，仍有 447 个成功；相关数学链还受其他节点或配置阻断，不能把新增能力数量当成新增材质覆盖数。
447 份导出文档与上一轮逐字节一致，保留先前独立 Blender 和 C4D 的读回证据，没有为相同文件重复生成整套宿主产物。
本轮证据为 `math-export/summary.json` 和 `math-document-comparison.json`。

### UV 名称和静态 Mapping 被当作不支持的坐标

原适配器只接受默认 UV 和恒等 POINT Mapping，A1 的上一轮首个阻断中有 198 个 `UV_UNSUPPORTED`。
新增命名 UV 和静态 POINT 序列：宿主读取每步变换，核心输出官方 `geompropvalue`／`texcoord`、类型转换和 `transformmatrix` 节点。
每步矩阵独立保存，避免将连续非均匀缩放与旋转合并成无法用单个 Mapping 还原的剪切。
Blender 目标按矩阵重建可编辑 UV 与 Mapping 链；不能分解的剪切或退化矩阵明确失败。C4D 两端尚无对应能力声明，因此不会静默退回默认 UV。

宿主夹具先复现命名 UV 阻断，随后验证中文 UV 名称、两步三维变换、非均匀／负缩放、共享坐标输入、三个数值采样点以及动态参数／实例 UV 等失败情况。
独立 Blender 仅从三份 `.mtlx` 和图片还原材质，保存重开后核对 108 个输入和 9 条含出口的功能连接；坐标链另按 UV 名称及各步矩阵核对。
第一次回读核对发现矩阵误差最大为 `2.384185791015625e-7`，其他资源字段完全一致；将资源中的数值比较纳入既有 `2e-5` 浮点容差，UV 名称、路径、色彩解释和字典键仍严格核对。
证据为 `uv-fixtures-01/summary.json`、`uv-import-02/summary.json` 及保存重开的 `restored.blend`。

22 项坐标回归和原有测试合计 277 项全部通过，核心及控制层总覆盖率 93%，坐标模块 96%；Ruff 检查通过，已有 GUI 和宿主读取回归通过。
本项保留坐标计算和可编辑链路，不保证原生坐标节点名称、布局或图片间的共享节点身份；命名 UV 对应的几何属性仍由目标几何体提供。
三份坐标夹具对 C4D Arnold 和 Redshift 的六个目标检查均返回明确的 `MATERIALX_RESOURCE_UNSUPPORTED`，未创建错误的默认 UV 材质；证据为 `uv-fixture-targets/summary.json`。

本轮已检查全部 1,803 个材质，成功导出 456 个，另 1,347 个仍阻断。
新增 9 个成功材质在独立 Blender 中全部创建、保存重开及读回，核对 378 个输入、57 条含出口的功能连接；24 个引用图片的 SHA-256 前后相同。
原有 447 份文档逐字节未变且没有新增失败，因此保留先前的读回证据。合计已有 456 个材质具备独立 Blender 回读证据，但不代表其全部渲染语义或外观已验证。
新增 9 份文档在 C4D Arnold、Redshift 的 18 个目标中均因坐标能力未实现而明确阻断，未运行宿主创建。现有 C4D 每端仍有 374 个材质具备实机证据。

另以原生源图快照直接计算 UV／POINT 采样点，与导出 XML 的矩阵链独立比较：456 份文档中的 839 个图片节点各核对三个不共线采样点、UV 名称和图片路径，全部通过，最大点误差为零。
该审计只用于比较源与文档，不参与导入还原；只能支持坐标链结论，不能替代整个着色图的语义验证。
原始 `A1.blend` 的 SHA-256 复核仍与初始值一致。
证据为 `uv-export/summary.json`、`uv-document-comparison.json`、`uv-source-audit.json`、`uv-added-import/summary.json`、`uv-added-targets/summary.json`、`uv-resources-after.json` 和 `source-after-uv.json`。

原先 198 个 UV 首个阻断的去向如下；修复坐标后暴露的后续问题仍算失败。

| 本轮结果／下一个阻断 | 材质数 |
| --- | ---: |
| 新增完整导出并通过 Blender 回读 | 9 |
| 仍为未支持的坐标来源或链路 | 72 |
| 后续节点未支持 | 83 |
| 后续节点配置未支持 | 23 |
| 后续连接需要额外变换 | 8 |
| 后续输入未支持 | 1 |
| 图片节点没有资源 | 2 |

### Windows 进度文件替换被短暂阻止

UV 全量运行在第 1,557 项保存 `summary.json` 时出现 `PermissionError / WinError 5`。逐材质报告已经落盘，但替换进度文件失败导致进程退出；这不属于材质转换失败。
进度写入增加最多八次短间隔重试，仅处理 `PermissionError`。持续拒绝访问时仍明确失败，并保留上一次完整进度文件，不先删除旧文件。
两项回归先复现失败，再验证一次占用后恢复以及持续失败时旧文件不变。
本次续跑先核对已存报告、导出文档和 1,557 个实际源图快照，随后只处理尚未执行的材质；完整复核记录保存在外部测试目录。
续跑进程退出码为零，最终完成全部 1,803 项，全部既有逐材质结果哈希未变；证据为 `uv-export-resume.json`。
修复后的完整 279 项自动测试通过，Ruff 静态及格式检查通过。

### 图片 Alpha 与透明度连接无法往返

UV 修复后的首个阻断中有 168 个 `LINK_TRANSFORM_UNSUPPORTED`，主要是图片驱动 Principled Alpha。此前桥接只创建 `image(color3)`，并且 `scalar_to_color` 只处理常量；直接放开连接会使图片 Alpha 丢失或发生错误类型连接。

实现同时补齐两部分：图片按实际解码通道、Alpha 模式和输出使用情况生成标准采样／提取／关联运算，透明度连接显式经过 `convert(float→color3)`。回导核验运算后合并为同一可编辑图片节点的 Color、Alpha 两个输出，并恢复图片模式。标准节点类型、默认值和实现仍来自 SDK，不增加私有 NodeDef。

研究与实测发现了以下易错点：

- RGB 图片在 Blender 中的 `Image.channels` 也可能为 4，不能据此认为文件包含 Alpha。使用临时 Image 的 `depth` 与浮点类型确定解码色彩模式，探测后立即释放，不触碰源 Image 缓存；无 Alpha 的文件显式输出 1。
- Blender 的 Alpha 模式、图片是否为数据、Alpha 输出是否使用，会影响颜色关联。导出显式 `premult`／`unpremult`，同时报告不同宿主加载、颜色转换与过滤次序的近似边界。
- 灰度图片不能简单使用 `image(float)` 并附加 sRGB 属性：官方着色生成器只对颜色类型应用颜色变换。改为颜色采样后提取 R、复制到 RGB；另用官方 GLSL 生成器确认实际调用 sRGB 到线性变换，未渲染。
- XML 附加属性仅用于原生模式与图片双输出合并，不能替代运算。回导核对实际辅助节点类型、常量和连线，篡改索引、模式、来源或运算会阻断。
- 旧验证夹具未声明 Alpha 信息；读回新增探测信息不应伪装成原来已验证的字段。验证器允许这种新增观测，但新文档声明的模式与通道必须完全匹配，原始完整读回信息保留在报告中。

完整 316 项自动测试通过；314 项覆盖率运行的核心总覆盖率为 93%，图片模块 95%，其后新增的着色生成与无关目标歧义回归也通过。全量资产中另发现颜色驱动透明度链，补充回归后 Alpha 专项共 38 项通过。Ruff 静态及 55 个文件的格式检查通过，原有 18 项 GUI 检查和宿主读取回归通过。
48 组夹具覆盖 1／3／4 通道、四种模式、sRGB／Raw 和 Alpha 使用／未使用，均完成独立 Blender 创建、保存重开，核对 1,632 个输入和 192 条含出口连接。源图、源 Image 缓存和图片文件保持不变；故意提供错误通道时清理本次新增材质和图片。
证据为 `alpha-fixtures-02/summary.json`、`alpha-import-01/summary.json`、`alpha-gray-generated.glsl`；首轮夹具发现灰度颜色解释问题后已由第二轮替代，不能混用第一轮作为最终证据。

48 份夹具在 C4D Arnold／Redshift 的 96 个目标检查均明确阻断，证据为 `alpha-fixture-targets/summary.json`，运行 ID 为 `1cc0c12c-1531-4d00-bedc-83b44dd5a212`。
两端尚缺图片模式读写及部分辅助运算绑定；新增资源解释也会使之前可转换的 RGB 材质文档受到更严格的能力检查。旧文档的既有宿主验证仍有效，但不能沿用为新版文档的兼容证明，C4D Alpha 接入仍需继续完成。

全量 1,803 项复测已完成：602 个成功导出，比上一轮增加 146 个，没有原有成功项退回失败；另外 1,201 个仍阻断。
原先 168 个非恒等连接阻断中，146 个完整导出，18 个继续遇到不支持输入、2 个遇到非法值、1 个缺少图片资源，剩余 1 个仍是尚未处理的缩放连接。
新版资源解释会改变旧成功文档，因此没有沿用旧 456 份文件的回读结果；602 份新版文档分三批全部在独立 Blender 中创建、保存重开，核对 23,266 个输入、2,707 条含出口连接。
三批分别为 `alpha-added-first-import/`（79 项）、`alpha-roundtrip-batch-02-import/`（358 项）和 `alpha-roundtrip-batch-03-import/`（165 项）；每批输入只有 `.mtlx` 与图片引用。最终审计核对三批 ID 的并集恰好等于全部成功文档，没有重复或遗漏。

602 份新版文档在 C4DtoA 和 Redshift 各有 133 份通过核心转换并完成宿主创建、保存重开；合计 266 个材质核对 19,831 个输入、280 条连接。每端另 469 份阻断，其中图片解释能力缺失 288、节点能力缺失 146、参数范围失败 35。
这意味着新版文档的 C4D 可用范围尚未恢复到之前旧 RGB 文档的每端 374 项，不能将 Alpha 的 Blender 成功数当作跨 DCC 支持数。
证据为 `alpha-all-targets/summary.json`、`host.json`、`host.c4d`，运行 ID 为 `c7636169-1dce-4e82-9474-aca744e3dc43`。本项仍通过项目 API 与宿主写入器验证；宿主自带 MaterialX 导入器另需验证。

源图到 XML 的独立审计覆盖全部 602 份文档、1,198 个图片节点，核对模式、通道、路径及色彩解释；另核对 113 条图片 Alpha 驱动透明度连接，以及 33 条颜色驱动透明度的亮度系数与连续类型转换。
三批材质共 1,002 个引用资源路径的 SHA-256 前后相同，源 `A1.blend` 和材质库缓存保持不变。审计只作比较，不参与导入，不构成渲染外观证明。
完整证据为 `alpha-export/summary.json`、`alpha-export-comparison.json`、各批 manifest／回读报告、`alpha-source-audit.json` 和 `alpha-final-audit.json`。

### 节点组实例读取与独立还原

原读取器把 `ShaderNodeGroup` 当作未支持的功能节点，无法继续识别组内计算。
新增只读实例视图，沿稳定接口标识解析组输入、活动输出和嵌套连接；共用一个组定义的实例分别保留覆盖值和内部节点身份。
节点转换仍由公开 API 和既有字典执行，适配器不复制字典规则，也不修改源数据块。
标准文档保存展开后的功能节点与连线，导入后可以编辑；原组层级、布局及共用组数据块尚不重建，报告明确记录展开及来源路径。

实际遇到两类不能直接消除的边界：一是颜色、标量等在组接口发生的隐式类型转换，二是未连接组向量输入形成的常量连接。
后者若直接变成图片的未连接 Vector，会误用默认 UV；接到表面时也可能误用隐式法线。
当前对未显式表达的类型转换和几何相关默认值明确阻断，避免以导出成功掩盖计算改变。缺失／空组、非唯一活动输出、递归及接口循环同样保留错误。

8 组真实夹具覆盖共享／嵌套数学组、实例常量和连接覆盖、旁路、共享输出、命名 UV 经组接口驱动图片 Color／Alpha，以及整个表面位于组内。
先按手工预期核对展开图，再在另一个 Blender 中仅从 `.mtlx` 和图片导入、保存重开；288 个输入与 32 条连接核对通过。
夹具还检查源材质、全部组定义、数据块数量及静音／常量向量失败边界。
证据为 `group-fixtures-02/`、`group-fixture-import-02/summary.json`；初次六组数学夹具位于 `group-fixtures-01/`，以第二批完整夹具为准。

8 份文档的现有目标检查中，Redshift 7 份、Arnold 1 份完成核心转换与 C4D 保存重开，共核对 664 个输入、29 条连接。
Arnold 的六份数学图因尚无对应标准数学绑定被阻断；图片夹具在两端因尚未实现的坐标／资源能力被阻断。
证据为 `group-fixture-targets/summary.json`、`host.json`、`host.c4d`，运行 ID 为 `467d3392-6e7e-44c4-ad26-b6b647d7530c`。
这仍是 Iris Relay 加宿主写入器的验证，不代表 DCC 自带 MaterialX 导入器或渲染外观验证。

全量 1,803 项复测完成，另对其中 303 个含顶层节点组的材质执行最终接口实现复测，成功数量仍为 602，没有原有成功项回退。
602 份 XML 与 Alpha 阶段已完成独立回读的文件逐字节相同，继续保留该阶段的 Blender／C4D 证据；本项尚未新增 A1 完整成功材质。
原先首先被 `ShaderNodeGroup` 阻断的 65 项，现在能定位到组内更具体的原因：

| 首个阻断原因 | 材质数 |
| --- | ---: |
| 未支持的坐标链 | 43 |
| 需要显式常量节点的向量接口 | 10 |
| 组内未支持功能节点 | 8 |
| 需要显式表达的接口类型转换 | 4 |

其中 30 项属于 `SimpleUVMapping / Vector Rotate`，实际输入均来自 UV；内部还包含向量加乘、Combine XYZ 和角度转弧度。
后续应按实际节点运算实现坐标链，不能按组名替换或仅删除报错节点。其他坐标来源仍需分别验证。

全部材质顶层快照与 Alpha 基线相同，材质缓存内 485 个组定义前后不变；源 `A1.blend` 和 1,002 个已验证贴图路径的 SHA-256 保持不变。
完整结果为 `group-export/summary.json`、`group-recheck/summary.json`、`group-final-audit.json`、`group-resources-audit.json`；最终审计逐材质注明报告目录。
334 项自动测试通过，核心、控制层与组读取模块合计覆盖率 93%，组模块 89%；58 个 Python 文件通过 Ruff，原 18 组 GUI 检查和读取回归通过。

### 缺失外链库和图片路径

源文件加载报告缺失 8 个外链库和 9 个链接数据块，涉及 botaniq 植物资产及 `TK_UtilityNode.blend`。
三个 `Decode_NR_Group`／`Decode_MA_Group` 数据块只有缺失占位，没有节点内容。已向用户询问完整外链库的位置，不能按组名猜测其内部计算。

普通文件图片中有 85 个数据块的原路径不存在。限定搜索源文件旁两处已有贴图目录后，50 个找到唯一同名候选，35 个没有候选。
候选清单写入 `missing-resource-candidates.json`；同名只构成定位线索，当前未据此修改源引用。
此外有 102 个 UDIM 图片数据块，其 `<UDIM>` 模板本身不是普通文件，不能简单归入缺图；需要逐 tile 验证。

### 大场景盘点的额外开销

首次全场景只读盘点访问 `image.size` 时触发大量贴图解码，该属性并非节点与资源结构验证所需。
盘点脚本已移除该访问。后续批处理只附加材质及依赖，并可另存材质库缓存，以减少重复加载和求值整场景几何体的成本。
缓存写入使用 Blender 的 `libraries.write`，依赖随材质写入、路径转为绝对路径；API 依据见 [Blender 文档](https://docs.blender.org/api/5.1/bpy.types.BlendDataLibraries.html)。
缓存与源文件的 1,803 个材质名称、顶层节点数量一致；除 4 个材质中缺失 TK 节点组的外链名称后缀变化外，节点记录、连接与底色一致。
这些缺失组仍为空占位，缓存不能恢复原本不存在的内容。原文件和附加进程的工作空间均为 Linear Rec.709；详细差异见 `cache-comparison.json`。

## 复现入口

在仓库根目录安装第三方依赖后，将下列占位路径替换为本机路径；输出目录必须是新的。

```powershell
& "Blender程序路径" --background --factory-startup --disable-autoexec "源A1.blend路径" --python-exit-code 1 --python _scripts/inspect_blender_materials.py -- --out "盘点JSON路径"
& "Blender程序路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_batch.py -- --source "源A1.blend路径" --out "新导出目录" --material-cache "新材质缓存.blend路径"
& "Blender程序路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_batch.py -- --documents "导出目录" --out "新回读目录"
uv run python _scripts/verify_materialx_targets.py --documents "导出目录" --out "新目标验证目录" --c4dpy "c4dpy程序路径"
```

回读报告中的 `target_graph_verified` 检查保存重开后的节点、全部目标输入、连接和图片解释是否符合核心返回的目标图。
源图到 MaterialX 的语义保留、跨 DCC 的实际创建仍须另行核对，不能仅凭目标图核对通过推断全部往返正确。

## 尚需完成

继续处理实际样本中的组内未知功能、组接口显式类型转换、数学／颜色链、尚未支持的坐标来源与动态 Mapping、位移、剩余图片格式与 Alpha 目标兼容、UDIM 和色彩空间。
需要分别汇总标准文档校验、Blender 实际还原、已有其他目标的核心转换及宿主创建结果，并复核资源和源文件保护。
本报告会随全量复测更新，当前不存在全部材质完成或跨 DCC 全部兼容的结论。
