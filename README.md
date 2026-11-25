# Mechanical Design Handbook (6th Ed.) — AI Agent Skill & Token-Efficient KB Builder

> ⚖️ **版权合规声明**：本开源仓库遵循 MIT 协议，**仅提供纯原创的自动化索引构建脚本、极省 Token 的本地检索引擎与标准 AI Agent Skill 规范，不包含任何受版权保护的图书 PDF、正文文本、目录数据或预构建数据库**。请使用您个人合法拥有的正版电子资料在本地运行构建脚本生成私有索引库。

一套面向机械工程研发场景的 **AI Agent Skill + 分层渐进式本地知识库构建工具**。针对《机械设计手册（第六版）》（成大先主编，第 1～5 卷，共 8,512 页）及《Machine Design Databook》（Lingaiah，1,282 页）等超大型工程工具书，通过 **35 秒本地离线建库 + 四层渐进式按需检索 + 复杂公式/大表按需高清视觉渲染**，让 AI Agent 毫秒级查表、查标准、查公式，同时较传统读 PDF / 常规 RAG **节省 95% 以上 Token**。

---

## 一、这个 Skill 的用途

1. **攻克大型机械工具书的“高 Token 消耗 + 查表幻觉”痛点**：
   - 传统方式直接上传数百 MB PDF 或粗粒度切片 RAG，往往一次查询耗费上万 Token，且方正书版 PDF 普遍存在全角字母数字、私有区小数点（`􀆰`）、断续表号（如 `表 6-1- 11`）和中文表头撑开空格，导致直接检索命中率极低。
   - 本 Skill 先在本地完成深度字符清洗与多维结构化入库，查询时按需只拉取最相关的 1～2 页，并在涉及密集公差表或复杂上下标公式时自动提示按需渲染单页高清 PNG 供多模态模型精准读图。
2. **覆盖机械设计全流程核心场景**：
   - 公差与配合（极限与配合、几何公差、表面粗糙度）、工程材料力学性能、螺纹连接与拧紧力矩、轴/联轴器/离合器/制动器、滑动/滚动/直线轴承、润滑密封、各类弹簧（螺旋/碟形/板簧/橡胶/空气弹簧）、齿轮/带/链传动、减速器、机械振动、机架结构以及液压/气压传动等。
3. **支持中英双语工程对照与国标/行标/ISO 标准号反查**：
   - 自动提取全书所有标准引用（`GB`、`GB/T`、`JB/T`、`JB/ZQ`、`ISO`、`DIN`、`ASTM`、`ASME` 等），支持按标准号一键反查对应手册章节与页码。
4. **跨平台 AI Agent 即插即用**：
   - 符合通用 `SKILL.md` 规范，可直接挂载到 **Antigravity**、**Claude Code**、**Cursor**、**Codex** 等主流 AI Agent。

---

## 二、Skill 包含的内容

### 1. 仓库文件清单（100% 纯原创开源代码）

```text
mechanical-design-handbook-6th-skill/
├── SKILL.md                  # 标准 AI Agent Skill 描述文件（Skill 名称：mechanical-design-handbook-6th）
├── scripts/
│   ├── build_kb.py           # 高速本地建库引擎：基于 pypdfium2，约 35 秒解析近万页 PDF 生成全部索引
│   └── handbook_cli.py       # 极致省 Token 的统一检索 / 精读 / 按需单页 PNG 渲染 CLI
├── handbook.bat              # Windows 快捷入口（自动优先 .venv，无 .venv 时回退系统 Python）
├── handbook.sh               # macOS / Linux 快捷入口
├── pyproject.toml            # Python 项目配置（检索零外部依赖；建库/渲染仅需 pypdfium2 + pillow）
├── LICENSE                   # MIT 开源许可证
└── README.md                 # 本说明文档
```

### 2. 本地一键构建后的四层渐进式架构

运行 `build_kb.py` 后，将在本地自动生成以下结构化资产（已默认加入 `.gitignore`，仅存本地）：

| 层级 | 本地生成产物 | 作用与实测指标（完整 5 卷 + Databook + 专题） |
|:---|:---|:---|
| **Level 0 总导航** | `INDEX.md` (~11 KB) | 全 23 篇 + 150 余章 + 英文 27 章速查路由表，Agent 一眼锁定目标卷号 |
| **Level 1 细粒度目录** | `toc/{v1..v5,en}_toc.md` | 自动解析原书多级目录页（约 **4,902 条** 篇/章/节/小节），100% 精确映射书内页码与 PDF 页码区间 |
| **Level 2 单文件检索引擎** | `handbook.db` (~75 MB) | SQLite + FTS5 中英混合倒排索引（支持任意长度中文词组精确短语 + BM25 排序），内置约 **6,942 张工程数据表索引**与 **3,066 项独立标准号索引** |
| **Level 3 双模态按需提取** | `handbook_cli.py` | 文本定位仅返 Top-K 紧凑摘要（~300~600 tokens/次）；遇复杂公式/密集大表/纯扫描页时，0.1 秒按需导出单页高清 PNG 存入 `rendered/` 供多模态视觉核验 |

### 3. 核心清洗与索引黑科技

