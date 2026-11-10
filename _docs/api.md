# 统一插件 API v1

所有插件调用同一抽象服务：`RelayService.handle(request) -> response`。业务协议与传输方式分离；Python 插件可直接使用 `RelayAPI`，其他宿主可发送 UTF-8 JSON 给命令行核心。插件只读取和创建宿主材质，不实现字典转换或联合校正。

真实宿主配置与参考配置共用此协议。真实绑定范围见[原生字典文档](native-dictionaries.md)；结构验证不代表视觉等价。0.4.0 新增的 `materialx.*` 操作见 [MaterialX 核心](materialx-core.md)，沿用同一请求与响应外层。

## 快速调用

在仓库根目录执行 `uv sync --extra dev` 后，可直接导入源码（不安装项目自身）：

```python
import json
from pathlib import Path
from iris_relay import RelayAPI

api = RelayAPI.from_directory("_examples/dictionaries")
request = json.loads(Path("_examples/convert-request.json").read_text(encoding="utf-8"))
response = api.handle(request)
print(response["status"])
```

等价的命令行调用：

```powershell
uv run python -m iris_relay --dictionaries _examples/dictionaries --request _examples/convert-request.json
```

`--request -` 从标准输入读取 JSON，`--output -` 向标准输出写入 JSON；两者均为默认值。`--output 新文件.json` 写入一个新文件，不覆盖已有文件，也不自动创建父目录。退出码 `0` 表示有可用响应（可能包含警告），`2` 表示请求、转换或文件读写失败。插件必须查看 JSON 的 `status` 和报告，不能只看退出码。

批量宿主调用可使用 `--stream`：标准输入、输出各为一行一个 UTF-8 JSON 对象，沿用同一 API，不与 `--request`／`--output` 文件参数组合。单帧仍限 16 MiB，业务错误返回对应响应并继续下一请求；超限帧终止会话。`JsonSessionService` 串行复用一个核心子进程；写入阻塞超时、响应超时、超限或无法解析的响应都会关闭该会话，不重启或重放请求。公共 `iris_relay.native.request` 另检查响应版本、请求 ID 及状态。服务支持上下文管理器和显式 `close()`，界面关闭及批处理退出须释放自己创建的服务。

## 请求与响应

请求必须包含以下字段，不接受额外字段：

| 字段 | 含义 |
| --- | --- |
| `api_version` | 字符串 `"1"` |
| `request_id` | 非空字符串，由插件生成，用于对应响应 |
| `operation` | `capabilities`、`semantics`、`identify`、`describe`、`validate`、`convert` 或文档中定义的 `materialx.*` 操作 |
| `payload` | 对应操作的数据对象 |

| 操作 | payload | 结果 |
| --- | --- | --- |
| `capabilities` | `{}` | 已加载字典、渲染器精确版本、支持操作、校正器是否配置 |
| `semantics` | `{}` | 通用功能契约版本、节点含义、端口类型、必要端口、边界及标准参考；无需宿主或字典 |
| `identify` | `{"renderer": 渲染器引用, "native_id": 原生ID, "properties": 原生配置}` | `implementation` 实现键、`semantic` 功能、`native` 绑定副本及 `dictionary_fingerprint` |
| `describe` | `{"renderer": 渲染器引用}` | 完整声明式 `dictionary` 副本及内容 `fingerprint`，供插件查询原生绑定 |
| `validate` | `{"material": 材质图}` | 图结构和源字典语义检查结果；不检查资源文件是否存在或视觉效果 |
| `convert` | `{"material": 材质图, "target": {"id": "...", "version": "..."}, "calibrate": false}` | 源图、语义中间结果、目标图和转换报告；`calibrate` 可省略 |

响应总是包含 `api_version`、`request_id`、`status`、`result`、`error`。`status` 为 `ok`、`warning` 或 `error`；`error` 为空或含稳定 `code` 和中文 `message`。无法识别请求 ID 的错误使用 `null`；文件加载错误也可能发生在服务处理之前。

请求结构错误或缺少字典时 `result` 为 `null`。节点语义等转换阻断发生时，保留转换结果中的 `source`、中间诊断和报告，`material` 为 `null`。阻断情况下 `intermediate` 可能包含未映射字段，只用于诊断，不能作为可应用的标准语义图。

