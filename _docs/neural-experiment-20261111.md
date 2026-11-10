# 首轮真实神经联合校正实验

日期：2026-11-11。执行依据为 [ADR-0007](adr/0007-首轮神经联合校正实验.md)，在 C1–C5 的结构交换、宿主验证与 597 项回归完成后训练。

**本轮已完成真实训练、原生应用和隔离测试，但尚未证明模型能稳定改善新材质。** 测试集平均误差下降 34.29%，材质族重采样的 95% 区间为 −27.90% 到 64.29%，跨过零；20 个测试变体中 7 个改善、9 个退化、4 个处于测得噪声范围。保留这些负例，没有按测试结果重调参数或进行第二次训练。

## 方向与数据

源为 Blender 5.2.3 LTS／Cycles，目标为 Maya 2022.5／MtoA 5.3.5.3／Arnold 7.2.5.3。数据来自 A12 的实际材质副本，原场景、材质缓存及纹理只读；不是随机颜色球、人工固定偏移或合成误差标签。

| 集合 | 材质族 | 参数变体 |
| --- | --- | --- |
| 训练 | 22 | 88 |
| 验证 | 5 | 20 |
| 测试 | 5 | 20 |
| 合计 | 32 | 128 |

其中 17 个材质族引用实际贴图。名称去除数字后缀后按族隔离，共享实际纹理 SHA-256 的材质只取一份，数值等价的无纹理材质去重；所有参数变体都在同一集合。实际 68 维输入若跨集合完全相同则中止。该策略依赖已有材质名称和资源证据，不证明未标注的外部资产来源完全独立；本轮仍只有一个源场景。

每个材质创建四份源副本，`(Roughness, Specular IOR Level)` 分别为 `(0.18,0.20)`、`(0.36,0.30)`、`(0.58,0.40)`、`(0.78,0.45)`。只接受实测 `MULTI_GGX`、不透明、单一 Principled、底色上游可转换，且没有连接驱动的反射参数、法线、涂层、Sheen、发光或透射的图。材质族名单、纹理哈希及划分在训练前冻结。

准备过程曾发现一张 WebP 可由 Blender 解码，但不能由当前 Arnold 解码器读取；在首次训练前将 `material_0389` 替换为 `material_0937`，重新冻结 32 族与全部源渲染。AVIF／WebP 显式排除，保留原失败日志。没有在查看测试效果后替换样本。

最终配置为 `neural-experiment-06/job.json`，条件 SHA-256：

```text
1aa2593b329138c195f0550d565c9751d240b3a2b9c6ecad78f54ebdf1a3abd5
```

## 固定条件与基线

使用独立、可丢弃场景。两端读取相同球体的顶点、面和逐面 UV；Y 向上单位球，32×64 分段，网格 SHA-256 为 `cf1425c05e91b00baee874ab3d89d7a7f4fe7c6f1d655f33e66fdd9e42692b9e`。训练使用默认 `map1`，额外 `DetailUV` 只用于独立坐标探测。

| 条件 | 冻结值 |
| --- | --- |
| 图像／相机 | 64×64 RGBA float EXR；正交相机 Z=4，宽 2.7；像素比与画幅比均为 1 |
| 光照 | 均匀环境线性 RGB `[0.5,0.5,0.5]`；无其他灯光、环境贴图或场景物体 |
| 颜色 | 线性 Rec.709、无曝光补偿；预览统一转 Standard sRGB，指标直接读 EXR |
| Cycles | CPU 8 线程，64 samples，8 bounces，关闭去噪和 dither |
| Arnold | CPU 8 线程，AA=4，diffuse/specular samples=2，许可失败即终止 |
| 随机性 | 主种子 20261111；验证／测试重复基线种子 20261112 |

每轮材质候选与校正前后查询实际几何条件、相机、灯光、颜色和渲染设置，要求与冻结快照相同。源／目标球体二值覆盖差异最多 32 像素，再对共同不透明区域腐蚀两像素以排除抗锯齿边缘；有效区域必须超过 1,000 像素。该阈值在比较训练结果之前确定。

