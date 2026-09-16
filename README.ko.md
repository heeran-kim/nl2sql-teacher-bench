[English](README.md) | [한국어](README.ko.md) | [中文](README.zh.md)

# nl2sql-teacher-bench

[Ollama](https://ollama.com)로 서빙되는 어떤 LLM이든 **제로샷 자연어-to-SQL 생성 품질**을
비교할 수 있는, 모델에 종속되지 않는 작은 벤치마크 도구입니다.

## 용도

NL2SQL 파인튜닝 파이프라인을 만들고 있다면, 보통 "teacher" 모델이 필요합니다 --
직접 라벨링한 데이터가 충분하지 않을 때, 학습 데이터의 SQL 라벨을 생성해 줄
모델입니다. 어떤 모델을 teacher로 써야 할까요?

이건 "어떤 모델을 실제로 배포할지"와는 다른 질문이고, 따로 벤치마크할 가치가
있습니다:

- Teacher는 학습 데이터를 만들기 위해 오프라인에서 딱 한 번만 돌아갑니다.
  추론 비용이나 지연 시간은 거의 중요하지 않습니다.
- 당신의 스키마와 쿼리 스타일에 대한 순수한 생성 품질만이 중요합니다.
- 최적의 teacher가 곧 파인튜닝해서 배포할 모델일 필요는 없습니다 --
  실행만 가능하다면 얼마든지 크거나 느려도 상관없습니다.

이 도구는 후보 모델들을 (prompt, reference SQL) 쌍으로 이루어진 홀드아웃
테스트셋에 대해 실행하고, 각 모델의 점수를 리포트로 만들어 줍니다. 감이 아니라
근거를 가지고 teacher를 고를 수 있게 해줍니다.

이 도구는 파인튜닝, 프롬프트 엔지니어링, 에이전틱/멀티스텝 SQL 생성은
**하지 않습니다** -- 의도적으로 범위를 좁혔습니다: 프롬프트 하나 입력하면
SQL 쿼리 하나가 나오고, 그걸 reference와 비교해서 점수를 매기는 것뿐입니다.

## 요구 사항

- Python 3.9 이상
- [Ollama](https://ollama.com)가 설치되어 실행 중이어야 함 (로컬이든, `--ollama-host`로
  접근 가능한 원격이든)
- 비교하려는 모델들이 Ollama에 이미 pull 되어 있어야 함

```bash
pip install -r requirements.txt
```

## 빠른 시작

```bash
ollama pull qwen3:14b
ollama pull llama3.1:8b

python bench.py \
  --models qwen3:14b llama3.1:8b \
  --data data/example.jsonl \
  --schema data/example_schema.sql \
  --dialect mysql
```

이 명령은 예제 스키마(어떤 실제 프로젝트와도 관련 없는 일반적인
customers/orders 스키마)로부터 인메모리 SQLite 데이터베이스를 만들고,
두 모델을 예제 질문들에 대해 실행한 뒤, 모든 답변을 채점하고
(**실행 정확도(execution accuracy)** 포함 -- 아래 참고), `results.md`를
작성합니다.

`--schema`는 선택 사항입니다. 없어도 텍스트 기반 채점(정확 일치, 파싱 가능
여부, 테이블/컬럼 겹침)은 그대로 됩니다 -- 그냥 이 플래그와 다음에 설명할
`{{schema}}` 플레이스홀더를 빼면 됩니다.

## 자신의 테스트 데이터 사용하기

### 테스트셋 형식

JSONL 형식, 한 줄에 예제 하나씩, 필수 필드 두 개:

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `prompt` | 문자열 | 모델에 보내는 완전한 지시문 -- 시스템 컨텍스트, 스키마, 질문, 포맷 지시, few-shot 예제 등 원하는 대로. 하나의 user 메시지로 그대로 전송됩니다. |
| `reference_sql` | 문자열 | 해당 prompt에 대한 정답(gold) SQL 쿼리. |

```json
{"prompt": "You are a SQL generator...\n\nSchema:\n{{schema}}\n\nQuestion: ...", "reference_sql": "SELECT ..."}
```

### 스키마: 두 곳이 아니라 한 곳에

모든 예제의 prompt가 같은 스키마를 필요로 한다면, 그걸 매 줄마다 손으로
붙여넣지 마세요 -- 스키마가 바뀌는 순간 어긋날(drift) 위험이 있습니다.
대신 prompt 안에 `{{schema}}`라는 리터럴 토큰을 넣고, 실제 스키마는
`--schema path/to/schema.sql`로 한 번만 전달하세요. `bench.py`는 모델에
무엇이든 보내기 전에 `{{schema}}`를 그 파일의 내용으로 치환합니다. 그래서
스키마가 존재하는 곳은 정확히 한 곳뿐입니다.

`--schema`는 동시에 두 가지 역할을 합니다:

1. 모든 prompt의 `{{schema}}`를 채웁니다 (필요 없다면 -- 예제마다 스키마가
   다르거나, 아예 스키마가 필요 없는 경우 -- 이걸 생략하고 prompt에 직접
   스키마를 써넣으면 됩니다).
2. 그걸로 인메모리 SQLite 데이터베이스를 만들고 무작위 데이터로 시딩합니다.
   이게 다음 섹션의 실행 정확도 채점을 가능하게 합니다.

스키마 파일 자체는 그냥 표준 `CREATE TABLE` 문입니다. `--dialect`로 전달한
방언으로 작성하면 됩니다:

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

단일 컬럼으로 된 `PRIMARY KEY`와 `FOREIGN KEY`는 인라인이든 테이블 레벨
제약이든 인식됩니다. 복합 키(composite key)는 별도로 처리하지 않고 --
시딩 목적으로는 복합 키의 첫 번째 컬럼만 사용됩니다.

### 실행하기

```bash
python bench.py \
  --models qwen3:14b sqlcoder:7b \
  --data path/to/your_testset.jsonl \
  --schema path/to/schema.sql \
  --dialect postgres
```

### 직접 시딩한 데이터 사용하기 (`--seed-script`)

질문이 특정 리터럴(알려진 ID, 주문 참조 번호)을 필터링하는 순간 무작위
데이터는 무너집니다 -- 두 쿼리 다 빈 결과를 반환하고 그냥 "매칭"됩니다.
`--seed-script path/to/script.py`는 무작위 채우기를 여러분 자신의
로직으로 대체합니다: `--schema`는 여전히 테이블을 만드는 데 쓰이지만,
`bench.py`는 무작위 행 대신 여러분 스크립트의 `seed(conn)` 함수를
호출합니다.

```python
# my_seed.py
def seed(conn):
    conn.execute("INSERT INTO customers (id, name) VALUES (?, ?)", (42, "Ada Lovelace"))
    conn.execute("INSERT INTO orders (id, customer_id, status) VALUES (?, ?, ?)", (1, 42, "completed"))
```

```bash
python bench.py --models qwen3:14b --data your_testset.jsonl --schema schema.sql --seed-script my_seed.py
```

중요한 경우에는 함정(decoy) 데이터도 넣으세요 (선택되면 안 되는 더 최근
행 등). 그래야 틀린 쿼리가 빈 결과에 우연히 매칭되는 대신 실제로 틀린
결과를 낼 수 있습니다.

`--dialect`는 스키마와 reference 쿼리가 작성된 SQL 방언과 맞춰 주세요
(임의의 [sqlglot dialect 이름](https://sqlglot.com/sqlglot/dialects/dialect.html) --
예: `mysql`, `postgres`, `sqlite`, `snowflake`, `bigquery`; 기본값은
sqlglot의 범용 방언입니다). 이건 파싱/채점/DDL 변환에만 영향을 주고,
모델에 보내는 내용에는 영향을 주지 않습니다.

## 측정 항목

각 예제마다 모델의 원본 출력은 먼저 `extract_sql()`을 거칩니다 (마크다운
코드 펜스와, 일부 모델이 실제 쿼리 앞에 붙이는 추론 서술을 제거합니다).
그 다음 reference와 비교해 채점합니다.

**항상 계산되는 항목:**

- **valid** -- 생성된 SQL이 `--dialect` 기준으로 파싱이라도 되는가?
- **table_match** -- reference와 참조하는 테이블 집합이 같은가 (순서 무관)?
- **col_overlap** -- reference의 SELECT 컬럼 중 생성된 쿼리의 SELECT
  목록에도 있는 비율.
- **exact_match** -- 공백을 제거한 뒤 바이트 단위로 완전히 일치하는가.
  엄격한 기준입니다: 포맷, 컬럼 순서, 별칭(alias)만 다른 논리적으로
  동일한 SQL도 실패로 처리됩니다.

**`--schema`를 줬을 때만 계산되는 항목:**

- **execution_match** -- reference 쿼리와 생성된 쿼리를 같은 시드
  데이터베이스에 대해 실행하고 결과 집합을 비교합니다 (행의 멀티셋으로,
  순서 무관). 이건 학계 NL2SQL 벤치마크(Spider, BIRD)에서 쓰는 표준
  평가 방식입니다 -- 다르게 작성됐지만 의미상 동일한 쿼리를 정확히
  인정해 주기 때문입니다. 바로 `exact_match`가 놓치는 부분입니다.
- **generated_executable** / **reference_executable** -- 결과가 맞았는지와
  무관하게, 쿼리가 DB 에러 없이 실행이라도 됐는가?

스키마가 있다면 `execution_match`를 주요 지표로 삼고, `exact_match`는
엄격한 하한선 정도로만 여기세요 -- 진짜 그림이 아닙니다.

### 실행 채점의 한계

- **데이터는 무작위이지, 실제와 비슷하지 않습니다.** 시드 값은 타입에는
  맞지만 실제 데이터의 사본이 아닌 무작위 노이즈입니다 -- 단, 예외가
  하나 있습니다: `ENUM(...)`으로 선언되었거나 `CHECK (col IN (...))`
  제약이 걸린 컬럼은 그 선언된 값 집합에서 값을 뽑으므로, `WHERE status =
  'COMPLETED'` 같은 필터가 실제로 매칭될 가능성이 있습니다. 값 집합이
  선언되어 있지 않다면, 같은 필터는 무작위 텍스트에 대해 보통 0개 행과
  매칭되어 reference와 생성된 쿼리 둘 다 빈 결과를 반환하고 -- 실제로는
  그 필터를 전혀 검증하지 않은 채로 "매칭"됩니다. 무작위 생성으로는
  나오지 않을 특정 리터럴(알려진 ID, 날짜 범위)에 대한 필터도 같은
  문제를 일으킵니다. 스키마에 유효한 값이 선언되어 있지 않다면 여러분의
  스키마 사본에 `CHECK` 제약을 추가하세요(합성 테스트 데이터베이스를
  만드는 데만 쓰이므로 실제 운영 DB에는 영향 없이 안전합니다); 특정
  리터럴 값에 대한 필터가 문제라면, 무작위 행 대신 질문에 필요한 정확한
  행을 시딩하도록 아래의 `--seed-script`를 사용하세요.
- **SELECT만 지원합니다.** SELECT가 아닌 문장은 시드 데이터베이스에 대해
  실행되지 않습니다 (의도적인 설계입니다 -- 잘못된 모델 출력 하나가
  같은 실행 중 다른 예제들이 의존하는 데이터를 바꿔버리는 걸 막기
  위함입니다).
- **스키마의 방언과 무관하게 실행 대상은 항상 SQLite입니다.** 표준
  DDL/DML은 sqlglot을 통해 잘 변환되지만, 방언에 크게 의존하는 SQL
  (특이한 윈도우 함수, JSON 연산자, 날짜 연산 등)은 완벽하게 변환되지
  않을 수 있습니다. 결과가 이상해 보인다면, 숫자를 그대로 믿기 전에
  `results.md`의 예제별 상세 내용과 `generated_error`/reference 에러를
  확인하세요.

## 후보 모델

NL2SQL teacher로 벤치마크해볼 만한 출발점 모델 목록입니다. 범용 LLM과
NL2SQL 특화 체크포인트로 나눴습니다. pull 명령은 Ollama 기준이며, 공식
Ollama 라이브러리 태그가 없는 모델의 경우 Ollama가 Hugging Face의 어떤
GGUF 저장소든 `ollama pull hf.co/<repo>:<quant>`로 직접 pull할 수
있습니다 -- Modelfile을 직접 만들 필요가 없습니다. 목록에 있는 quant
태그가 404가 난다면, 정확한 이름을 Hugging Face 저장소의 파일 목록에서
확인하세요 (커뮤니티 양자화 배포자마다 태그 표기법이 다를 수 있습니다).

### 범용

| 모델 | 크기 (활성 파라미터) | Pull 명령 |
| --- | --- | --- |
| Qwen3-14B | 14.8B dense | `ollama pull qwen3:14b` |
| Qwen3-30B-A3B | 30B 총 / 3B 활성 (MoE) | `ollama pull qwen3:30b-a3b` |
| Qwen3-Coder-30B-A3B | 30B / 3.3B 활성 (MoE) | `ollama pull qwen3-coder:30b-a3b` |
| Granite 4.0 H-Small | 32B / 9B 활성 (MoE) | `ollama pull ibm/granite4:small-h` |

### NL2SQL 특화

| 모델 | 크기 (활성 파라미터) | Pull 명령 |
| --- | --- | --- |
| OmniSQL-7B | 7B dense | `ollama pull hf.co/mradermacher/OmniSQL-7B-GGUF:Q4_K_M` |
| OmniSQL-14B | 14B dense | `ollama pull hf.co/mradermacher/OmniSQL-14B-GGUF:Q4_K_M` |
| OmniSQL-32B | 33B dense | `ollama pull hf.co/mradermacher/OmniSQL-32B-i1-GGUF:Q4_K_M` |
| SQLCoder-7B-2 (defog, CodeLlama 기반) | 7B dense | `ollama pull pxlksr/defog_sqlcoder-7b-2:Q4_K_M` |
| Llama-3-SQLCoder-8B (defog) | 8B dense | `ollama pull hf.co/QuantFactory/llama-3-sqlcoder-8b-GGUF:Q4_K_M` |
| SQLCoder2-15B (defog, StarCoder 기반) | 15B dense | `ollama pull hf.co/TheBloke/sqlcoder2-GGUF:Q4_K_M` |
| Arctic-Text2SQL-R1-7B (Snowflake) | 7B dense | `ollama pull a-kore/Arctic-Text2SQL-R1-7B` |
| XiYanSQL-QwenCoder-7B | 7B dense | `ollama pull hf.co/mradermacher/XiYanSQL-QwenCoder-7B-2504-GGUF:Q4_K_M` |
| XiYanSQL-QwenCoder-14B | 14B dense | `ollama pull hf.co/visualbruno/XiYanSQL-QwenCoder-14B-2502-Q5_K_M-GGUF` |
| XiYanSQL-QwenCoder-32B | 32B dense | `ollama pull hf.co/mradermacher/XiYanSQL-QwenCoder-32B-2504-GGUF:Q4_K_M` |

이건 고정된 목록이 아니라 출발점입니다 -- Ollama가 실행할 수 있는 모델이면
뭐든 후보가 될 수 있습니다. 아래 "후보 모델 추가하기"를 참고하세요.
Arctic-Text2SQL-R1과 XiYanSQL-QwenCoder는 모든 크기를 항상 공개로
배포하는 건 아니니, 각 조직의 Hugging Face 페이지에서 현재 라인업을
확인하세요.

한 가지 주의할 함정: 공식 Ollama 라이브러리 태그인 `sqlcoder:7b`와
`sqlcoder:15b`는 defog의 **원본, 더 오래된** SQLCoder 체크포인트입니다
(각각 Mistral-7B 기반, 그리고 최초의 StarCoder-15B 릴리스) -- 위 표에
있는, 개선된 `sqlcoder-7b-2`(CodeLlama 기반)나 `sqlcoder2` 버전이
아닙니다. 이 개선 버전들은 커뮤니티가 배포한 태그/GGUF로만 존재합니다.
짧은 공식 태그 이름이라고 해서 최신 버전이라는 뜻은 아니니, 어떤 태그가
실제로 어떤 체크포인트를 가리키는지 확인하고 사용하세요.

## 자신의 하드웨어를 판단하는 법

모델을 실행할 수 있는지는 서로 독립적인 두 가지에 달려 있습니다 --
총 파라미터 수 하나만으로 모델을 제외하지 마세요:

- **메모리 사용량** (VRAM+RAM 합계에 들어가야 함): 사용하는 양자화
  수준에서 *총* 파라미터 수에 비례합니다. Ollama는 모델이 VRAM에 다
  들어가지 않으면 자동으로 레이어를 GPU와 시스템 RAM에 나눠 올립니다 --
  실행은 되지만, CPU에 올라간 레이어 부분은 느려집니다.
- **토큰당 연산 비용** (속도를 좌우함): forward pass당 *활성* 파라미터
  수에 비례합니다. Dense 모델은 매 토큰마다 파라미터의 100%를
  활성화합니다. "30B-A3B"(총 30B, 활성 3B)처럼 이름 붙은
  Mixture-of-Experts(MoE) 모델은 일부만 활성화하므로, 동일한 크기의
  dense 모델보다 훨씬 적은 연산 비용으로 훨씬 많은 총 용량을 가질 수
  있습니다.

대략적인 기준: 양자화된 모델의 크기(GB)는 대략 `총 파라미터(B) x
비트/가중치 / 8`입니다 (예: 14B를 4비트로 양자화하면 대략 7GB, 약간의
오버헤드 포함). 이 값이 VRAM+RAM 합계보다 충분히 작다면 실행은 됩니다.
*빠르게* 도는지는 VRAM에서 얼마나 많은 부분이 시스템 RAM으로 넘어갔는지,
그리고 토큰당 활성 파라미터가 얼마나 되는지에 달려 있습니다.

Teacher 역할은 오프라인에서 딱 한 번만 실행되므로, 하드웨어 트레이드오프를
고를 때는 속도보다 생성 *품질*을 우선하세요 -- 느리지만 더 나은 teacher가
보통 기다릴 가치가 있습니다.

## 후보 모델 추가하기

Ollama가 서빙할 수 있는 태그면 뭐든 됩니다:

```bash
# 공식 Ollama 라이브러리
ollama pull <tag>

# Hugging Face의 어떤 GGUF 저장소든, Modelfile 없이
ollama pull hf.co/<user>/<repo>:<quant>
```

그 다음 그 태그를 `--models`에 넣기만 하면 됩니다.

## 출력

`bench.py`는 마크다운 리포트 하나(기본값 `results.md`, `--output`으로
변경 가능)를 작성합니다:

- 요약 테이블 (`--schema`를 줬다면 실행 매칭과 실행 성공률 포함;
  정확 일치, 파싱 가능 여부, 테이블 매칭, 평균 컬럼 겹침, 평균 지연
  시간은 항상 포함) -- 테스트한 모든 모델에 대해.
- 각 모델이 생성한 SQL을 reference와 나란히 보여주는 예제별 상세
  테이블 -- 근소한 차이(near-miss)를 직접 검토할 수 있게.

실행 중에는 예제별 진행 상황과 최종 요약을 stdout에도 실시간으로
출력합니다.

## 저장소 구성

```
bench.py           CLI 진입점
metrics.py         텍스트 기반 채점 (파싱 가능 / 테이블 매칭 / 컬럼 겹침 / 정확 일치)
db_builder.py       스키마 파싱 + 인메모리 SQLite 데이터베이스 시딩
execution.py        시드 데이터베이스에 대한 실행 정확도 채점
ollama_client.py    최소 구현의 Ollama /api/chat 클라이언트
data/example.jsonl        데모 테스트셋 (일반적인 스키마, 실제 프로젝트와 무관)
data/example_schema.sql   위 데모용 스키마
```

`data/` 아래의 그 외 파일들은 기본적으로 git에서 무시됩니다 -- 여러분의
(비공개일 수도 있는) 스키마와 테스트셋은 그곳에 두면 됩니다.