转换结果：

| 字段 | 含义 |
| --- | --- |
| `source` | 经过模式校验和可选字段补全的源材质副本，保留原节点及未知语义信息 |
| `intermediate` | 使用语义节点和端口名称的图；失败时可能是部分转换的诊断快照 |
| `material` | 可用目标材质图，阻断时为 `null` |
| `report.status` | `converted` 或 `blocked`，不代表视觉一致 |
| `report.dictionary_versions` | 源、目标字典 ID、内容版本、渲染器版本和指纹 |
| `report.dictionary_fingerprints` | 源、目标规则内容的 SHA-256，便于追溯 |
| `report.issues` | `severity`、`code`、`message`、`node`、`port` 组成的问题列表 |
| `report.calibration` | 校正状态、模型标识及实际应用的整组输入变化 |
| `report.appearance_verified` | 当前固定为 `false`，没有执行视觉验证 |

常见错误码包括 `INVALID_REQUEST`、`UNSUPPORTED_API_VERSION`、`UNSUPPORTED_SCHEMA`、`UNSUPPORTED_OPERATION`、`UNSUPPORTED_RENDERER`、`MATERIAL_REJECTED`。具体问题报告可包含 `UNSUPPORTED_NODE`、`UNSUPPORTED_INPUT`、`UNSUPPORTED_OUTPUT`、`INVALID_VALUE`、`LINK_TYPE_MISMATCH`、`LINK_TRANSFORM_UNSUPPORTED`。`TARGET_DEFAULT` 是需要插件展示的近似处理警告。

`identify` 不按显示名识别，也不猜测缺失的模式。它匹配精确版本下的 `native.id` 和声明的全部 `native.properties`；属性类型和值均须一致。未知 ID 返回 `UNSUPPORTED_NODE`，缺失或未支持的配置返回 `UNSUPPORTED_NODE_CONFIGURATION`，多重匹配返回 `AMBIGUOUS_NATIVE_NODE`。属性只是适配器对宿主的读取结果，不能用目标默认模式冒充来源观测值。识别操作不读取材质，也不证明未提交参数或连接受支持；随后仍须调用 `validate`／`convert`。

`identify.payload` 可额外提交 `implementation_hint`，值为先前创建材质时保存的实现键。该键只能在当前原生 ID 与全部配置仍匹配的候选中消除歧义；失效提示返回 `NATIVE_HINT_MISMATCH`。适配器仍须读取当前节点、所有受支持属性和连接，不能借提示或源 XML 快照恢复已被用户改动的材质。

例如查询 Redshift 高度凹凸实现：

```python
api = RelayAPI.from_directory("dictionaries")
response = api.handle(
    {
        "api_version": "1",
        "request_id": "识别高度凹凸",
        "operation": "identify",
        "payload": {
            "renderer": {
                "id": "redshift",
                "version": "2025.3.0",
                "host": "cinema4d",
                "host_version": "2025.3.2",
                "node_system": "redshift.maxon",
            },
            "native_id": "com.redshift3d.redshift4c4d.nodes.core.bumpmap",
            "properties": {"com.redshift3d.redshift4c4d.nodes.core.bumpmap.inputtype": 0},
        },
    }
)
```

此配置识别为 `bump.height`；模式 `1` 对应 `normal.tangent`。返回的 `implementation` 才是原生材质图的节点 `type`。一个宿主节点 ID 可以对应不同实现键，创建时从键查询绑定，不能把实现键直接当宿主节点 ID。

## 材质图

完整示例见 [convert-request.json](../_examples/convert-request.json)。材质本身只允许以下顶层字段，不接受灯光、相机或曝光修改：

| 字段 | 要求 |
| --- | --- |
| `schema_version` | 字符串 `"1"` |
| `name` | 非空材质名 |
| `renderer` | `id`、`version`，可选一组 `host`、`host_version`、`node_system`；必须精确匹配字典 |
| `nodes` | 节点列表，每项包含 `id`、`type`、`inputs`、`outputs`，可含 `metadata` |
| `links` | 每项为 `{"from": {"node": "...", "socket": "..."}, "to": {"node": "...", "socket": "..."}}` |
| `output` | 材质输出的 `node` 和 `socket` |
| `textures` | 可省略，默认 `{}`；按资源 ID 索引的贴图描述 |
| `metadata` | 可省略，默认 `{}`；一般透传。`color_to_float` 是下述 MaterialX 导出的显式色彩上下文；不触发场景操作 |

