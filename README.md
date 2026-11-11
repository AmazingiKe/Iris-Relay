# Iris Relay

Iris Relay 探索跨渲染器的材质转换：以字典建立材质语义对应，以小型神经网络联合校正材质在目标渲染器中的整体外观，通过最小侵入的 DCC 接入层读取和应用材质。

系统只修改材质，不通过改变灯光、相机、曝光或色彩管理来补偿差异。“整体校正”指材质内部多种因素的关联调整，不是一个统一系数，也不是整幅图像的后期处理。

当前已完成初步核心：统一 API、材质图协议、声明式字典、转换与诊断报告、联合校正扩展接口，以及通过同一 API 运行的参考插件。Python 核心不依赖 DCC 运行时；0.4.0 引入官方 MaterialX 1.39.5 SDK。

新的 [MaterialX 核心](_docs/materialx-core.md) 支持官方节点查询、`.mtlx` 校验与保存、自定义 NodeDef／子图、资源枚举与改写、语义字段差异及受限原生桥接。专有节点没有目标规则时保留源信息并阻断。[最新交换报告](_docs/editable-materialx-20261111.md)完成 A12 全量 1,801 份文档，其中 997 份标准／内联图、804 份仅保留原生结构；最终 50 个材质通过 Blender → Maya／C4D、两端当前原生再导出 → Blender，以及 Maya → C4D 的保存重开。原生保留文档不能计入转换成功。

已提供 Blender Cycles、C4D Redshift 和 C4DtoA 的真实节点目录与基础字典，包含节点名称、资产 ID、整数 code 和端口绑定。早期 6 组源材质完成保存重开和原生识别，随后 18 组转换完成目标创建、保存重开及参数／连接核对；最新批次另列于上述交换报告。详见[原生字典与验证范围](_docs/native-dictionaries.md)。

基础语义包括标准表面、图片、切线法线、高度凹凸、通道及 RGB 加权求和；标准数学直接复用官方 NodeDef。本轮补充受限 ColorRamp／Mix 配方、RGBA、命名 UV／静态变换、原生功能分组和共享控制；支持范围按精确宿主字典与实际预检决定。Blender、Maya 与 C4D 提供[统一操作界面](_docs/unified-ui.md)，包含连接选择、导入／导出和结果摘要。尚无完整生产插件或任意材质视觉一致性证明；[验收清单](_docs/overnight-acceptance-20261111.md)分别跟踪结构与首轮神经实验。示例中的 `reference.gloss` 和 `reference.rough` 仍仅用于协议测试。

