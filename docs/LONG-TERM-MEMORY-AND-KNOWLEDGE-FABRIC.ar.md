# W1 Long-Term Memory + Knowledge Fabric

توفر هذه الطبقة ذاكرة طويلة المدى محكومة للمستخدم والمشاريع وفرق الوكلاء. لا تعامل الذاكرة على أنها سجل محادثة كبير أو Vector Database غير مفسرة، بل كسجل معرفي له هوية وإصدارات ومصدر وصلاحيات وفترة صلاحية وتعارضات قابلة للمراجعة.

## الهدف

تحتاج W1 Nexus الكاملة إلى الاحتفاظ بالقرارات والحقائق والتفضيلات والإجراءات عبر الجلسات، مع منع أربع مشكلات شائعة:

1. خلط بيانات مشروع بمشروع آخر.
2. تقديم استنتاج نموذج على أنه حقيقة قالها المستخدم.
3. استعمال معلومة قديمة أو متعارضة دون تنبيه.
4. إرسال سياق أكثر مما تحتاجه المهمة إلى نموذج أو أداة.

لذلك تعمل طبقة الذاكرة وفق مبدأ:

> لا تدخل معلومة إلى سياق وكيل إلا إذا عُرفت هويتها ومصدرها ومشروعها وصلاحيتها وحق الوصول إليها.

## أنواع الذاكرة

يدعم التنفيذ المرجعي:

```text
fact
decision
preference
constraint
procedure
observation
summary
relationship
```

النوع لا يثبت صحة المحتوى. ذاكرة `fact` تعني أن السجل يقدم ادعاءً واقعيًا، بينما تحدد `provenance` والثقة والمراجعات مقدار الاعتماد الممكن عليه.

## بنية السجل

مثال:

```json
{
  "memory_id": "mem-pump-supply-voltage",
  "namespace_id": "w1-local",
  "project_id": "pump-project",
  "kind": "fact",
  "subject": "pump prototype",
  "predicate": "supply-voltage",
  "value": 12,
  "text": "The pump prototype available supply voltage is 12 V.",
  "summary": "Available supply voltage: 12 V.",
  "confidence": 0.98,
  "sensitivity": "internal",
  "visibility": "team",
  "team_id": "workspace-team",
  "tags": ["pump", "electronics"],
  "source": {
    "source_type": "user_statement",
    "source_ref": "conversation://pump-design"
  }
}
```

## المصدر Provenance

كل مراجعة تحمل:

- `source_type`.
- الطرف الذي التقط المعلومة.
- وقت الالتقاط.
- مرجع المصدر عندما يتوفر.
- مقتطفًا قصيرًا اختياريًا.

أنواع المصادر الحالية:

```text
user_statement
protocol_entity
artifact
tool_result
model_inference
import
system_observation
```

لا يغير W1 `model_inference` إلى `user_statement` أو `tool_result` أثناء التلخيص أو النقل.

## الإصدارات وعدم المحو

كل تعديل ينشئ مراجعة جديدة:

```text
memory@1
→ memory@2
→ memory@3
```

جداول المراجعات وسجل الأحداث محمية من `UPDATE` و`DELETE` بواسطة SQLite triggers. الإلغاء أو الاستبدال يضاف كمراجعة نهائية جديدة بدل حذف التاريخ.

كل مراجعة ترتبط بسابقتها عبر SHA-256. كما يحمل سجل الأحداث سلسلة تجزئة مستقلة. تستطيع:

```bash
w1 memory verify
```

كشف فجوات الإصدارات أو التعديل منخفض المستوى في السجل.

## الفصل بين المشاريع

كل عملية بحث أو استرجاع تحتاج:

```text
namespace_id
project_id
```

لا يبحث W1 في مشروع آخر تلقائيًا حتى إذا كانت الكلمات متشابهة. يمكن إدخال ذاكرة عامة في مشروع `global`، لكن لا تُقرأ إلا عند طلب `include_global` صراحة أو وفق سياسة مساحة العمل.

## الصلاحيات

لكل سجل مالك وACL منفصلة. الصلاحيات:

```text
read
write
share
resolve
admin
```

الرؤية:

- `private`: المالك أو من مُنح صلاحية صريحة.
- `team`: أعضاء مجموعة الفريق أو ACL صريحة.
- `public`: متاح للقراءة، ولا يسمح به إلا مع تصنيف `public`.

لا تكفي معرفة `memory_id` لقراءتها.

## الحساسية

التصنيفات:

```text
public
internal
confidential
restricted
```

يمنع التنفيذ ذاكرة `public visibility` ذات تصنيف أعلى من `public`. لا يوفر الإصدار الحالي تشفير قيم الذاكرة عند السكون؛ لذلك تحتاج البيئات الإنتاجية إلى تشفير القرص أو KMS قبل تخزين بيانات شديدة الحساسية.

## التعارضات

إذا وجد سجلان نشطان في المشروع نفسه ولهما `subject + predicate` نفسيهما وقيم مختلفة، ينشئ W1 `MemoryConflict`:

```text
pump prototype / supply-voltage = 12
pump prototype / supply-voltage = 24
```

لا يحسم W1 التعارض بناء على حداثة السجل أو ثقة النموذج وحدها. الحقول المتعارضة لا تُرسل إلى Orchestrator. يجب أن تحسمها جهة تحمل `resolve`:

