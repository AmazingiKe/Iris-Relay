# HTTP 服务调用

HTTP 是统一 `RelayService` 的可选传输层。核心、字典和转换规则与进程内／JSON 子进程调用共用；现有插件默认方式不变。

## 启动服务

在仓库根目录执行：

```powershell
uv sync --extra dev --extra http
uv run --extra http python -m iris_relay.server --dictionaries dictionaries
```

服务默认监听 `http://127.0.0.1:8765`。可通过 `--port` 调整端口；`--host` 只接受回环地址。保持终端运行，按 Ctrl+C 停止。端口占用、字典无效或指定模型无法加载会明确启动失败。模型和字典路径相对于启动工作目录。

可选模型通过 `--model "你的模型目录/model.json"` 指定；不加载 `weights.pt`，推理无须 PyTorch。不传 `--model` 时未配置预测器。请求仍须显式指定 `calibrate` 并提供适用上下文；首轮模型的适用范围和负面结果见[神经实验](neural-experiment-20261111.md)。

## 插件调用

```python
from iris_relay.http_client import HttpRelayService

service = HttpRelayService("http://127.0.0.1:8765", timeout=60)
response = service.handle({
    "api_version": "1",
    "request_id": "查询能力",
    "operation": "capabilities",
    "payload": {},
})
```

客户端地址只包含协议、主机、端口及可选末尾 `/`，不填写 `/v1/handle`，不接受 URL 中的凭据、查询参数或路径前缀。客户端仅使用标准库，可被 Maya 2022 的 Python 3.7 导入，不会加载核心、FastAPI 或 MaterialX。

在 Blender／Maya 脚本环境创建服务之前设置：

```python
import os
os.environ["IRIS_RELAY_HTTP_URL"] = "http://127.0.0.1:8765"
```

然后运行现有 `_examples/blender_demo_gui.py` 或 `_examples/maya_demo_gui.py` 入口。已有会话应关闭旧面板并重新运行入口，使新建服务采用该配置。移除环境变量后重新创建服务恢复默认方式。显式 HTTP 连接失败会报告错误，不自动切回子进程。

C4D 可通过 `plugins.cinema4d.create_service()` 获取相同选择逻辑，并将返回的服务注入现有适配器；该入口不代表新增了 C4D GUI。

服务端工作线程负责核心运算，宿主材质创建仍在宿主原有流程中。此次没有将现有 GUI 改为异步任务界面，调用期间可能等待网络响应；客户端 `timeout` 是网络操作超时，不是整项转换的硬截止或取消操作。

## 传输协议与资源

| 路径／情况 | 行为 |
| --- | --- |
| `GET /health` | 就绪时 HTTP 200，返回 `status` 和 `api_version` |
| `POST /v1/handle` | `application/json`，请求与响应沿用[统一 API](api.md) |
| 有效业务响应 | HTTP 200，仍可能为 `status: error` 或 `warning` |
| 坏 JSON／重复键／非有限值 | HTTP 400 |
| 请求超过 16 MiB | HTTP 413；响应同样有大小限制 |
| 核心忙碌 | HTTP 503；客户端不自动重试 |
| 网络异常或非 200 | 客户端抛出带错误码的 `ValidationError` |
| 错误响应版本、请求 ID 或结构 | 客户端拒绝该响应 |

API 只交换材质数据和资源引用，不传输贴图文件。本机多 DCC 共用已有文件路径；相对路径仍由宿主导入流程按 `.mtlx` 位置解析。资源打包继续使用既有工具，不把 HTTP 传输成功视为贴图可用。

服务复用一个核心实例并串行处理业务；多个 DCC 同时请求时，忙请求明确失败。客户端断开后，正在执行的核心调用可能继续直到结束；不会在同一实例上提前开启第二次计算。只读 `/health` 不进入业务执行槽。

首版用于本机 DCC 接入，不提供远程鉴权、TLS、资源上传或多用户隔离。默认禁用代理转发解释和跨站调用；客户端禁用系统代理、自动重定向和重试。FastAPI 的 OpenAPI 描述可在 `/openapi.json` 查看，业务字段及各 operation 的完整约束以统一 API 文档和核心校验为准。

## 验证与复现

```powershell
uv run --extra dev --extra http pytest -q _tests/test_http_client.py _tests/test_http_server.py _tests/test_http_plugin.py _tests/test_http_e2e.py
```

