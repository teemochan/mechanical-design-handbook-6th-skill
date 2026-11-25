---
name: mechanical-design-handbook-6th
description: >-
  Token-efficient local knowledge base and retrieval tool for 《机械设计手册（第六版）》
  (5 volumes, 23 parts, 8,512 pages), Machine Design Databook (Lingaiah, 1,282 pages),
  and the Surface Roughness supplement. Use when designing, selecting, or verifying
  mechanical components, tolerances & fits, geometric tolerances, surface roughness,
  engineering materials, fasteners/threads/pre-tightening torque, shafts/couplings/brakes,
  bearings, lubrication/seals, springs, gears, belts/chains, reducers, vibration,
  frames, or hydraulic/pneumatic systems, or when looking up GB/JB/ISO mechanical standards.
---

# Mechanical Design Handbook (6th Ed.) — Token-Efficient Agent Skill

本技能连接由用户本地构建的《机械设计手册（第六版）》结构化知识库（支持全 5 卷 23 篇 8,512 页、英文《Machine Design Databook》27 章 1,282 页及《表面粗糙度》专题 14 页扫描件）。

**核心省 Token 工作流（Progressive Disclosure）**：绝不直接读取大段 PDF 或全卷目录；始终遵循 **“1. 检索定位（<400 tokens）→ 2. 按页精读清洗文本 → 3. 遇到复杂公式/密集大表/曲线图时按需单页 PNG 视觉核验”** 三步法。

## 前置检查

使用前确认本 Skill 目录下已存在 `handbook.db`。若尚未生成，请先提示用户运行本地建库命令（仅需约 35 秒，需用户自备本地合法 PDF）：

```bash
python ./scripts/build_kb.py --pdf-dir "<本地PDF目录>"
```

## 快捷调用入口

`handbook.db` 生成后，**检索与正文读取零外部依赖（仅需 Python 3.10+ 标准库）**：

```bash
# Windows (PowerShell / CMD)
./handbook.bat <command> [args...]

# macOS / Linux
./handbook.sh <command> [args...]

# 或直接运行 Python
python ./scripts/handbook_cli.py <command> [args...]
```

> 使用 `render` 高清渲染单页 PNG 时，需安装可选依赖 `pypdfium2`、`pillow`，并配置环境变量 `HANDBOOK_PDF_DIR` 指向本地 PDF 目录（或传 `--pdf-dir`）。

## 卷号路由速查

| `vol` | 覆盖核心领域 |
|:---|:---|
| `v1` | 第1~5篇：基础资料与力学公式、工艺性/热处理/表面技术、制图/极限与配合/几何公差/表面结构、工程材料、机构、结构设计准则 |
| `v2` | 第6~10篇：螺纹连接与拧紧力矩、铆/销/键/花键/过盈/胀紧/粘接、轴/联轴器/离合器/**制动器**、滑动/滚动/直线轴承、起重运输零部件、操作件与管件 |
| `v3` | 第11~15篇：润滑与密封件、**弹簧**（螺旋/碟形/板簧/橡胶/空气弹簧等21章）、螺旋与摩擦轮传动、带/链传动、齿轮传动 |
| `v4` | 第16~20篇：多点啮合柔性传动、减速器与变速器、电机/电器/电动（液）推杆升降机、机械振动控制与临界转速、机架/梁/柱/桁架/框架设计 |
| `v5` | 第21~23篇：液压传动（泵/马达/缸/阀/回路）、液压伺服与电液比例控制、气压传动与气动元件 |
| `en` | 英文《Machine Design Databook》：27 章（材料/静动应力/应力集中/凸轮/压力容器/公差/紧固件/离合器制动器/弹簧/轴承摩擦学/弹性力学） |
| `ra` | 《表面粗糙度》专题（14 页纯扫描件，需配置原 PDF 后用 `render ra <pages>` 读图） |

> 本地建库后会自动生成总路由 `INDEX.md` 及分卷细粒度目录 `toc/{v1..v5,en}_toc.md`。

## 标准三步检索与防错协议

### Step 1：毫秒级定位

```bash
# 1) 查细粒度目录（最省 Token，精确到小节与 PDF 页码范围）
python scripts/handbook_cli.py toc 拧紧力矩 --vol v2

# 2) 直查工程数据表（按表名关键词或表号）
python scripts/handbook_cli.py table 梯形螺纹 旋合长度

# 3) 按国标/行标/ISO 标准号反查
python scripts/handbook_cli.py std GB/T 3098

# 4) 跨全库 BM25 全文检索（支持中英文混合词组，默认仅返 Top 8 紧凑摘要）
python scripts/handbook_cli.py search 圆柱螺旋压缩弹簧 刚度 --vol v3 --limit 6
python scripts/handbook_cli.py search "stress concentration" fillet --vol en --limit 5
```

### Step 2：按需精读清洗后的紧凑文本（每次 1～3 页）

```bash
python scripts/handbook_cli.py read v2 book:6-70
python scripts/handbook_cli.py read v2 93-94 --max-chars 4500
```

### Step 3：复杂公式 / 密集大表 / 曲线图的高精度视觉核验

**防幻觉关键规则**：当 `read` 输出提示 `[视觉核验建议]`，或涉及精确公差数值、多栏大表对齐、公式上下标/分数、线图取值、结构简图或纯扫描源 `ra` 时，将对应 1～2 页渲染为高清 PNG，再调用视觉能力（如 `read_file`）直接目视核验原版图文：

```bash
python scripts/handbook_cli.py render v2 93 --dpi 170
python scripts/handbook_cli.py render v1 300 --dpi 220 --clip bottom
```