```bash
w1 memory conflicts list
w1 memory conflicts resolve CONFLICT_ID \
  --winner mem-pump-supply-voltage \
  --rationale "The second meter used the wrong range."
```

السجلات الخاسرة تنتقل إلى `superseded` مع الاحتفاظ بتاريخها.

## الصلاحية والقدم

يدعم السجل:

- `valid_from`.
- `valid_until`.
- `stale_after`.

بعد `valid_until` يصبح السجل `expired` ويخرج من البحث النشط. بعد `stale_after` يستبعد افتراضيًا، ويمكن إظهاره بوضوح عبر `--include-stale` دون معاملته كمعلومة حديثة.

## الاسترجاع الهجين

يجمع التنفيذ المرجعي أربع إشارات:

1. المرشحات المنظمة: المشروع والنوع والوسوم والموضوع والعلاقة والثقة.
2. SQLite FTS5 عندما تكون متاحة.
3. متجه محلي hashed vector ثابت وحتمي.
4. توسع Knowledge Graph من كيان مرساة.

المتجه المحلي ليس Embedding لنموذج تأسيسي ولا ندعي أنه يملك الفهم الدلالي نفسه. صمم `EmbeddingProvider` بحيث يمكن لاحقًا إضافة مزود Embeddings حقيقي دون تغيير ACL أو الإصدارات أو المصدر.

## الرسم المعرفي

عندما يحمل السجل `object_entity` ينشئ حافة:

```text
subject ──predicate──> object_entity
```

مثال:

```text
battery pack ──powers──> pump controller
```

يمكن استرجاع الجيران:

```bash
w1 memory graph --anchor "battery pack" --depth 2
```

ولا تظهر حافة إذا لم يملك الطرف حق قراءة الذاكرة التي أنشأتها.

## ضغط السياق

يبني الأمر:

```bash
w1 memory context "pump controller current" --token-budget 1200
```

حزمة محدودة الحجم تحتوي:

- السجلات الأعلى صلة.
- Citation مثل `memory:mem-id@revision`.
- الثقة والتصنيف والمصدر.
- تحذيرات التعارض.
- عدد العناصر التي حُذفت بسبب الميزانية.

لا يلخص التنفيذ المحلي المحتوى بواسطة نموذج بصورة صامتة. يستعمل `summary` المعتمدة إن وجدت، وإلا النص الأصلي حتى حدود الميزانية.

## التكامل مع Orchestrator

يوفر `KnowledgeContextProvider` الحقول المطلوبة صراحة فقط. إذا كانت المهمة تطلب:

```text
supply-voltage
```

يبحث عن ذاكرة نشطة غير متعارضة تحمل `predicate: supply-voltage`. لا يرسل `secret-note` أو بقية المشروع.

الأولوية:

```text
explicit current context
→ governed memory
→ awaiting_user
```

إذا وجد تعارض ينتقل التشغيل إلى:

```text
orchestrator_memory_conflict
```

ولا يستدعي النموذج بقيمة مختارة آليًا.

المصادر المستخدمة تسجل في حدث `orchestrator.context.granted` تحت `memory_citations`.

## ذاكرة الفريق

يستطيع سجل `team` منح القراءة لمجموعة مثل:

```text
workspace-team
```

ويمكن منح وكيل أو مراجع صلاحيات إضافية بصورة صريحة. لا تعني عضوية الفريق حق التعديل أو حل التعارض إلا إذا مُنحت تلك الصلاحية.

## أوامر CLI

```bash
w1 memory add --file memory.json
w1 memory revise MEMORY_ID --file revised.json
w1 memory get MEMORY_ID
w1 memory history MEMORY_ID
w1 memory list
w1 memory search "pump voltage"
w1 memory context "pump controller current" --token-budget 1200
w1 memory graph --anchor "pump controller" --depth 2
w1 memory grant MEMORY_ID --to-type agent --to-id reviewer-one --permission read
w1 memory ungrant MEMORY_ID --from-type agent --from-id reviewer-one --permission read
w1 memory conflicts list
w1 memory revoke MEMORY_ID --reason "The component was replaced."
w1 memory verify
w1 memory export
```

يمكن تجاوز الهوية الافتراضية لكل أمر عبر:

```text
--namespace
--project
--principal-type
--principal-id
--group
```

## قاعدة البيانات

تُحفظ افتراضيًا في:

```text
.w1nexus/memory.sqlite3
```

والجداول الأساسية:

```text
memories
memory_revisions
memory_acl
memory_conflicts
knowledge_edges
memory_events
memory_fts
```

`memories` إسقاط للحالة الحالية، بينما `memory_revisions` و`memory_events` هما السجلان غير القابلين لإعادة الكتابة.

## الحدود الحالية

لم يكتمل بعد:

- تشفير القيم عند السكون داخل W1 نفسه.
- KMS أو HSM خارجي.
- Embeddings تأسيسية مدمجة افتراضيًا.
- مزامنة ذاكرة موزعة بين الأجهزة.
- حذف تشفيري كامل يلبي سياسات مؤسسات متعددة.
- استيراد تلقائي واسع من البريد والمستندات دون موافقة.
- تعلم آلي تلقائي يقرر ما الذي يجب حفظه.

بصورة افتراضية:

```json
{
  "automatic_model_inference_persistence": false
}
```

أي أن مخرجات النماذج لا تصبح ذاكرة طويلة المدى لمجرد أن النموذج قالها.