[首轮真实神经实验](_docs/neural-experiment-20261111.md)已完成 Cycles → Arnold 的 32 个材质族、128 组变体训练及独立渲染测试。测试平均误差下降 34.29%，但材质族置信区间跨零，且包含退化样本，尚未证明稳定改善。模型不默认启用；权重与训练数据留在仓库外。

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
.\.venv\Scripts\python.exe -m pip install "materialx==1.39.5" "pypinyin==0.55.0"
.\.venv\Scripts\python.exe -m iris_relay --dictionaries _examples/dictionaries --request _examples/convert-request.json
```

## Blender 材质交换界面

脚本入口为 [`_examples/blender_demo_gui.py`](_examples/blender_demo_gui.py)，现已接入真实 MaterialX 导出与导入。在 Blender 文本编辑器打开它，按需填写顶部 `REPO_ROOT` 为仓库根目录的绝对路径，然后点击“运行脚本”。从仓库原位置打开时可以留空自动定位；复制到未保存文本块或移到别处时需要填写路径。本机路径只在 Blender 中修改，不提交；不需要安装 Blender 插件。

运行后在文本编辑器或 3D 视图的 `N` 侧栏选择 **Iris Relay** 标签，侧栏过窄时可拖动左边界加宽：

1. 在“导出”页选择材质，或点击“使用活动材质”。
2. 点击“检查材质”，读取当前节点图，调用 `materialx.export` 和 `materialx.validate`，查看下方诊断。此操作不写文件。
   若提示包含位移，而本次只想测试表面，可勾选“仅导出表面（排除位移）”后重新检查。该选项默认关闭；位移排除会留下警告及源节点快照。
3. 选择尚不存在的 `.mtlx` 文件绝对路径，父目录须已存在。
4. 若有近似转换警告，查看诊断后按需勾选“允许带警告导出”；默认不保存有警告的结果，阻断错误不能通过勾选绕过。
5. 点击“导出 .mtlx”。每次导出重新读取并校验当前材质，成功后明确显示“已导出”。可通过“复制完整报告”取得核心响应、源图快照和诊断。

导出后可直接切换到“导入”页试验往返，输入路径会自动填入刚保存的文件：

1. 点击“检查材质”，校验文档、当前 Blender 版本的转换支持及贴图文件；此时不创建材质。也可以通过文件夹按钮选取已有 `.mtlx`。
2. 查看诊断，按需勾选“允许带警告导入”，再点击“导入为新材质”。操作会重新读取文档和检查图片，不复用旧检查结果；错误不能通过勾选绕过。
3. 成功后报告显示实际新材质名（带 `_IrisRelay` 后缀，同名时由 Blender 分配编号）。可在对象的材质下拉框中手动选择它。导入不要求活动对象，不自动更换已有绑定。

新材质启用保留标记（Fake User），未绑定对象也能随 `.blend` 保存。导入操作加入 Blender 全局撤销，可通过顶部“编辑 → 撤销”恢复；撤销、重做或打开文件后清除过期报告。源文件与贴图只读，导入失败由适配器清理本次新增数据。图片相对路径以 `.mtlx` 所在目录解析，缺图时明确阻断；不自动下载或打包资源。

导出采用排他创建，不覆盖现有文件；写入失败会尝试清理本次创建的残留。源材质、绑定和贴图保持不变，不改变场景渲染条件。GUI 保留原贴图引用；需要便携交付时可使用[资源打包工具](_docs/editable-materialx-20261111.md#c2rgba坐标与便携资源)复制贴图并改写相对 URI。文件可保存、目标可转换和外观一致是不同结论。

界面位于 `plugins/blender/ui.py`，`controller.py` 负责核心连接、导入导出检查和文件流程，`adapter.py` 负责 Blender 材质读写；`from plugins.blender import BlenderAdapter` 保持兼容。首次操作时优先使用仓库已有 `.venv` 通过公开 `JsonCommandService` 调用核心，不向 Blender 安装依赖。不存在 `.venv` 时尝试进程内服务，两种方式都要求 MaterialX 1.39.5；依赖缺失或版本不同会明确报错。本机 Blender 5.2.3 自带 SDK 为 1.39.4，因此本轮使用仓库环境的 1.39.5。

GUI 按需加载核心；重复运行入口会重新加载宿主模块、清空输入和诊断。关闭 Blender 后不会自动启用。需要在当前会话移除界面时，在 Blender Python 控制台执行 `from plugins.blender import ui; ui.unregister()`。

当前受已实现功能及实际节点接口能力限制；字典跨软件版本复用，已验证环境单独记录。读取所选表面的上游图，解析转接点、整理框、默认／命名 UV 及静态 POINT Mapping 序列；坐标通过标准 MaterialX 几何属性和矩阵节点导出，Blender 导入后重建可编辑链路。节点组按实例只读展开，保留输入覆盖值和连线，回导创建可编辑功能节点；组层级、布局及接口限制见[节点组说明](_docs/materialx-core.md#blender-节点组)。未参与表面的节点有信息提示并保留顶层源快照。参与表面的未知节点、动态坐标变换、其他坐标来源、未支持的参数或连接仍会阻断。Blender 旧 Principled `GGX + BURLEY` 配置已加入识别；关闭 SSS 时未连接的 SSS 辅助值按字典条件报告为未启用。具体范围见[原生字典文档](_docs/native-dictionaries.md)。

颜色接粗糙度等标量输入时，由宿主读取当前工作空间的亮度系数，核心按字典规则生成显式 MaterialX `luminance → extract`，不猜测 R 通道。目前只核验 Linear Rec.709；缺失或不支持的色彩上下文会阻断。此链可通过 `materialx.convert` 转回 Blender 的 `Vector Math / DOT_PRODUCT`，保留文档中的三个常量系数；GUI 和独立脚本共用该 API。位移和渲染外观仍未验证。

独立宿主交互逻辑验证（不会操作当前打开的 Blender 场景）：

```powershell
& "本机blender.exe路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_gui.py
& "本机blender.exe路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_blender_read.py
```

检查覆盖入口重复运行、真实导出及文档校验、导入警告策略、新材质保存重开、缺图与坏文档阻断、已有文件保护、活动图未知节点阻断、源材质保护、图片法线／高度链路、位移排除、UV 与静态 POINT 矩阵保留、未知坐标来源拒绝、注册卸载和 Python 导入兼容。更多坐标夹具见 `_scripts/verify_blender_coordinates.py`。

2026-11-06 木地板样本验证：196 项自动测试通过，核心及导出控制层合计覆盖率 92%（控制层 95%）。原有 15 组 Blender 5.2.3 LTS GUI 操作检查及新增读取回归通过。用户提供的 `raw_wooden_floor_01_4k.blend` 在独立进程完成“默认阻断位移 → 选择表面 → 确认警告 → 保存”的 GUI 操作流程，文件读回通过 MaterialX 1.39.5 校验。源 `.blend` 与四张贴图的 SHA-256 前后相同。底色、粗糙度、法线链路已导出；位移排除并报告，未进行渲染外观对照。样本保留原位置，输出与报告位于本地忽略目录，均不提交仓库。此前的独立前台界面验证记录保留在 Git 历史中。

2026-11-09 木地板回读验证：先前导出的 `.mtlx` 已在独立 Blender 5.2.3 LTS 场景中生成新材质，保存重开后核对 42 个输入、6 条含材质出口的连接，并再次导出通过校验的 `.mtlx`。亮度系数、底色／粗糙度／法线图片引用保留；原 `.blend` 与四张贴图哈希仍与导出前一致。208 项自动测试通过，核心及导出控制层合计覆盖率 92%（MaterialX 桥接 94%）；原 GUI、读取回归及两条既有 MaterialX 宿主夹具通过。复现命令及边界见 [MaterialX 文档](_docs/materialx-core.md#验证与复现)，位移及渲染外观仍未验证。

2026-11-10 GUI 导入验证：228 项自动测试通过，核心及导入导出控制层合计覆盖率 92%（控制层 96%）；18 组后台 GUI 操作检查及读取回归通过。木地板样本重新通过 GUI 操作完成“导出表面 → 检查导入 → 确认警告 → 新建材质 → 保存重开 → 再导出”，核对 42 个输入和 6 条连接，原 `.blend` 与四张贴图哈希不变，对象绑定及记录的场景条件不变。本地结果为 `.local/wood-gui-roundtrip-01/`，位移未导入，未做渲染对照。

同日在独立前台测试副本检查面板、输入文件选择器、检查与导入按钮；导入后材质数从 2 增至 3、图片数从 8 增至 11，通过顶部编辑菜单撤销后恢复为 2 和 8，旧成功报告随之清空。未操作用户原先打开的 Blender 场景。

## Maya 2022 导入

在 Maya 脚本编辑器 **Python** 页签运行，替换仓库路径：

```python
import runpy