端到端测试启动并清理自己的 Uvicorn 进程，核对真实 MaterialX 导出／跨目标转换的材质图、连接、中文贴图 URI 与进程内响应完全一致；参考插件核对 Python、子进程、HTTP 三种传输结果相同。客户端测试使用真实本机 HTTP 连接覆盖断连、超时、重定向、坏响应和大小限制。服务端测试覆盖生命周期、忙请求、严格 JSON 和失败报告。

本轮最终定向回归包含上述四组 HTTP 测试、Blender／Maya 控制层及预测器协议，共 155 项通过；新增 HTTP 客户端和服务端合计覆盖率为 94%（包含分支）。Ruff、格式检查、锁文件一致性及变更文档的本地链接检查通过。

手动运行参考插件时，另开服务终端使用 `--dictionaries _examples/dictionaries`，再执行：

```powershell
uv run --extra http python _examples/plugin_demo.py --transport http --url http://127.0.0.1:8765
```

参考字典与真实宿主字典不同，必须为该示例启动匹配配置的服务。

实机验证入口为 `_scripts/verify_http_host.py`，仅在独立 mayapy 或后台 Blender 运行。先启动真实字典 HTTP 服务，再将文档目录和新输出目录传入：

```powershell
& "本机mayapy.exe路径" _scripts/verify_http_host.py --host maya --url http://127.0.0.1:8765 --documents "待验证文档目录" --out "新验证目录"
& "本机blender.exe路径" --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/verify_http_host.py -- --host blender --url http://127.0.0.1:8765 --documents "待验证文档目录" --out "另一个新验证目录"
```

Maya 需要已配置可用 MtoA 的独立运行环境；此入口不修改用户当前打开的场景。2026-11-11 实测 Maya 2022.5／Arnold 7.2.5.3 和 Blender 5.2.3：各通过一份图片连接标准表面夹具的 HTTP 导入、保存重开和当前原生再导出。Maya 核对 2 个节点、19 个有效输入、2 条连接和 1 张贴图；Blender 核对 34 个输入和 2 条连接。两端源文档和贴图哈希不变，本机证据位于 `.local/http-host-smoke-01/`。这属于接入冒烟验证，不替代已有材质覆盖报告；本轮未进行 C4D 实机 HTTP 或渲染验证。

架构决定见 [ADR-0009](adr/0009-多传输服务接入.md)。实现参考 [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) 和 [Python urllib.request](https://docs.python.org/3/library/urllib.request.html)；生命周期初始化与客户端代理行为按官方接口实现。

## 本轮实际模型验证

2026-11-11 使用已训练的 `iris.n1/20261111/57c86d864031ecc2` 启动独立 HTTP 服务，对原实验测试样本 `material_1126_v0` 重放相同输入与上下文。HTTP 与直接 Python 的完整响应相等，校正状态为 `applied`，两项调整均由原模型产生。替换为未验证的场景条件后明确返回 `rejected`，调整列表为空，目标图与基础转换一致。

模型、实验配置、数据集及源 MaterialX 的 SHA-256 前后相同。本机证据位于忽略目录 `.local/http-model-smoke-20261111-01/summary.json`，包含原始响应和复现脚本；权重与数据不提交。本次未训练或渲染，只证明模型调用与失败回退经过 HTTP 后保持一致。

## 遇到的问题与处理

- 宿主侧导入控制器会提前加载核心：将进程内服务导入延迟到实际选择该方式时，并用隔离 Python 验证 HTTP 路径不加载核心和第三方依赖。
- 网络非 200 响应只显示状态码：客户端在较小大小限制内读取合法错误信封，保留 `SERVICE_BUSY` 等说明；损坏响应仍回退到通用传输错误，不自动重试。
- 畸形 Host 在框架解析后可能回退为监听地址：直接校验原始 Host 头，增加回归，避免误接收。
- JSON 中孤立 Unicode surrogate 导致错误回包再次编码失败：业务响应使用 JSON 转义保留核心值，传输错误中无法编码的请求 ID 使用 `null`；增加嵌套值和失败路径回归。
- Python 对超长整数 JSON 的解析可能抛出普通 `ValueError`：客户端统一转换成 `INVALID_RESPONSE`，不让底层解析异常穿透调用协议。
- 客户端断开不代表线程中的业务已经停止：执行槽跟随实际核心调用释放，取消等待不会开启重叠计算。
