# ExecutionResourcePlan في W1-CIP v0.1

## 1. الغرض

`ExecutionResourcePlan` هو العقد الذي يربط تشكيل الفريق بواقع تشغيل النماذج: حدود الاستخدام الخاصة بالمستخدم أو مساحة العمل، تفاوت قدرات النماذج، الاحتياطي، ترتيب البدائل، وحدود خفض الجودة.

وجود عدة نماذج لا يعني أنها متساوية، ولا يعني أن رأي نموذجين خفيفين يتغلب عدديًا على نموذج أقوى. في W1-CIP تبقى القرارات مبنية على السلطة المخولة والأدلة والتحقق، بينما يستخدم هذا الكيان لاختيار المورد الأنسب لكل مهمة.

## 2. لماذا لم توضع الحصة داخل AgentCard؟

`AgentCard` تصف هوية الوكيل ومزوده وقدراته وسياسة بياناته. أما حصة الاستخدام فهي مرتبطة غالبًا بحساب مستخدم أو Workspace أو مشروع API، وتتغير أثناء الجلسة. لذلك لا تُعامل كخاصية ثابتة للنموذج، بل كملاحظة زمنية داخل خطة موارد قابلة للإصدار.

نفس النموذج قد يكون متاحًا بالكامل لمستخدم، ومنهك الحصة لمستخدم آخر، أو يملك حدًا مختلفًا في حساب آخر.

## 3. عدم وجود ترتيب عالمي ثابت للنماذج

لا تسجل المواصفة حكمًا مثل «النموذج أ أقوى دائمًا من النموذج ب». تسجل `fitness_score` داخل قاعدة توجيه محددة، مع:

- نوع المهمة والمرحلة والخطر والتعقيد والمجال.
- مصدر التقييم.
- درجة الثقة في التقييم.
- أساس مكتوب يوضح سبب الدرجة.

وبذلك يمكن لنموذج خفيف أن يكون المرشح الأول لمهمة منخفضة المخاطر ومحدودة، لكنه يكون دون حد الجودة لمهمة مادية الخطورة.

أسماء النماذج الواردة في الأمثلة مثل `gemini-3.5-lite` و`opus-5` و`sol-5.6` هي تسميات سيناريو قدمها المستخدم لاختبار المعمارية، وليست ادعاءً من المواصفة عن توافرها التجاري أو ترتيبها الفعلي.

## 4. حصة المورد

كل مورد يربط:

```yaml
resource_id: opus-resource
role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-executor-opus-001
  entity_version: 1
agent_card:
  entity_type: agent_card
  entity_id: agent-opus-01
  entity_version: 1
quota:
  accounting_scope: user_account
  meter: requests
  window: week
  status: low
  limit_units: 50
  remaining_units: 4
  reserved_units: 2
  observed_at: 2026-08-05T15:35:00Z
  reset_at: 2026-08-10T00:00:00Z
  source: user_report
  status_reason: Two requests are protected for critical work.
```

الحالات:

```text
available | low | exhausted | unknown
```

الحصة المجهولة لا تُعامل كحصة غير محدودة. مصدر المعلومة قد يكون API المزود، عدادًا محليًا، تصريح المستخدم، أو غير معروف.

## 5. الاحتياطي

`reserved_units` لا يستهلك في العمل العادي عندما تكون المرحلة خارج `protected_phases`.

مثال:

```yaml
reserve_policy:
  protected_phases:
    - review
    - verification
    - approval
    - synthesis
  release_condition: explicit_orchestrator_or_owner
```

إذا بقيت أربع وحدات وكان اثنتان منها احتياطًا، فلا يجوز لمهمة تنفيذ عادية تستهلك ثلاث وحدات. ينتقل النظام إلى البديل بدل تدمير القدرة المتبقية على التحقق أو التجميع النهائي.

## 6. قواعد التوجيه

كل قاعدة تحدد:

- الأدوار والمراحل التي تنطبق عليها.
- مستوى الخطر والتعقيد والمجال.
- المرشح الأساسي والبدائل المرتبة.
- درجة الملاءمة الخاصة بالمهمة.
- الحد الأدنى للجودة.
- ما يحدث عند تعذر المرشح الأساسي.
- هل يحتاج البديل إلى مراجعة إضافية.

```yaml
selection_objective: capability_first
minimum_fitness_score: 85
active_resource_id: opus-resource
activation_reason: initial_selection
on_primary_unavailable: activate_fallback
below_floor_policy: prohibited
```

عند نفاد الحصة ينشأ إصدار جديد يثبت التحويل:

```yaml
active_resource_id: sol-resource
activation_reason: quota_exhausted
fallback_from_resource_id: opus-resource
additional_review_required: true
```

لا يحدث التحويل بصمت.