`inputs` 是端口名到值的对象，`outputs` 是输出端口名列表。节点 ID 必须唯一，连接必须指向存在的端口，每个输入最多一条连接，图必须无环。连接输入仍应在 `inputs` 中提供默认值；核心保留默认值和连接的区别。

字典允许的输入类型为 `float`、三个分量的 `color` / `vector`、四分量 `color4`、`string`、`bool`、`texture`、`shader`。`texture` 值为 `null` 或 `{"texture": "资源ID"}`，`shader` 默认值为 `null`，实际层次通过连接表达。资源引用必须存在于 `textures`。

真实配置的宿主三个字段必须一起提供，`target` 和 `describe.payload.renderer` 使用相同引用结构。`nullable: true` 的输入可使用 `null` 表示隐式值。

每项贴图描述包含 `uri`、`color_space`、`coordinates` 对象，可含 `metadata`。旧 `convert` 保留这些描述，不读取贴图像素、不自动转换色彩空间、不下载资源，也不更改资源路径。真实适配器须根据共同约定解析这些信息。示例使用的 `memory://reference/albedo` 只代表参考资源，并无附带图像文件。

`materialx.export`／`materialx.convert` 另解释可选的 `metadata.image_alpha: {"mode":"STRAIGHT","channels":4}`：模式限定为 `STRAIGHT`、`PREMUL`、`CHANNEL_PACKED`、`NONE`，通道限定为整数 1、3、4。源适配器提供实际观测，核心不读像素猜测；目标字典须用可选 `texture_alpha_modes` 列表声明支持的模式。缺省字段仍采用旧 RGB 行为，使用 Alpha 输出时必须补充明确描述。标准图和原生还原边界见 [图片 Alpha](materialx-core.md#图片-alpha-与透明度连接)。

目标字典可另用 `texture_image_channels` 声明不重复的 1／3／4 通道列表；未声明时兼容既有通道行为。核心按图片声明提前拒绝超出能力的图，宿主继续核对实际资源，不能仅检查 Alpha 模式。当前 Maya／Redshift 配置声明 `[1, 3]`，RGBA 继续阻断。PNG／EXR 文件头核对只提供通道证据，不证明像素、采样或外观相同。

`materialx.export`／`materialx.convert` 另解析受限 UV 描述：`{"kind":"uv","set":"UV名称或空字符串","transforms":[矩阵1,矩阵2]}`。`transforms` 可省略；每个矩阵为按 MaterialX 行向量约定展平的 16 个有限数值，按列表顺序应用。只支持仿射矩阵，数量最多 64。命名 UV 和变换的目标能力由字典的可选 `texture_coordinates` 声明，缺省只接受默认 UV；目前 `transforms` 能力取值为 `none` 或 `trs`。实际标准节点及宿主限制见 [MaterialX 坐标说明](materialx-core.md#uv-与静态-mapping)。

源图中未列出的已知输入由源字典默认值补全。源图含未知输入或输出时阻断转换。目标端增加了源端没有的语义时，使用目标字典默认值并发出 `TARGET_DEFAULT` 警告；目标端丢失源语义则阻断。

API 接受有限数值；JSON 文件拒绝重复键、NaN 和 Infinity，单次 JSON 读取限 16 MiB，嵌套限 64 层，材质图限 10000 个节点及 100000 条连接。限制是输入边界，不代表已验证的批量处理性能。

## 字典

完整格式见 [参考光泽度字典](../_examples/dictionaries/reference.gloss.json) 和 [参考粗糙度字典](../_examples/dictionaries/reference.rough.json)。字典目录由核心启动方指定，API 请求不能指定加载路径或可执行代码。

- 顶层包含 `schema_version`、`id`、`version`、`renderer`、`nodes`；可选 `host`、`description`、`evidence`、`materialx_export`。`host` 含 `id`、`versions` 和 `node_system`。
- `renderer` 包含 `id` 及明确列出的 `versions`；同一渲染器版本、宿主版本和节点系统组合不能由多份字典覆盖。
- 节点键表示一种确定的原生实现配置，映射到唯一 `semantic`。多个键可以共用原生 ID，通过 `native.properties` 区分模式；多个原生实现也可以归入同一通用功能。
- 同一功能仅一个目标实现时直接选择；多个实现须声明唯一布尔值 `target_default: true`，否则转换返回 `AMBIGUOUS_TARGET_NODE` 并保留源图。多个默认值在加载字典时拒绝。默认选择不免除参数、连接和支持边界检查。
- 新真实字典声明 `semantic_schema_version: "1"`，加载时按核心集中契约校验功能名、端口名、类型、可空性和必要端口。`native.*` 专用输入只允许显式声明 `omit_if_default`；未声明语义版本的旧协议示例保留兼容，不作为标准定义。
- 输入规则包含 `semantic`、`type`、`default`，可指定 `range`、`transform`、`adjustable`、`nullable`、`omit_if_default`、`inactive_when`；输出规则包含 `semantic`、`type`。节点和端口可包含 `native`；节点可通过 `approximation` 声明近似。
- `transform` 白名单包含 `identity`、用于浮点值的 `one_minus`、`{"op":"scale","factor":非零有限数值}` 及 `scalar_to_color`。后者将标量展开为三个相等的颜色分量，逆变换只接受灰度颜色。默认值和范围描述原生参数域，解码后进入语义域，编码时逆变换回目标原生域。
- 旧 `convert` 的非恒等变换遇到连接时返回阻断报告。MaterialX 桥接另支持将 `scalar_to_color` 连接显式展开为标准转换节点，并在目标适配规则匹配时还原；缩放与反相等连接仍阻断。

`native` 含实际 `id`（字符串或整数 code），可选 `label`、`properties`、`kind`、`codec`、`colorspace_id`、`uv_id`。`kind` 为 `socket`／`property`／`resource`；`codec` 白名单为 `identity`／`rgb`／`srgb`／`bool`／`normal`／`texture`／`gray`。`gray` 仅将标量读写为相等的三个分量，读取非灰度三通道时拒绝，不做亮度或通道猜测。这些是声明式宿主读写信息，不可携带执行代码。数值编码由宿主适配层的公开辅助函数处理，语义变换只由核心处理。

`normal` 编码可用 `native.properties.implicit_value` 声明未连接的隐式几何默认向量；只有该精确值解码为空值。资源端口可用 `native.properties.color_spaces` 声明逻辑名称到原生 ID 的字符串映射，交由公开 `resource_color_space` 编码／解码。反向映射须唯一，未知名称返回 `COLORSPACE_UNSUPPORTED`，不回退 Auto。Maya 原生枚举映射见后文。

`omit_if_default` 仅允许省略未连接、等于默认值的源原生控制项，并在源快照保留其值；目标使用自己的默认值。已知输出未连接且不是最终出口时允许省略。信息码为 `NATIVE_DEFAULT_OMITTED`、`UNUSED_OUTPUT_OMITTED`；不同 BSDF 的近似使用 `SHADING_MODEL_APPROXIMATION` 警告。信息记录不影响状态，错误阻断，警告需要插件明确接受。

原生辅助参数可额外声明 `inactive_when: {"input": "Subsurface Weight", "equals": 0}`：条件输入及当前输入都未连接、条件值精确相等时，报告 `INACTIVE_INPUT_OMITTED`，原值保留在源快照。条件输入须为同一节点的浮点输入，不能引用自身；当前规则仍须声明 `omit_if_default`。Blender 字典将它用于 SSS 关闭时的半径、尺度、IOR 和各向异性。开启 SSS 或连接辅助参数仍受原有支持边界限制。

`materialx_export: {"color_to_float": "scene_linear_luminance"}` 声明原生颜色到标量的隐式连接语义，仅用于 `materialx.export`。此类连接要求源 `metadata.color_to_float` 含 `working_space: "lin_rec709_scene"` 及三项非负有限 `coefficients`，总和须接近 1。适配器必须读取真实宿主上下文；不能填目标默认系数冒充源观测值。核心生成官方 `luminance` 和 `extract(index=0)` 节点并报告 `IMPLICIT_CAST_EXPLICIT`；未声明规则的字典、其他类型转换及旧 `convert` 仍按原有类型规则阻断。详细范围见 [MaterialX 核心](materialx-core.md)。

字典对语义、单位、默认值和范围的陈述必须有具体渲染器依据。核心只能验证声明的一致性，不能自动证明物理含义或视觉等价性。

官方标准节点可直接引用 `semantic: "materialx.ND_add_float"` 等 NodeDef 名称，输入／输出语义使用官方端口名。
此命名空间不加入手工 `semantics` 节点清单：使用 `materialx.definitions` 查询官方契约。
字典加载检查原生数据和引用格式，MaterialX 操作再按固定 SDK 校验定义、端口和类型；不要求只负责宿主读写的进程安装 SDK。
声明不匹配时报告 `MATERIALX_BINDING_INVALID`。目前实际绑定及单输出限制见 [MaterialX 核心](materialx-core.md#官方-nodedef-直接绑定)。

字典可选 `materialx_extensions` 为内联 MaterialX XML 字符串，只接受本地 `NodeDef` 和 `NodeGraph`，每个定义必须有唯一实现；不接受外部实现、标准定义覆盖、递归和超限展开。绑定仍使用 `materialx.<NodeDef名称>`，端口由 SDK 校验。材质实际使用扩展时才把库写入输出文档，未使用扩展的文档保持原有输出。

导入时，核心对文档内的定义、完整子图及其自定义依赖与目标字典逐项比对，只有实现一致才能恢复为原生功能节点；修改计算、默认值、删除实现或增加替代实现均明确阻断。没有原生绑定的目标按标准子图展开后逐节点检查支持范围。`materialx.normalize` 的显式展开不保留原生包装节点；展开后的图不保证能再次折叠为原生节点。这不改变 API v1，也不读取源快照恢复材质。

目标字典还可声明 `materialx_lowerings`，以官方 NodeDef 名称为键，候选为 `{"when": {常量选择端口: 值}, "replacement": "目标内联 NodeDef"}`。替换定义须来自已校验的 `materialx_extensions`，输出类型一致，输入接口只允许省去已经精确匹配的常量选择端口；动态连接不能冒充常量。核心在工作副本中展开，保留源文档，限制循环与规模，重名但不同的实现阻断。规则归核心执行，宿主不复制公式。已验证实例见 [ADR-0006](adr/0006-未知原生图保存与目标组合转换.md)。

## 未知原生图保存

`materialx.preserve` 的 payload 为 `{"snapshot": 原生快照}`。快照包含 `name`、`host`、`nodes`、`links`，可选 `metadata`。每个节点包含 `id`、原生 `type`、`inputs`、`outputs`，可选 `properties`／`metadata`；输入输出为含 `id`、MaterialX `type` 以及可选 `value`／`metadata` 的列表。连线仍使用 `from`／`to` 的节点与端口标识。完整小例见 [保存测试夹具](../_tests/test_materialx_preserve.py)。

成功响应保留 `snapshot`、`document` 和报告，固定 `conversion_supported=false`、`appearance_verified=false`。文档中的未知 NodeDef 与隐式类型连接没有计算实现，原生 JSON 通过明确的 Base64 UTF-8 编码属性保存，文档标记 `iris_preservation_only="true"`。`materialx.convert` 对它返回 `MATERIALX_PRESERVED_ONLY`，不得据此创建替代着色器。保存成功、目标可转换与外观验证是三个独立结论。

JSON 及有效材质数值仍拒绝 NaN／Infinity。Blender 保存适配层仅在原生记录中将其标记为 `{"iris_nonfinite_float":"nan"}`／`"+inf"`／`"-inf"`，不会将标签作为有效参数。原始 DCC 文件仍是完整资产恢复来源；保留文档不包含全部宿主内部状态或贴图像素。

## 插件接入

插件实现 `MaterialAdapter` 的两个方法：

```python
class MyAdapter:
    def read_material(self) -> dict:
        # 从宿主读取选中材质，完整导出 API 材质图。
        ...

    def create_material(self, material: dict):
        # 在宿主撤销事务中创建新材质，失败时回滚，返回句柄。
        ...
```

调用 `convert_material(adapter, service, target)` 获取 `PluginResult`。`service` 可以是 `RelayAPI`、`JsonCommandService` 或 `JsonSessionService`，其他实现只需满足 `RelayService` 协议；两种子进程服务均使用参数列表启动，不经 shell 执行。

默认只有 `status == "ok"` 才调用 `create_material`。警告响应保留供界面展示；插件明确选择接受已展示的近似方案后，可设置 `accept_warnings=True`。错误响应从不应用。新建后的材质绑定替换属于宿主的显式、可撤销操作，辅助接口不会自动执行。

`create_material` 必须只创建材质及必要的派生资源，不从 metadata 中读取或执行任何场景修改命令，不原地覆盖源材质或源贴图。真实适配器负责 DCC 事务、撤销和失败清理；参考插件仅验证接口，不证明实际 DCC 具备这些保证。

运行 [参考插件](../_examples/plugin_demo.py)：

```powershell
uv run python _examples/plugin_demo.py --transport python
uv run python _examples/plugin_demo.py --transport process
```

两种方式返回相同业务结果，核心没有 DCC 依赖。独立进程方式需要能定位仓库源码且装有第三方依赖的 Python 环境；CLI 使用 UTF-8，默认子进程超时为 60 秒，可由客户端调整。

本地子进程从其他工作目录启动时，可传入 `JsonCommandService([...], cwd=仓库根目录)`，使 `python -m iris_relay` 能定位核心；该设置只影响子进程，不改变宿主工作目录。

Python 3.7 宿主可从 `iris_relay.transport` 导入两种子进程服务，从 `iris_relay.protocol` 导入 `ValidationError`；这些模块、`iris_relay.model.Material` 及公开 `iris_relay.native` 辅助接口不加载核心或 SDK。旧 `iris_relay.plugin` 导入路径保留，但该模块本身仍面向 Python 3.11+。`env=环境字典` 可指定子进程环境；不传时继承父进程。公共宿主控制层仅对子进程清除宿主注入的 `PYTHONHOME` / `PYTHONPATH`。

`plugins/core.py` 提供 Maya／C4D 共用的文件检查与导入／导出流程：调用公开服务、检查响应、解析完整目标材质、定位资源、执行宿主预检，再交适配器创建。导出必须从当前原生材质重新读取，通过核心导出及校验后独占创建新文件，不覆盖已有输出。插件只创建材质，默认不替换对象绑定；事务与失败清理由各宿主适配器实现。

Maya 字典的端口 `native.properties.enum_values` 是原生枚举编码表，例如 `[5, 6, 7]` 将标准 RGB 索引 0/1/2 写为对应 `aiColorToFloat.mode`。它不参与语义计算；动态连接到该枚举端口会被宿主明确阻断。标准 NodeDef 的 integer 端口在旧材质协议中使用数值字段，要求实际值为整数，导出时写为 MaterialX integer。

## 联合校正扩展

核心可配置一个实现 `JointPredictor` 的对象：`model_id` 标识模型与版本，`predict(context)` 返回 `CorrectionProposal`。上下文包含完整 `source`、`intermediate`、`target` 和 `dictionary_versions`，均为隔离副本。

提案中的 `InputChange` 指定节点、原生输入端口及新值。一次提案可以修改多个节点、多个属性。首版只允许字典标记 `adjustable` 的数值或向量输入，不能修改受连接驱动的输入、节点拓扑或纹理资源。所有操作验证通过后整组应用；任何一个操作失败就保留完整基础方案。

状态包含 `not_requested`、`skipped`、`unavailable`、`rejected`、`unchanged`、`applied`。缺少模型或模型异常会报告警告；`applied` 仅表示提案成功应用，不证明外观变好。

仓库没有训练模型、权重或真实训练数据，测试预测器只验证接口。后续模型应学习整体关联，不能把测试提案中的固定值当作校正算法。新增贴图或拓扑调整需要扩展操作协议与验证规则。
