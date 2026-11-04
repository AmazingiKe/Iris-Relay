# 统一插件 API v1

所有插件调用同一抽象服务：`RelayService.handle(request) -> response`。业务协议与传输方式分离；Python 插件可直接使用 `RelayAPI`，其他宿主可发送 UTF-8 JSON 给命令行核心。插件只读取和创建宿主材质，不实现字典转换或联合校正。

当前实现为初步协议基线，参考字典没有真实渲染器适配或视觉等价性保证。

## 快速调用

在仓库根目录执行 `uv sync --extra dev` 后：

```python
import json
from pathlib import Path
from iris_relay import RelayAPI

api = RelayAPI.from_directory("examples/dictionaries")
request = json.loads(Path("examples/convert-request.json").read_text(encoding="utf-8"))
response = api.handle(request)
print(response["status"])
```

等价的命令行调用：

```powershell
uv run iris-relay --dictionaries examples/dictionaries --request examples/convert-request.json
```

`--request -` 从标准输入读取 JSON，`--output -` 向标准输出写入 JSON；两者均为默认值。`--output 新文件.json` 写入一个新文件，不覆盖已有文件，也不自动创建父目录。退出码 `0` 表示有可用响应（可能包含警告），`2` 表示请求、转换或文件读写失败。插件必须查看 JSON 的 `status` 和报告，不能只看退出码。

## 请求与响应

请求必须包含以下字段，不接受额外字段：

| 字段 | 含义 |
| --- | --- |
| `api_version` | 字符串 `"1"` |
| `request_id` | 非空字符串，由插件生成，用于对应响应 |
| `operation` | `capabilities`、`validate` 或 `convert` |
| `payload` | 对应操作的数据对象 |

| 操作 | payload | 结果 |
| --- | --- | --- |
| `capabilities` | `{}` | 已加载字典、渲染器精确版本、支持操作、校正器是否配置 |
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

## 材质图

完整示例见 [convert-request.json](../examples/convert-request.json)。材质本身只允许以下顶层字段，不接受灯光、相机或曝光修改：

| 字段 | 要求 |
| --- | --- |
| `schema_version` | 字符串 `"1"` |
| `name` | 非空材质名 |
| `renderer` | `id` 和 `version`；必须匹配已加载字典的精确版本 |
| `nodes` | 节点列表，每项包含 `id`、`type`、`inputs`、`outputs`，可含 `metadata` |
| `links` | 每项为 `{"from": {"node": "...", "socket": "..."}, "to": {"node": "...", "socket": "..."}}` |
| `output` | 材质输出的 `node` 和 `socket` |
| `textures` | 可省略，默认 `{}`；按资源 ID 索引的贴图描述 |
| `metadata` | 可省略，默认 `{}`；只透传，不参与场景操作 |

`inputs` 是端口名到值的对象，`outputs` 是输出端口名列表。节点 ID 必须唯一，连接必须指向存在的端口，每个输入最多一条连接，图必须无环。连接输入仍应在 `inputs` 中提供默认值；核心保留默认值和连接的区别。

字典允许的输入类型为 `float`、三个分量的 `color` / `vector`、`string`、`bool`、`texture`、`shader`。`texture` 值为 `null` 或 `{"texture": "资源ID"}`，`shader` 默认值为 `null`，实际层次通过连接表达。资源引用必须存在于 `textures`。

每项贴图描述包含 `uri`、`color_space`、`coordinates` 对象，可含 `metadata`。核心保留它们，但不读取贴图像素、不自动转换色彩空间、不解析 UV、不下载资源，也不更改资源路径。真实适配器须根据共同约定解析这些信息。示例使用的 `memory://reference/albedo` 只代表参考资源，并无附带图像文件。

源图中未列出的已知输入由源字典默认值补全。源图含未知输入或输出时阻断转换。目标端增加了源端没有的语义时，使用目标字典默认值并发出 `TARGET_DEFAULT` 警告；目标端丢失源语义则阻断。

API 接受有限数值；JSON 文件拒绝重复键、NaN 和 Infinity，单次 JSON 读取限 16 MiB，嵌套限 64 层，材质图限 10000 个节点及 100000 条连接。限制是输入边界，不代表已验证的批量处理性能。

## 字典

完整格式见 [参考光泽度字典](../examples/dictionaries/reference.gloss.json) 和 [参考粗糙度字典](../examples/dictionaries/reference.rough.json)。字典目录由核心启动方指定，API 请求不能指定加载路径或可执行代码。

- 顶层包含 `schema_version`、`id`、`version`、`renderer`、`nodes`。
- `renderer` 包含 `id` 及明确列出的 `versions`；同一渲染器版本不能由多份字典覆盖。
- 每个原生节点类型映射到唯一 `semantic`；同一字典不能把多个目标实现映射到同一语义后再隐式选择。
- 输入规则包含 `semantic`、`type`、`default`，可指定 `range`、`transform`、`adjustable`；输出规则包含 `semantic`、`type`。
- `transform` 当前只允许 `identity` 和用于浮点值的 `one_minus`。默认值和范围描述原生参数域，解码后进入语义域，编码时逆变换回目标原生域。
- 非恒等变换遇到连接时返回阻断报告，不将单个数值操作误当作整张贴图变换。

字典对语义、单位、默认值和范围的陈述必须有具体渲染器依据。核心只能验证声明的一致性，不能自动证明物理含义或视觉等价性。

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

调用 `convert_material(adapter, service, target)` 获取 `PluginResult`。`service` 可以是 `RelayAPI` 或 `JsonCommandService`，其他实现只需满足 `RelayService` 协议；后者使用参数列表启动子进程，不经 shell 执行。

默认只有 `status == "ok"` 才调用 `create_material`。警告响应保留供界面展示；插件明确选择接受已展示的近似方案后，可设置 `accept_warnings=True`。错误响应从不应用。新建后的材质绑定替换属于宿主的显式、可撤销操作，辅助接口不会自动执行。

`create_material` 必须只创建材质及必要的派生资源，不从 metadata 中读取或执行任何场景修改命令，不原地覆盖源材质或源贴图。真实适配器负责 DCC 事务、撤销和失败清理；参考插件仅验证接口，不证明实际 DCC 具备这些保证。

运行 [参考插件](../examples/plugin_demo.py)：

```powershell
uv run python examples/plugin_demo.py --transport python
uv run python examples/plugin_demo.py --transport process
```

两种方式返回相同业务结果，核心没有 DCC 依赖。独立进程方式需要一套已安装核心的 Python 环境；CLI 使用 UTF-8，默认子进程超时为 60 秒，可由客户端调整。

## 联合校正扩展

核心可配置一个实现 `JointPredictor` 的对象：`model_id` 标识模型与版本，`predict(context)` 返回 `CorrectionProposal`。上下文包含完整 `source`、`intermediate`、`target` 和 `dictionary_versions`，均为隔离副本。

提案中的 `InputChange` 指定节点、原生输入端口及新值。一次提案可以修改多个节点、多个属性。首版只允许字典标记 `adjustable` 的数值或向量输入，不能修改受连接驱动的输入、节点拓扑或纹理资源。所有操作验证通过后整组应用；任何一个操作失败就保留完整基础方案。

状态包含 `not_requested`、`skipped`、`unavailable`、`rejected`、`unchanged`、`applied`。缺少模型或模型异常会报告警告；`applied` 仅表示提案成功应用，不证明外观变好。

仓库没有训练模型、权重或真实训练数据，测试预测器只验证接口。后续模型应学习整体关联，不能把测试提案中的固定值当作校正算法。新增贴图或拓扑调整需要扩展操作协议与验证规则。