runpy.run_path(r"仓库绝对路径/_examples/maya_demo_gui.py", run_name="__main__")
```

选择文件／目录 → 检查选中 → 查看并接受警告 → 导入为新材质。默认不替换绑定，每份材质可撤销。实测版本为 Maya 2022.5、MtoA 5.3.5.3、Arnold 7.2.5.3；复用仓库 `.venv`，不在 Maya 安装 MaterialX。上一轮 A1 的 89 份文件中，31 份通过节点、贴图连接及保存重开检查；其余明确阻断。使用方式、已解决问题和限制见 [Maya 导入文档](_docs/maya-materialx-import.md)。未验证渲染外观。

## 统一接口

阅读 GUI 导出代码时，可按下面顺序定位；主入口内已有步骤注释，具体处理拆到对应的小函数：

| 顺序 | 入口 | 负责什么 |
| --- | --- | --- |
| 1 | [`ui.run_export`](plugins/blender/ui.py) | 读取界面选择，调用控制层并显示结果 |
| 2 | [`controller.export_material / check_material`](plugins/blender/controller.py) | 组织读取、核心请求、校验、警告确认与保存 |
| 3 | [`BlenderAdapter.read_material`](plugins/blender/adapter.py) | 从 Blender 读取表面图、节点参数、贴图与源快照 |
| 4 | [`materialx_bridge.export_material`](iris_relay/materialx_bridge.py) | 按字典生成标准节点和连接，返回通过校验的 XML 内容 |

核心返回 XML 后，控制层才把内容保存到所选 `.mtlx` 路径；“构建 MaterialX 节点图”与“写文件”是两个步骤。

回读验证可从 [`verify_materialx.main`](_scripts/verify_materialx.py) 按步骤阅读：`input_documents` 读取文档，`prepare_cases` 调用公开 API 转换，`run_host` 启动独立 Blender 创建并检查材质。核心反向入口为 [`materialx_bridge.convert_document`](iris_relay/materialx_bridge.py)，宿主创建仍由 `BlenderAdapter.create_material` 完成。

GUI 导入入口为 `ui.run_import` → `controller.check_import / import_material` → `BlenderAdapter.create_material`，依次负责界面选择、公开 API 校验与转换、目标节点创建。控制层不读取核心桥接实现。

所有插件依赖 `RelayService.handle(request) -> response`。进程内使用 `RelayAPI`；跨进程可使用 JSON 子进程客户端或 `HttpRelayService`，请求与响应协议共用。插件只读写宿主材质，转换和校正规则集中在核心。

可选的本机 HTTP 服务位于 `iris_relay/server/`：执行 `uv sync --extra dev --extra http`，然后运行 `uv run --extra http python -m iris_relay.server --dictionaries dictionaries`。默认监听 `127.0.0.1:8765`；宿主设置 `IRIS_RELAY_HTTP_URL=http://127.0.0.1:8765` 后重新创建服务即可使用。默认调用方式不变，连接失败不静默回退。启动、模型加载、资源约定及限制见 [HTTP 服务调用](_docs/http.md)。

