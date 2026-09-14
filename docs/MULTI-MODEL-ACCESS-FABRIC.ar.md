# W1 Multi-Model Access Fabric

## 1. الهدف

هذه الطبقة تجعل W1 Nexus محايدة تجاه النماذج والمزودين، ومحلية أولًا، وقابلة للعمل دون أي خادم مملوك لـW1.

لا يفترض النظام أن المستخدم يختار نموذجًا واحدًا. يستطيع الفرد أو المبرمج أو الشركة تكوين **Portfolio من عدة نماذج مفضلة**، بحيث يعمل كل نموذج في الدور الذي يناسبه:

- نموذج محلي للتنفيذ المتكرر والخاص.
- نموذج سحابي قوي للتفكير المعقد.
- نموذج مختلف للمراجعة.
- نموذج مستقل للتحقق.
- نموذج خاص بالشركة داخل شبكتها.
- تطبيق خارجي يملك المستخدم فيه حسابًا ويعرض واجهة مسموحة للربط.

## 2. ما ليست عليه W1 Nexus

W1 Nexus ليست نموذج أوزان واحدًا، ولا تعتمد على نموذج مركزي تابع لنا. هي:

```text
Open protocol
+ Open runtime
+ Multi-model coordination
+ Governed tools and actions
+ Embeddable local service
```

يمكنها استعمال نماذج مفتوحة الأوزان، لكنها ليست هي نفسها نموذجًا ذا أوزان.

## 3. أوضاع الوصول

### 3.1 Local endpoint

للخوادم المحلية المتوافقة مع OpenAI أو غيرها، مثل خادم يختاره المستخدم على جهازه.

```json
{
  "access_mode": "local_endpoint",
  "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
  "privacy_mode": "local"
}
```

### 3.2 BYOK API

يستعمل المستخدم مفتاح API الخاص به. تحفظ إعدادات W1 اسم مرجع السر فقط، ولا تحفظ القيمة داخل المشروع أو سجل التشغيل.

### 3.3 OAuth broker

يدعم نموذج البيانات هذا الوضع للمزودين الذين يوفرون تدفق OAuth رسميًا، لكن Broker عام لتسجيل الدخول وتجديد الرموز لم يكتمل في `dev32`.

لا تستخرج W1 Cookies أو جلسات من تطبيقات أخرى، ولا تفترض أن اشتراك تطبيق المستهلك يساوي حق API.

### 3.4 External application

يمكن لتطبيق خارجي يملكه المستخدم أو الشركة استقبال طلب W1 عبر:

- `stdio` JSON.
- HTTP محلي على loopback.

يبقى تسجيل الدخول والحساب داخل التطبيق الخارجي نفسه.

### 3.5 Custom plugin

يمكن للمبرمج توفير Factory بصيغة:

```text
package.module:build_adapter
```

وتعيد Factory كائنًا يطبق عقد `ProviderAdapter`.

## 4. ModelProfile

كل نموذج يسجل كملف قدرة مستقل:

```yaml
model_id: local-coder
provider_id: local
connector_type: openai_compatible
model_name: user-selected-model
access_mode: local_endpoint
privacy_mode: local
capabilities:
  executor: 0.92
  execution: 0.90
  coding: 0.94
roles:
  - executor
domains:
  - coding
```

الدرجات خاصة بمهام المستخدم أو تقييماته، وليست ترتيبًا عالميًا تدعيه W1.

## 5. Portfolios متعددة النماذج

### 5.1 best_fit

يختار أعلى نموذج ملاءمة للمهمة وفق القدرة والدور والمجال والخصوصية والتكلفة والسرعة.

### 5.2 fallback_chain

يجرب النماذج بالترتيب المحدد، وينتقل إلى التالي عند تعذر الأول.

### 5.3 parallel_collect

يشغّل عدة نماذج بالتوازي ويعيد جميع المخرجات.

لا ينتخب فائزًا بعدد النماذج، ولا يعني اتفاق ثلاثة نماذج خفيفة أنها تتغلب على دليل أو تحقق أقوى.

### 5.4 verified_synthesis

يشغّل منتجين أو متخصصين بالتوازي، ثم يرسل مخرجاتهم إلى نموذج متحقق مستقل. يجب أن يكون المتحقق نموذجًا مختلفًا عن المنتجين.

## 6. التكامل مع Orchestrator

تعرض `PortfolioProviderAdapter` المجموعة كموارد متوافقة مع عقد Orchestrator الحالي.

يمكن أن تحتوي `ExecutionResourcePlan` على:

```text
portfolio-preferred-team
```

بدل مورد نموذج منفرد.

في `parallel_collect` تنتج Portfolio حزمة مرشحين موسومة:

```json
{
  "selection_required": true,
  "selection_basis": "governed_review_not_model_count_vote"
}
```

ثم تستمر دورة W1-CIP بالمراجعة والتحقق والقرار.

## 7. التضمين في منتجات أخرى

### 7.1 Python SDK

```python
from w1cip import EmbeddedW1Runtime, ModelAccessFabric, ModelAccessStore

store = ModelAccessStore("model-access.sqlite3")
runtime = EmbeddedW1Runtime(ModelAccessFabric(store))
models = runtime.list_models()
```

### 7.2 Local Control API

```bash
w1 --workspace ./project access serve
```

الخصائص:

- Loopback فقط.
- Bearer token محلي بصلاحية ملف مقيدة.
- لا يحتاج حساب W1.
- لا يرسل الطلبات عبر خوادم W1.

المسارات الأساسية:

```text
GET  /v1/health
GET  /v1/models
GET  /v1/portfolios
POST /v1/invoke/model
POST /v1/invoke/portfolio
```

### 7.3 تبني شركات الذكاء الاصطناعي

تستطيع الشركة:

- إضافة Connector رسمي لنماذجها.
- تضمين Python SDK داخل منتجها.
- تشغيل W1 كخدمة محلية داخل التطبيق.
- تقديم Plugin أو Endpoint خاص.
- استعمال W1-CIP وMCP دون استعمال واجهة W1 الرسومية.

## 8. الخصوصية والسيادة

```text
requires_w1_owned_server: false
local_first: true
```

تبقى المشاريع والذاكرة والسجل محليًا افتراضيًا. الاتصال الخارجي يحدث فقط مع النموذج أو التطبيق الذي اختاره المستخدم.

## 9. التقييم التنفيذي

الأمر:

```bash
w1 --workspace ./project evaluate
```

يشغّل Benchmark محليًا يختبر:

- تسجيل عدة نماذج.
- التنفيذ المتوازي وسرعته.
- fallback فعلي.
- منع التصويت العددي.
- متحقق مستقل.
- Local Control API.
- عدم الحاجة إلى خادم W1.

ويصدر درجتين منفصلتين:

- قدرة Backend المتحققة.
- اكتمال منتج W1 Nexus الكامل.

## 10. الحدود الحالية

- لا يوجد Broker OAuth عام حتى الآن.
- لا توجد مزامنة حسابات بين الأجهزة.
- لا تتحقق W1 من جودة نموذج حقيقي دون Benchmark خاص بذلك النموذج.
- التقييم الحالي يقيس وظائف W1 المنفذة، لا ذكاء النماذج ولا التفوق الشامل على منتجات أخرى.