预检先发现 Maya 的 `deviceAspectRatio` 未随分辨率更新，造成椭圆；明确设置两端共同的 1:1 条件后重跑。独立发光色参考的内部 MAE 约 `2.73e-7`，漫反射灰参考约 `1.40e-4`，覆盖差异 4 像素。默认 UV 与命名 UV／静态矩阵另有[实际采样对照](editable-materialx-20261111.md#c2rgba坐标与便携资源)。这些是场景／资源核对，不是通过调目标灯光压低材质误差。

共保留 424 张源图（128 份 beauty／底色／粗糙度，另 40 份重复 beauty）、1,152 张目标候选图、80 张校正／重复基线图。实际目标整批资源预检 128 / 128 通过，未使用缺失图、许可水印或占位图补数据。

## 标签、模型与训练

基础目标来自公开 `materialx.convert`，字典版本为 Maya 0.4.0。每个样本保存基础结果及粗糙度 `±0.12`、反射权重 `±0.25` 的 3×3 联合搜索；基础方案加八个候选，反射限制为 0–1，粗糙度限制为 0.05–0.95。每项候选都实际渲染。使用内部区域的线性 RGB `log1p` MSE；候选未改善至少 0.5% 则标签为零变化。128 项中 61 项选择了非基础候选，所有候选误差均保留。

模型为 2,770 个参数的共享 MLP `68 → 32 → 16 → 2`，ReLU／ReLU／Tanh。输入为实际底色 4×4 RGB 均值、实际粗糙度 4×4 均值，以及当前目标粗糙度、反射权重、IOR、金属度；空角保留零值。两个输出共享隐藏层与空间上下文，分别生成受限粗糙度和反射权重变化，不采用独立固定修正系数。

PyTorch `2.7.1+cpu`，Python `3.11.14`，CPU 四线程、确定性算法。CUDA 大包下载不稳定，按 ADR 使用同版本 CPU；没有把准备中的 GPU 环境写成实际训练设备。训练使用 AdamW，学习率 0.001、weight decay 0.001、batch=32，最多 800 epoch、验证 patience=100、种子 20261111。归一化统计只由训练集计算；验证标签用于 early stopping，测试标签不参与优化或模型选择。

实际运行 260 epoch，选用第 159 epoch（从零编号）的状态，验证归一化联合标签 MSE 为 `0.22995393`。训练计算耗时约 0.82 秒，不含框架启动、数据检查与渲染。训练曲线显示训练误差持续下降、验证误差随后回升，存在小数据过拟合迹象；这条标签损失曲线不能替代真实图像评估。

模型 ID：`iris.n1/20261111/57c86d864031ecc2`。保存真实 `weights.pt`、安全 `model.json`、完整 `loss.csv` 和数据清单；核心只加载有限、有界的 JSON，不反序列化 pickle。PyTorch 与纯 Python 推理的最大目标参数差为 `7.43e-8`。

## 独立真实渲染结果

40 个验证／测试提案经公开核心的字典与原子核验后，在 Maya 创建实际材质，逐项核对原生节点。相对基线只修改两个允许参数，节点连接和资源完全相同；灯光、相机、颜色与其他条件不变。每项再渲染不同种子的基础方案以估计重复误差。

| 集合 | 基础平均图像损失 | 校正平均图像损失 | 相对下降 | 按族重采样的 95% 区间 |
| --- | --- | --- | --- | --- |
| 验证（5 族／20 变体） | `6.65459e-6` | `6.38835e-6` | 4.00% | 0.12%～14.05% |
| 测试（5 族／20 变体） | `1.90481e-5` | `1.25172e-5` | 34.29% | −27.90%～64.29% |

重采样以材质族为单位、10,000 次，不能把同一族四个变体当成独立材质。验证集用于选模型，其结果不能充当独立泛化证明。测试平均改善超过此次重复基线的平均指标变化 `2.22e-7`，但族间差异很大、置信区间跨零，因此 `improvement_proven_in_this_experiment=false`。

| 测试材质 ID（各四个变体） | 平均误差相对下降 |
| --- | --- |
| `material_1126` | 12.30% |
| `material_0505` | −51.70%（退化） |
| `material_0164` | −26.63%（退化） |
| `material_0100` | 69.45% |
| `material_1551` | 2.19% |

总体均值主要受部分高误差材质改善影响，不能概括为每个材质更好。逐变体为 7 改善、9 退化、4 位于噪声范围；验证集对应 8／4／8。重复噪声只有两组种子，不能代替充分的随机性研究。统一三列预览固定展示每个测试族的 v2，包含退化样本；没有按最好结果挑图。

## 回退与边界

实际问题与处理：

| 问题 | 解决与证据 |
| --- | --- |
| Maya 分辨率已改、画幅比仍为默认宽屏 | 两端共同设定 1:1，重新渲染独立灰／发光参考并核对几何覆盖 |
| 原生坐标探测混用了 Maya API 1／2 方法名 | 按实际 API 2 的 `MFnMesh.createUVSet` 创建命名集，固定默认集后重跑真实 UV 对照 |
| 源能读取、目标不能解码的 WebP | 训练前替换该材质族，重做冻结清单、源渲染及整批目标资源预检 |
| CUDA 大包下载持续失败 | 保留安装日志，使用实际验证可用的 CPU PyTorch；如实记录设备 |
| 评估报告的 NumPy 布尔不能直接写 JSON | 转成原生 `bool` 后重跑同一评估；模型、数据、指标和测试划分不变 |
| 训练标签误差下降，测试部分材质却退化 | 保留所有原生渲染与负例；以隔离材质族图像评估为准，结论为尚未证明稳定改善 |

用实际训练权重完成八类公开 API 检查：缺模型、未知条件、未知源着色模型、缺空间样本、非法空间值、不同目标、连接驱动粗糙度，以及故意破坏第二项提案。全部保留完整基础图；缺模型为 `unavailable`，不适用输入或非法提案为 `rejected`，没有部分应用第一项。

本轮是离线研究入口，Maya GUI 默认仍进行字典转换。模型仅覆盖声明的精确方向、不透明 MULTI_GGX、单位球、均匀环境、64×64 空间采样及有界参数。条件身份和样本必须由调用者真实提供，不能把同一哈希填入任意场景就当成外观已验证。参数护栏也不表示护栏内每种材质均有训练证据。

尚未证明更多材质族、高分辨率、多视角／照明、法线、涂层、透明、连接驱动参数、拓扑与纹理修改的泛化。下一轮应先扩充隔离数据与场景条件、分析退化族，再设计新实验；不能继续用本轮测试集调参后重复声称独立测试。

## 复现与产物

产物根目录名为 `iris-relay-overnight-20261111-01`。数据、纹理副本、权重、EXR、场景与图表均在仓库外。以下命令中的 `$Run`、`$ExperimentPython`、`$Blender`、`$Mayapy` 由本机明确指定；宿主环境要求见 [Maya 文档](maya-materialx-import.md)。不把本机路径提交为配置。

独立 Python 3.11 环境安装 `_scripts/neural_requirements.txt`；CPU Torch 可先从官方 CPU wheel 索引安装。核心／宿主不依赖这些训练包。若从头生成清单和渲染：

```powershell
& $ExperimentPython _scripts/prepare_neural_experiment.py --documents "$Run/blender-final-03" --source "只读材质缓存.blend" --out "新实验清单目录" --exclude-resource-extension .webp --exclude-resource-extension .avif
& $Blender --background --factory-startup --disable-autoexec --python-exit-code 1 --python _scripts/render_blender_experiment.py -- --job "$Run/neural-experiment-06/job.json" --out "新的源渲染目录"
& $Mayapy _scripts/render_maya_experiment.py --job "$Run/neural-experiment-06/job.json" --documents "$Run/neural-source-02/documents" --out "新的目标渲染目录"
```

使用本轮保留的源／目标数据重跑训练和评估时：

```powershell
& $ExperimentPython _scripts/train_neural_experiment.py --job "$Run/neural-experiment-06/job.json" --source "$Run/neural-source-02" --target "$Run/neural-target-02" --gates "$Run/exchange-gates.json" --out "新的训练目录"
& $Mayapy _scripts/render_maya_predictions.py --job "$Run/neural-experiment-06/job.json" --predictions "$Run/neural-training-01/predictions" --baseline "$Run/neural-target-02" --out "新的校正渲染目录"
& $ExperimentPython _scripts/evaluate_neural_experiment.py --job "$Run/neural-experiment-06/job.json" --source "$Run/neural-source-02" --baseline "$Run/neural-target-02" --corrected "$Run/neural-corrected-01" --training "$Run/neural-training-01" --out "新的评估目录"
& $ExperimentPython _scripts/verify_neural_fallback.py --job "$Run/neural-experiment-06/job.json" --source "$Run/neural-source-02" --training "$Run/neural-training-01" --out "新的回退检查目录"
```

这些是独立阶段示例；使用新一轮结果时，后续参数须指向对应的新目录。每个输出目录必须不存在。`exchange-gates.json` 保存本轮 C1–C5 证据路径与哈希；新代码或新数据须重新验收，不能直接复制旧完成标记。

| 关键产物 | 路径（相对产物根） |
| --- | --- |
| 冻结条件与隔离清单 | `neural-experiment-06/job.json` |
| 实际源图与文档 | `neural-source-02/` |
| 资源预检、基线及全部候选 | `neural-target-02/` |
| 权重、数据、损失、40 份公开响应 | `neural-training-01/` |
| 原生校正材质、EXR 和重复基线 | `neural-corrected-01/` |
| 独立评估与全部负例 | `neural-evaluation-02/evaluation.json` |
| 统一三列对比与训练曲线 | `neural-evaluation-02/held-out-comparison.png`、`training-loss.png`、`training-loss.pdf` |
| 八类真实权重回退 | `neural-fallback-02/summary.json` |

模型 JSON SHA-256 为 `b7646a2f68b4ffe679e001059e03cf7e9731d637975c58d3b45f771027efe0a3`；权重为 `31113dfc4a85e01807ff3ad771d6708cc1dd2a98cec98fae3c6a216cefe677f5`；数据清单为 `ba13c74adb84f651864a8269b1dd0c18ad862cd1d88e3e52a79cf33ed2717c83`。源资源复核记录见 `source-integrity-final.json`。
