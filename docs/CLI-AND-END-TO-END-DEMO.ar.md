# واجهة W1 Nexus الطرفية والتجربة المرجعية الكاملة

## الهدف

واجهة `w1` ليست غلافًا نصيًا حول نموذج واحد. إنها نقطة تشغيل لـW1-CIP تربط:

- مخزن جلسات append-only.
- منظم المهام متعدد الأدوار.
- موصلات مزودين متعددة.
- سياسة الحصص والاحتياطيات والتحويلات.
- تقليل السياق.
- الاستئناف الآمن ومفاتيح idempotency.
- إعادة التشغيل والتدقيق والتصدير.

## التثبيت

من جذر المشروع:

```bash
python -m pip install -e .
w1 --version
```

أو دون تثبيت:

```bash
PYTHONPATH=src python -m w1cip --help
```

## إنشاء مساحة عمل

```bash
w1 init ./my-project
```

ينشئ:

```text
my-project/.w1nexus/
├── workspace.json
├── providers.json
├── context.json
├── session.sqlite3        # بعد أول حدث
├── orchestrator.sqlite3   # بعد أول تشغيل
├── reports/
└── runs/
```

لا تكتب الواجهة مفاتيح API. يخزن `providers.json` أسماء متغيرات البيئة فقط.

## الفحص التشخيصي

```bash
w1 --workspace ./my-project doctor
w1 --workspace ./my-project doctor --deep
```

يفحص:

- إصدار Python.
- إعداد مساحة العمل.
- جذر ثقة المسجل.
- إعداد سلطة Runtime.
- المخططات المضمنة في الحزمة.
- موصلات المزودين دون طلبات شبكة.
- وجود مراجع الأسرار دون طباعة قيمها.
- تثبيت معرفات النماذج بدل aliases متحركة.
- سلامة جميع الجلسات الموجودة.

## وضع التخطيط بلا استهلاك

```bash
w1 plan \
  --goal goal.json \
  --team team.json \
  --resources resources.json \
  --domain electronics
```

يعرض رسم المهام والاعتماديات والدور والمورد المتوقع والحالة عند غياب مورد مناسب. لا ينشئ قاعدة بيانات ولا يستدعي أي نموذج.

## إدخال أحداث بروتوكولية

```bash
w1 --workspace ./my-project sessions append --file bootstrap.json
```

يدعم الملف:

- غلافًا واحدًا.
- مصفوفة أغلفة مرتبة.

ولمنع الكتابة فوق رأس تغير بالتزامن:

```bash
w1 sessions append \
  --file next-event.json \
  --expected-last-sequence 40
```

## التشغيل الحي

بعد وجود `GoalContract` و`TeamPlan` و`ExecutionResourcePlan` في الجلسة:

```bash
w1 --workspace ./my-project run \
  --run-id run-driver-selection-001 \
  --session-id session-pump-001 \
  --goal-id goal-pump-driver-001 \
  --team-plan-id team-plan-pump-001 \
  --resource-plan-id execution-resource-plan-pump-001 \
  --context .w1nexus/context.json \
  --events text
```

أنماط الأحداث:

```text
text    عرض بشري إلى stderr
json    JSON Lines إلى stderr
silent  دون تقدم حي
```

تبقى النتيجة النهائية في stdout، مما يسمح بفصلها عن بث الأحداث في خطوط الأتمتة.

## الاستئناف

```bash
w1 --workspace ./my-project resume run-driver-selection-001
```

يستعيد معرفات الجلسة والكيانات ومسار السياق من ملف تشغيل لا يحتوي الأسرار. لا يخترع محاولة جديدة إذا كانت محاولة مزود معلقة؛ يعيد مفتاح idempotency نفسه عندما يكون ذلك آمنًا.

## الحالة والتدقيق

```bash
w1 status run-driver-selection-001
w1 sessions list
w1 sessions show session-pump-001
w1 sessions verify session-pump-001
w1 sessions replay session-pump-001
w1 sessions rebuild session-pump-001
```

التقارير:

```bash
w1 audit session-pump-001 --format markdown
w1 export run-driver-selection-001 --format json
```

## التجربة المرجعية دون شبكة

```bash
w1 --workspace ./demo demo --reset
```

تشغل السلسلة:

```text
Executor primary quota exhausted
→ Fallback executor
→ Evidence
→ Verification
→ Independent Review
→ Decision
→ Approval
→ FinalResult
```

وتتحقق من أن الحقل `private-note` لا يصل إلى المزود.

إعادة الأمر دون `--reset` لا تضيف أحداثًا أو طلبات جديدة لأن التشغيل مكتمل وثابت.

## اختبار المقاومة الداخلي

```bash
w1 --workspace ./demo benchmark --reset
```

يفحص:

- اكتمال التشغيل.
- سلامة السجل.
- الإفصاح عن نفاد الحصة.
- فرض المراجعة الإضافية.
- عدم تسرب السياق غير الممنوح.
- فردية مفاتيح idempotency.
- عدم تكرار الأحداث أو الاستدعاءات عند إعادة التشغيل.
- ثبات النتيجة النهائية.

هذا اختبار لثوابت W1 الداخلية، وليس مقارنة أداء خارجية بمنتجات أخرى.

## مخرجات JSON

يعمل الخيار العام:

```bash
w1 --json ...
```

مع جميع الأوامر، ويعيد أخطاء ثابتة مثل:

```json
{
  "ok": false,
  "error_code": "runtime_authority_not_configured",
  "message": "Set runtime_identity.authorized_by to an active orchestrator RoleAssignment."
}
```

## حدود هذه الخطوة

لم تُنفذ بعد:

- واجهة رسومية مكتبية.
- استعمال الحاسوب العام.
- محررات مستندات وجداول وشرائح مدمجة.
- MCP.
- تشغيل موزع بين عدة أجهزة.
- الجداول الزمنية والمحفزات.
- توقيع رقمي خارجي للأحداث.

هذه عناصر Action Runtime وWorkspace اللاحقة، وليست مخفية خلف ادعاء اكتمال غير صحيح.


## MCP وTool Registry

أضيفت في الخطوة 26 الأوامر التالية:

```text
w1 mcp servers list|add|remove
w1 mcp probe
w1 mcp serve --stdio
w1 tools list|plan|approve|call|history
w1 resources list|read
w1 prompts list|get
```

راجع [وثيقة MCP وUnified Tool Registry](MCP-AND-TOOL-REGISTRY.ar.md).


## أوامر الذاكرة طويلة المدى

تضيف الخطوة 29 مجموعة `w1 memory`:

```bash
w1 memory add --file memory.json
w1 memory search "project requirement"
w1 memory context "task context" --token-budget 1200
w1 memory conflicts list
w1 memory graph --anchor "project entity" --depth 2
w1 memory verify
```

تقرأ الأوامر هوية الذاكرة الافتراضية وNamespace وProject من `.w1nexus/workspace.json`، ويمكن تجاوزها لكل أمر دون تعديل الإعداد الدائم.