API 提供能力查询、通用功能定义查询（`semantics`）、原生模式识别（`identify`）、原生绑定查询、材质校验和转换。真实配置按宿主、渲染器与节点系统选择共享字典，不因软件版本变化自动阻断；实际端口缺失、类型不符、未知节点、无法表达的连接和非法数值均有明确错误。软件版本保留为运行与验证记录，第三方旧字典仍可声明显式版本限制，详见 [ADR-0010](_docs/adr/0010-共享字典与运行时接口兼容检查.md)；缺少模型时可返回基础转换结果及警告。插件辅助接口默认不应用带警告的方案，也不会自动替换场景中的材质绑定。

## 目录

```text
iris_relay/            核心、公开 API、插件辅助接口与命令行
  server/              可选 HTTP 接入、配置与核心实例生命周期
dictionaries/          真实转换字典；catalogs/ 保存原生节点探测目录
plugins/               宿主接入层，按实际宿主分目录
  blender/             GUI（ui.py）、导入导出控制（controller.py）、材质读写（adapter.py）
  maya/                Maya 2022 导入导出界面、公开 API 控制层与原生节点创建
  cinema4d/            Redshift 原生适配器、导入导出窗口及当前场景控制层
  presentation.py      三宿主共用的连接检查、状态文字和报告摘要
_scripts/              节点探测、字典生成和独立宿主验证
_examples/             可执行参考插件、GUI 测试入口、请求和示例字典
_tests/                图转换、接口、失败回退和跨进程测试
_docs/                 API 文档和架构决策
```

核心 `iris_relay/` 与宿主接入 `plugins/` 在根目录并列，不使用 `src/`。示例和验证脚本自行定位仓库根目录；第三方插件在导入核心前将仓库根目录加入 Python 搜索路径。按实际内容创建目录，不预建空目录。字典的具体 code 与支持边界见[原生字典文档](_docs/native-dictionaries.md)。

## 开发检查

```powershell
uv run --extra dev --extra http pytest -q
uv run --extra dev --extra http coverage run -m pytest -q
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

下一步继续扩展真实材质导出与节点覆盖，并推进渲染对照及首轮神经实验之后的泛化验证。MaterialX 桥接已显式处理颜色转亮度、标量转灰度颜色，以及受限的图片 Alpha 模式和通道；Blender 完整颜色反相现可直接绑定官方 NodeDef，部分反相与尚未实现的端口变换仍阻断。图片双输出、资源探测及跨宿主限制见 [Alpha 说明](_docs/materialx-core.md#图片-alpha-与透明度连接)。

A1 全量测试继续覆盖全部 1,803 个材质。静态 UV 读取已扩展到常量数学、向量加减乘／缩放及五种旋转模式，回导重建可编辑 Mapping；常量计算子图与共享坐标节点身份未重建，动画和跨分支共享依赖明确阻断。组接口的一次类型转换保留到展开后的连接，由核心显式表达，浮点组默认值可还原为灰度颜色。10 组坐标、3 组反相、4 组接口夹具完成独立 Blender 保存重开；完整 HSV 增加五个端口及标准数学子图，回导重建可编辑原生节点；五组 HSV 夹具完成独立回读。389 项自动测试通过，合计覆盖率 93%。实际样本累计 690 项完整导出并具备独立回读证据、1,113 项阻断；本轮新增 88 项，已按用户时间限制停止新增转换，尚未完成全量目标，详见 [A1 问题与验证记录](_docs/a1-materialx-roundtrip.md)。