- **方正书版专有字符修复**：将方正私有区小数点、特殊连字符（`⁃`）、全角数字字母统一标准化为半角，利用零宽断言一次性修复 `表 6-1- 11` → `表 6-1-11`、`3.5. 1` → `3.5.1`，折叠表头冗余全角空格，单页文本 Token 压缩约 **30%～40%**。
- **零第三方分词依赖的 CJK/EN 混合 FTS5**：通过汉字字间切分 + 英文/标准号整词保留策略，仅靠 Python 标准库内置 `sqlite3` 即可实现任意长度中文短语与英文术语的毫秒级 BM25 检索。
- **书内页码 ↔ PDF 页码精准双校验映射**：自动扫描全书页眉书页号并结合 PDF 书签校准偏移，支持直接通过 `read v2 book:6-70` 按原书页码精准直读。

---

## 三、详细用法

### Step 1：克隆仓库并安装建库/渲染依赖

```bash
git clone https://github.com/teemochan/mechanical-design-handbook-6th-skill.git
cd mechanical-design-handbook-6th-skill

# 使用 uv 安装可选 PDF 解析与渲染依赖（推荐，约 11 MB）
uv sync --extra pdf
# 或使用 pip:
pip install pypdfium2 pillow
```

### Step 2：使用本地合法拥有的 PDF 一键构建知识库（约 35 秒）

将您本地拥有的原版 PDF 放在任意目录（支持仅放部分分卷，缺失分卷会自动跳过）：

| `vol` | 期望文件名（可在 `scripts/build_kb.py` 的 `SOURCES` 中按需调整） |
|:---|:---|
| `v1`～`v5` | `机械设计手册 第六版 第1卷.pdf` ～ `机械设计手册 第六版 第5卷.pdf` |
| `en` | `Machine Design Databook 机械设计手册.pdf`（可选） |
| `ra` | `机械设计手册-表面粗糙度.pdf`（可选，纯扫描件） |

运行建库脚本：

```bash
python scripts/build_kb.py --pdf-dir "/path/to/your/pdfs"
```

> 建库完成后，`overview`、`toc`、`search`、`table`、`std`、`read` 六大核心命令**不再依赖任何第三方 Python 包**，仅靠 Python 3.10+ 标准库即可离线秒查。

### Step 3：挂载到 AI Agent 自动调用

- **Antigravity**：
  - 将仓库直接克隆到工作区 `.agents/skills/mechanical-design-handbook-6th/` 或全局 `~/.gemini/config/skills/mechanical-design-handbook-6th/`（完成 Step 2 本地建库后即自动发现生效）。
- **Claude Code / Cursor / Codex 等**：
  - 将本目录作为 Skill 注册（Skill 标识：`mechanical-design-handbook-6th`），或在项目 `CLAUDE.md` / `AGENTS.md` 中指明本仓库的 `SKILL.md` 与 `scripts/handbook_cli.py` 路径。

挂载后可直接用自然语言向 Agent 提问，例如：
- *“查一下《机械设计手册（第六版）》中螺栓拧紧力矩计算公式和预紧力控制方法，并标明出处。”*
- *“查一下梯形螺纹旋合长度表和常用工作表面粗糙度 Ra 选用表。”*
- *“查一下 GB/T 3098 在机械设计手册第六版中的相关引用章节。”*

### Step 4：CLI 全命令速查（也可手动或通过脚本调用）

```bash
# 1. 查看知识库总览（卷目、23 篇及英文 27 章路由）
./handbook.bat overview        # macOS/Linux 用 ./handbook.sh

# 2. 查细粒度目录（最省 Token）
./handbook.bat toc 拧紧力矩 --vol v2

# 3. 直查工程数据表（支持表名关键词或表号）
./handbook.bat table 梯形螺纹 旋合长度
./handbook.bat table 6-1-66 --vol v2

# 4. 按国标/行标/ISO 标准号反查
./handbook.bat std GB/T 3098

# 5. 跨全库 BM25 全文检索（支持中英文，可加 --json 输出结构化结果）
./handbook.bat search 圆柱螺旋压缩弹簧 刚度 --vol v3 --limit 5
./handbook.bat search "stress concentration" fillet --vol en --limit 4

# 6. 按书内页码或 PDF 页码精读清洗后正文
./handbook.bat read v2 book:6-70
./handbook.bat read v2 93-94 --max-chars 4500

# 7. 复杂公式/密集大表/扫描件按需高清渲染单页 PNG（支持 --clip top|bottom|left|right）
# 需先设置 HANDBOOK_PDF_DIR 环境变量或传入 --pdf-dir
./handbook.bat render v2 93 --dpi 170 --pdf-dir "/path/to/your/pdfs"
./handbook.bat render v1 300 --dpi 220 --clip bottom --pdf-dir "/path/to/your/pdfs"
```

---

## 四、扩展到其他工程手册或企业标准

本架构同样适用于其他大型机械/电气/铁路/国标 PDF 工具书：只需修改 `scripts/build_kb.py` 顶部的 `SOURCES` 列表，即可在几十秒内将任意多卷 PDF 整理为同款省 Token 的 AI Agent 本地知识库与 Skill。

## License

[MIT](./LICENSE)
