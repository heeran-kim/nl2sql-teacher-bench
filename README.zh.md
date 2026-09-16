[English](README.md) | [한국어](README.ko.md) | [中文](README.zh.md)

# nl2sql-teacher-bench

一个轻量级、与模型无关的基准测试工具,用于比较任意由 [Ollama](https://ollama.com)
提供服务的大语言模型在**零样本自然语言转 SQL 生成质量**上的表现。

## 这是做什么用的

如果你正在构建一个 NL2SQL 微调流水线,通常需要一个 "teacher" 模型 --
用它来为训练集生成 SQL 标签(通常是因为你没有足够的人工标注数据)。
应该用哪个模型来做这件事?

这和"应该部署哪个模型"是一个不同的问题,值得单独做基准测试:

- Teacher 模型只需离线运行一次来生成训练数据,推理成本和延迟基本不重要。
- 唯一重要的是它在你的 schema 和你的查询风格上的原始生成质量。
- 最好的 teacher 不一定就是你最终要微调并上线的模型 -- 只要能跑起来,
  它可以任意大、任意慢。

这个工具会让一组候选模型在一份留出的 (prompt, reference SQL) 测试集上
运行,并汇报每个模型的得分,这样你就可以基于证据而不是直觉来选择
teacher 模型。

它**不**做微调、提示词工程,也不做 agentic / 多步 SQL 生成 -- 它的范围
是刻意收窄的:输入一个 prompt,输出一条 SQL 查询,再与参考答案比较打分。

## 环境要求

- Python 3.9 及以上
- 已安装并运行 [Ollama](https://ollama.com)(本地运行,或通过
  `--ollama-host` 访问远程实例)
- 想要比较的模型已经在 Ollama 中 pull 好

```bash
pip install -r requirements.txt
```

## 快速开始

```bash
ollama pull qwen3:14b
ollama pull llama3.1:8b

python bench.py \
  --models qwen3:14b llama3.1:8b \
  --data data/example.jsonl \
  --schema data/example_schema.sql \
  --dialect mysql
```

这条命令会根据内置示例 schema(一个通用的 customers/orders schema,
与任何实际项目都无关)创建一个内存中的 SQLite 数据库,让两个模型在
示例问题上运行,对每个回答打分(包括**执行准确率(execution accuracy)**,
见下文),并写出 `results.md`。

`--schema` 是可选的。不提供它,依然可以得到基于文本的评分(精确匹配、
是否可解析、表/列重合度)-- 只需去掉这个参数,以及下面会介绍的
`{{schema}}` 占位符即可。

## 使用你自己的测试数据

### 测试集格式

JSONL 格式,每行一个样例,两个必填字段:

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `prompt` | 字符串 | 发送给模型的完整指令 -- 系统上下文、schema、问题、格式要求、few-shot 示例,任何你想要的内容。会作为一条 user 消息原样发送。 |
| `reference_sql` | 字符串 | 该 prompt 对应的标准(gold)SQL 查询。 |

```json
{"prompt": "You are a SQL generator...\n\nSchema:\n{{schema}}\n\nQuestion: ...", "reference_sql": "SELECT ..."}
```

### Schema:只维护一份,而不是两份

如果每个样例的 prompt 都需要同一份 schema,不要把它手动粘贴到每一行里 --
一旦 schema 发生变化,就有出现不一致(drift)的风险。应该在 prompt 中
放入字面量占位符 `{{schema}}`,然后通过 `--schema path/to/schema.sql`
只传一次真正的 schema。在把内容发给模型之前,`bench.py` 会把
`{{schema}}` 替换成该文件的内容,这样你的 schema 就只存在于一个地方。

`--schema` 同时做两件事:

1. 填充每个 prompt 中的 `{{schema}}`(如果不需要 -- 比如每个样例的
   schema 不同,或者根本不需要 schema -- 可以跳过这个参数,直接把
   schema 写进 prompt 里)。
2. 用它构建一个内存 SQLite 数据库并填充随机数据,从而启用下一节介绍的
   执行准确率评分。

schema 文件本身就是标准的 `CREATE TABLE` 语句,使用你通过 `--dialect`
指定的方言:

```sql
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name VARCHAR(100),
    email VARCHAR(100)
);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    status VARCHAR(20),
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);
```

支持单列的 `PRIMARY KEY` 和 `FOREIGN KEY`,无论是内联声明还是表级约束都
可以识别。复合主键/外键不做特殊处理 -- 用于生成数据时只使用复合键的
第一列。

### 运行

```bash
python bench.py \
  --models qwen3:14b sqlcoder:7b \
  --data path/to/your_testset.jsonl \
  --schema path/to/schema.sql \
  --dialect postgres
```

### 使用自定义填充数据(`--seed-script`)

一旦问题过滤的是某个特定字面量(已知 ID、订单编号),随机数据就会失效
-- 两个查询都返回空结果,于是被判定为"匹配"。`--seed-script
path/to/script.py` 会用你自己的逻辑替换随机填充:`--schema` 仍然用来
创建表,但 `bench.py` 会调用你脚本中的 `seed(conn)` 函数,而不是生成
随机行。

```python
# my_seed.py
def seed(conn):
    conn.execute("INSERT INTO customers (id, name) VALUES (?, ?)", (42, "Ada Lovelace"))
    conn.execute("INSERT INTO orders (id, customer_id, status) VALUES (?, ?, ?)", (1, 42, "completed"))
```

```bash
python bench.py --models qwen3:14b --data your_testset.jsonl --schema schema.sql --seed-script my_seed.py
```

在关键之处加入"陷阱"数据(比如一条不应该被选中的更新记录),这样一个
错误的查询才能真正产生错误的结果,而不是碰巧匹配上一个空结果集。

用 `--dialect` 匹配你的 schema 和参考查询所使用的 SQL 方言(任意
[sqlglot 方言名称](https://sqlglot.com/sqlglot/dialects/dialect.html) --
例如 `mysql`、`postgres`、`sqlite`、`snowflake`、`bigquery`;默认使用
sqlglot 的通用方言)。这只影响解析/评分/DDL 转换,不影响发送给模型的
内容。

## 评测指标

对每个样例,模型的原始输出会先经过 `extract_sql()` 处理(去除 markdown
代码块以及一些模型在给出真正查询之前附加的推理性文字),然后与参考
答案比较打分。

**始终计算的指标:**

- **valid** -- 生成的 SQL 在 `--dialect` 下是否至少能被解析?
- **table_match** -- 引用的表集合是否与参考答案一致(不考虑顺序)?
- **col_overlap** -- 参考答案 SELECT 列表中的列,有多少比例也出现在
  生成查询的 SELECT 列表中。
- **exact_match** -- 去除首尾空白后是否逐字节完全一致。这是严格指标:
  格式、列顺序或别名不同的逻辑等价 SQL 也会被判为不匹配。

**仅当提供 `--schema` 时才计算:**

- **execution_match** -- 将参考查询和生成查询都在同一个已填充数据的
  数据库上执行,并比较结果集(按行的多重集合比较,不考虑顺序)。这是
  学术界 NL2SQL 基准测试(Spider、BIRD)采用的标准评测方法,因为它能
  正确地认可那些写法不同但语义等价的查询 -- 这正是 `exact_match` 会
  判错的情况。
- **generated_executable** / **reference_executable** -- 无论结果是否
  匹配,查询本身是否能在没有数据库错误的情况下执行成功?

一旦有了 schema,应该把 `execution_match` 当作主要信号,把 `exact_match`
当作一个严格的下限,而不是真实的全貌。

### 执行评分的局限性

- **数据是随机的,不是真实的。** 填充的值只是类型匹配的随机噪声,并非
  真实数据的副本 -- 但有一个例外:声明为 `ENUM(...)` 或带有
  `CHECK (col IN (...))` 约束的列,会从该声明的取值集合中采样,因此像
  `WHERE status = 'COMPLETED'` 这样的过滤条件是有可能真正匹配上的。
  如果没有声明取值集合,同样的过滤条件在随机文本上通常匹配不到任何行,
  于是参考查询和生成查询都返回空结果集 -- 这种情况下"匹配"是平凡的,
  并没有真正测试到那个过滤条件。任何过滤条件用到随机生成不太可能碰上的
  特定字面量(已知 ID、日期范围)都会有同样的问题。如果你的 schema 没有
  声明有效取值,给你本地的 schema 副本加上 `CHECK` 约束即可(这只用于
  构建合成测试数据库,不会影响你的真实数据库,是安全的);如果问题出在
  对特定字面量的过滤上,请使用下文的 `--seed-script`,填充问题真正
  需要的那些行,而不是随机数据。
- **仅支持 SELECT。** 非 SELECT 语句不会在已填充数据的数据库上执行
  (这是有意设计的 -- 避免某一次糟糕的模型输出污染同一次运行中其他
  样例所依赖的数据)。
- **无论你的 schema 是什么方言,执行目标始终是 SQLite。** 标准的
  DDL/DML 通过 sqlglot 基本都能转换成功,但方言特有程度较高的 SQL
  (少见的窗口函数、JSON 运算符、日期运算等)可能无法完美转换。如果
  某个结果看起来不对,在直接采信这个数字之前,先查看 `results.md`
  中的逐条详情以及 `generated_error` / reference 错误信息。

## 候选模型

值得作为 NL2SQL teacher 进行基准测试的一组起始模型,分为通用大模型和
NL2SQL 专用模型两类。以下 pull 命令均基于 Ollama;对于没有官方 Ollama
库标签的模型,Ollama 可以直接从 Hugging Face 上的任意 GGUF 仓库拉取,
命令为 `ollama pull hf.co/<repo>:<quant>` -- 不需要手动创建
`Modelfile`。如果列出的 quant 标签返回 404,请在对应 Hugging Face
仓库的文件列表中查找准确的名称(不同社区量化发布者使用的标签命名并不
统一)。

### 通用模型

| 模型 | 规模(激活参数) | Pull 命令 |
| --- | --- | --- |
| Qwen3-14B | 148 亿,稠密(dense) | `ollama pull qwen3:14b` |
| Qwen3-30B-A3B | 总参数 300 亿 / 激活 30 亿(MoE) | `ollama pull qwen3:30b-a3b` |
| Qwen3-Coder-30B-A3B | 300 亿 / 激活 33 亿(MoE) | `ollama pull qwen3-coder:30b-a3b` |
| Granite 4.0 H-Small | 320 亿 / 激活 90 亿(MoE) | `ollama pull ibm/granite4:small-h` |

### NL2SQL 专用模型

| 模型 | 规模(激活参数) | Pull 命令 |
| --- | --- | --- |
| OmniSQL-7B | 70 亿,稠密 | `ollama pull hf.co/mradermacher/OmniSQL-7B-GGUF:Q4_K_M` |
| OmniSQL-14B | 140 亿,稠密 | `ollama pull hf.co/mradermacher/OmniSQL-14B-GGUF:Q4_K_M` |
| OmniSQL-32B | 330 亿,稠密 | `ollama pull hf.co/mradermacher/OmniSQL-32B-i1-GGUF:Q4_K_M` |
| SQLCoder-7B-2(defog,基于 CodeLlama) | 70 亿,稠密 | `ollama pull pxlksr/defog_sqlcoder-7b-2:Q4_K_M` |
| Llama-3-SQLCoder-8B(defog) | 80 亿,稠密 | `ollama pull hf.co/QuantFactory/llama-3-sqlcoder-8b-GGUF:Q4_K_M` |
| SQLCoder2-15B(defog,基于 StarCoder) | 150 亿,稠密 | `ollama pull hf.co/TheBloke/sqlcoder2-GGUF:Q4_K_M` |
| Arctic-Text2SQL-R1-7B(Snowflake) | 70 亿,稠密 | `ollama pull a-kore/Arctic-Text2SQL-R1-7B` |
| XiYanSQL-QwenCoder-7B | 70 亿,稠密 | `ollama pull hf.co/mradermacher/XiYanSQL-QwenCoder-7B-2504-GGUF:Q4_K_M` |
| XiYanSQL-QwenCoder-14B | 140 亿,稠密 | `ollama pull hf.co/visualbruno/XiYanSQL-QwenCoder-14B-2502-Q5_K_M-GGUF` |
| XiYanSQL-QwenCoder-32B | 320 亿,稠密 | `ollama pull hf.co/mradermacher/XiYanSQL-QwenCoder-32B-2504-GGUF:Q4_K_M` |

这些只是起点,不是固定名单 -- 任何 Ollama 能运行的模型都是有效候选。
参见下文"添加候选模型"。另外,Arctic-Text2SQL-R1 和 XiYanSQL-QwenCoder
并不总是公开发布所有尺寸的权重,请以各自 Hugging Face 页面上当前公开的
版本为准。

一个值得注意的版本陷阱:官方 Ollama 库中的 `sqlcoder:7b` 和
`sqlcoder:15b` 标签,是 defog **最原始、较旧**的 SQLCoder 检查点
(分别基于 Mistral-7B,以及最早发布的 StarCoder-15B 版本)-- 而不是
上表中列出的改进版 `sqlcoder-7b-2`(基于 CodeLlama)或 `sqlcoder2`,
后两者只以社区发布的标签/GGUF 形式存在。简短的官方标签名并不意味着
它就是最新版本,使用前务必确认某个标签实际指向的是哪一个检查点。

## 如何评估你自己的硬件

一个模型能否实际运行,取决于两个相互独立的因素 -- 不要仅凭总参数量就
排除某个模型:

- **内存占用**(必须能放进 VRAM + 内存的总和里):与所选量化精度下的
  *总*参数量成正比。如果模型无法完全放进显存,Ollama 会自动把部分层
  拆分到系统内存中运行 -- 仍然能跑,只是落在 CPU 上的那部分会变慢。
- **每 token 的计算成本**(决定速度):与每次前向传播时的*激活*参数量
  成正比。稠密(dense)模型每个 token 都会激活 100% 的参数。像
  "30B-A3B"(总参数 300 亿,激活 30 亿)这样命名的
  Mixture-of-Experts(MoE)模型只激活一小部分参数,因此可以在远低于
  同等规模稠密模型的计算成本下,承载多得多的总容量。

一个大致的经验法则:量化后模型的体积(GB)约等于 `总参数量(十亿) x
每权重比特数 / 8`(例如 140 亿参数在 4-bit 量化下大约是 7GB,再加上
一些额外开销)。如果这个数字明显小于你的 VRAM + 内存总和,模型就能
跑起来;至于跑得*快不快*,则取决于有多少部分从显存溢出到了系统内存,
以及每个 token 实际激活了多少参数。

由于 teacher 角色只需离线运行一次,在权衡硬件取舍时应优先考虑生成
*质量*而不是速度 -- 一个慢一点但更好的 teacher 通常是值得等待的。

## 添加候选模型

任何 Ollama 能提供服务的标签都可以使用:

```bash
# 官方 Ollama 库
ollama pull <tag>

# Hugging Face 上任意 GGUF 仓库,无需手动创建 Modelfile
ollama pull hf.co/<user>/<repo>:<quant>
```

然后把这个标签加入 `--models` 参数即可。

## 输出结果

`bench.py` 会写出一份 Markdown 报告(默认为 `results.md`,可通过
`--output` 修改),内容包括:

- 一张汇总表(如果提供了 `--schema`,会包含执行匹配率和可执行率;
  精确匹配、是否可解析、表匹配、平均列重合度、平均延迟则始终包含)--
  涵盖测试的所有模型。
- 一张逐条详情表,把每个模型生成的 SQL 与参考答案并排展示,方便人工
  复核那些"差一点"的结果。

运行过程中还会实时向标准输出打印每条样例的进度以及最终汇总。

## 仓库结构

```
bench.py           命令行入口
metrics.py         基于文本的评分(是否可解析 / 表匹配 / 列重合度 / 精确匹配)
db_builder.py       schema 解析 + 内存 SQLite 数据库填充
execution.py        基于已填充数据库的执行准确率评分
ollama_client.py    极简的 Ollama /api/chat 客户端
data/example.jsonl        演示用测试集(通用 schema,与任何实际项目无关)
data/example_schema.sql   上述演示用的 schema
```

`data/` 目录下除上述文件外的其他内容默认都会被 git 忽略 -- 那里正是
存放你自己的(可能是私有的)schema 和测试集的地方。