## 7. خفض القدرة

السياسات الممكنة:

```text
prohibited
allow_with_extra_review
allow_for_non_final_work
```

- `prohibited`: لا يستخدم مورد أدنى من حد الجودة.
- `allow_with_extra_review`: يسمح به مع مراجعة مستقلة إضافية.
- `allow_for_non_final_work`: يسمح به في العمل غير النهائي فقط، ولا يستخدم في `approval` أو `synthesis`.

حتى إذا كان المورد البديل فوق حد الجودة لكنه أقل من المرشح الأساسي، تعيد دالة التوجيه `requires_extra_review=true` عندما يكون التحويل مؤثرًا.

## 8. عند نفاد جميع الحصص

السياسة العامة تختار واحدة من:

```text
await_reset | await_user | blocked | cancelled
```

لا يخترع Runtime حصة، ولا يعيد المحاولة بصورة استهلاكية، ولا يسقط إلى نموذج أدنى سرًا. يحفظ جميع المساهمات والأدلة الموجودة، ثم ينتظر إعادة الضبط أو قرار المستخدم أو يحجب المهمة.

يمكن تسجيل الأثر المعياري على المهمة بحدث `execution_blocked` من `ProtocolEvent`، ثم إصدار `Task.next_version` بحالة `blocked` وسبب `resource_unavailable`.

## 9. تفاوت القوة والسلطة

السياسة ثابتة في v0.1:

```yaml
heterogeneity_policy:
  model_count_voting: prohibited
  decision_basis: authority_evidence_verification
  fitness_scope: task_specific
  silent_downgrade: prohibited
```

ثلاثة نماذج خفيفة لا تحصل على ثلاثة أصوات مقابل نموذج قوي. أصلًا لا توجد آلية تصويت عددي في النواة. صاحب `decision_authority` يصدر القرار ضمن نطاقه بعد الأدلة والتحققات والمراجعات، ويمكن لنموذج خفيف أن ينجز مسودة أو بحثًا دون أن يرث سلطة القرار.

## 10. خوارزمية التوجيه المرجعية

الدالة:

```python
route_execution_resource(...)
```

تقوم بما يلي:

1. تطابق المهمة مع قاعدة واحدة فقط.
2. ترتب المرشحين حسب الأولوية المسجلة.
3. تستبعد المورد المنهك أو مجهول الحصة.
4. تخصم الاحتياطي من الحصة المتاحة للمراحل غير المحمية.
5. تمنع المورد دون حد الجودة وفق السياسة.
6. تعلن استعمال البديل والحاجة إلى مراجعة إضافية.
7. تطلب إصدارًا جديدًا للخطة إذا اختلف المورد المختار عن `active_resource_id`.

هي خوارزمية مرجعية حتمية، وليست بعد موصلًا حيًا مع APIs المزودين.

## 11. السلطة ودورة الحياة

عمليتا:

```text
execution_resource_plan.create
execution_resource_plan.next_version
```

مقصورتان على:

```text
orchestrator | human_owner
```

الحالات:

```text
active | suspended | completed | cancelled
```

الهدف وخطة الفريق ثابتان عبر الإصدارات. `completed` و`cancelled` نهائيتان.

## 12. حدود هذه الخطوة

ما اكتمل:

- المخطط القانوني.
- تحقق الحصص والاحتياطي.
- التوجيه بحسب ملاءمة المهمة.
- البدائل المرتبة ومنع الخفض الصامت.
- ربط الموارد بالأدوار وبطاقات الوكلاء وخطة الفريق.
- إصدار لاحق يثبت نفاد الحصة والتحويل.

ما لم يكتمل بعد:

- قراءة الحصص مباشرة من APIs المزودين.
- ربط حجز وحدات المزود واستدعائه الفعلي بمعاملة/حجز تنفيذي؛ يوفر `SessionStore` التخزين والقفل العام، لكن محاسبة المزود الذرية لم توصل بعد.
- قفل خاص بحجز الحصة يمنع عمليتين من إنفاق وحدات المزود نفسها قبل الاستدعاء.
- تقدير التوكنات الفعلي قبل الاستدعاء.
- تعلم درجات الملاءمة تلقائيًا من نتائج التقييم.

بدأت طبقة التخزين والقفل العامة في [SessionStore](SESSION-STORE.ar.md)، وتبقى محاسبة المزود الفعلية ضمن Orchestrator والموصلات.

## التكامل مع FinalResult

يثبت `FinalResult.resource_summary` إصدار الخطة وحالة الحصص وتحويلات المسار الفعلية. لا يجوز للنتيجة إخفاء بديل استعمل بسبب نفاد الحصة أو خفض قدرة احتاج مراجعة إضافية. راجع [FinalResult](FINAL-RESULT.ar.md).
