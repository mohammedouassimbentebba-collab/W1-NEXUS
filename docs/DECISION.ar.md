# Decision — W1-CIP v0.1

## 1. الغرض

يمثل `Decision` حكمًا معياريًا واحدًا حول موضوع محدد في `GoalContract`. لا ينشئ القرار الحقيقة من تلقاء نفسه، ولا يستبدل الأدلة أو التحقق أو المراجعة؛ بل يجمع الإصدارات المثبتة التي بُني عليها الحكم، ويحدد السلطة التي اعتمدته، وخياراته، واعتمادياته، وحالته الحالية.

مخطط الحمولة القانونية:

```text
urn:w1-cip:entity-payload:0.1:decision
```

ويحفظ كل إصدار داخل `VersionedEntity` كامل وغير قابل للتعديل.

## 2. البنية الأساسية

```yaml
goal:
  entity_type: goal_contract
  entity_id: goal-pump-driver-001
  entity_version: 1

team_plan:
  entity_type: team_plan
  entity_id: team-plan-pump-001
  entity_version: 1

task:
  entity_type: task
  entity_id: task-select-driver-decision-001
  entity_version: 4

subject: motor_driver_selection
status: accepted

proposed_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-decision-001
  entity_version: 1
proposed_at: 2026-08-04T15:30:00Z

last_transition_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-decision-001
  entity_version: 1
last_transition_at: 2026-08-04T15:31:00Z

decided_by_role_assignment:
  entity_type: role_assignment
  entity_id: role-assignment-decision-001
  entity_version: 1
decided_at: 2026-08-04T15:31:00Z

options_considered:
  - option_id: candidate-a
    description: Candidate with a 15 A one-second peak limit.
  - option_id: candidate-b
    description: Candidate with a 20 A one-second peak limit.
selected_option: candidate-b
```

## 3. دورة الحياة

```text
proposed
  → under_review | needs_evidence | rejected

under_review
  → needs_evidence | accepted | rejected

needs_evidence
  → under_review | accepted | rejected

accepted
  → reassessment_required | superseded | revoked

reassessment_required
  → accepted | superseded | revoked
```

الحالات `rejected` و`superseded` و`revoked` نهائية داخل هوية القرار نفسها.

العودة من `reassessment_required` إلى `accepted` تحتاج إصدارًا جديدًا وإعادة تأكيد مسجلة؛ لا تعود الحالة صامتًا.

## 4. شروط القرار المقبول

لا يقبل `status: accepted` إلا إذا تحقق المرجع البرمجي من الآتي:

1. المهمة الحالية في مرحلة `approval` وحالتها `in_progress`.
2. صاحب الانتقال هو المسؤول المثبت للمهمة.
3. يملك صاحب السلطة دور `decision_authority` أو `human_owner` وإسنادًا نشطًا وساريًا.
4. يغطي نطاق الإسناد عمليتي `decision.create` أو `decision.next_version` وموضوع القرار.
5. موضوع القرار موجود في `GoalContract.decision_policy`.
6. يطابق صاحب السلطة الإسناد الذي حسمه `TeamPlan` لهذا الموضوع.
7. يوجد خيار محدد ضمن `options_considered`.
8. ترتبط النتيجة بمساهمات وأدلة وتحققات ومراجعات مثبتة وحديثة.
9. يوجد تحقق حاسم، ولا يعتمد القرار على تحقق `inconclusive` أو `not_run`.
10. توجد مراجعة واحدة على الأقل نتيجتها `approved`.
11. الاعتراضات الموسومة معالجة تشير إلى حالات `resolved` أو `rejected`.
12. لا يوجد اعتراض حرج غير محسوم، إلا `disclosed_unresolved` مع موافقة مخاطر صالحة تستهدف الاعتراض نفسه.
13. إذا طلب عقد الهدف موافقة بشرية، توجد `decision_approval` فعالة مرتبطة بالقرار.
14. لا يقل تصنيف القرار عن تصنيف أي مصدر اعتمد عليه.

لا تعني `confidence.level: high` منح سلطة أو تعويض غياب الدليل أو التحقق.

## 5. المصادر المثبتة

يسجل القرار القوائم الآتية بصورة مستقلة:

```yaml
based_on_contributions: []
supporting_evidence: []
verifications: []
reviews: []
addressed_challenges: []
unresolved_challenges: []
approvals: []
```

لا يجوز وضع الاعتراض نفسه في `addressed_challenges` و`unresolved_challenges` معًا. وإذا ظهر إصدار دلالي أحدث من مصدر مثبت، يحتاج القرار إلى إعادة تقييم؛ أما التصحيح التحريري المعتمد ذو `substantive_effect: none` فلا يغير المرجع الدلالي تلقائيًا.

## 6. اعتماديات القرارات

```yaml
depends_on_decisions:
  - decision:
      entity_type: decision
      entity_id: prerequisite-decision
      entity_version: 3
    critical: true
    on_revoked: invalidate
    on_superseded: reassess
    on_reassessment: block
```

السياسات المسموحة:

```text
notify | reassess | invalidate | block
```

- `notify`: إشعار فقط.
- `reassess`: نقل القرار التابع إلى `reassessment_required`.
- `invalidate`: إبطال الحكم السابق.
- `block`: منع الاستعمال الجديد مؤقتًا دون الحكم ببطلانه.

لا يسمح لاعتمادية حرجة بأن تستخدم `notify` وحدها في جميع الحالات. ويجب أن يكون رسم اعتماديات القرارات لا دوريًا؛ يرفض أي ارتباط ينشئ دورة بخطأ `decision_dependency_cycle`.

ينتشر الأثر عبر الاعتماديات المسجلة فقط، لا عبر التشابه النصي أو قرب العناصر في واجهة العرض.

## 7. إعادة التقييم

تستخدم الحالة:

```yaml
status: reassessment_required
status_reason: A critical challenge was reopened.
reassessment:
  trigger_type: challenge_reopened
  triggered_at: 2026-08-04T15:40:00Z
  basis_refs:
    - entity_type: challenge
      entity_id: challenge-startup-current-001
      entity_version: 5
```

المحفزات المعتمدة تشمل تغير الدليل، بطلان التحقق، إعادة فتح اعتراض، تغير قرار معتمد عليه، تغير عقد الهدف، سحب موافقة، بطلان السلطة، أو فقدان حق الاعتماد المستمر على السياق.

إذا أعيد فتح اعتراض `critical` كان القرار يعتمد عليه، يعيد النظام تقييم القرار مباشرة، ثم ينشر الأثر إلى القرارات التابعة وفق سياسات الاعتماديات المسجلة.

لا يجوز استعمال قرار في `reassessment_required` كأساس غير مقيد لقرار جديد.

## 8. الاستبدال والسحب

- `superseded` يعني أن قرارًا جديدًا حل محل الحكم، ويجب تسجيل `superseded_by`.
- `revoked` يعني سحب الحكم بسبب خلل أو تغير شرط، ويحتاج كائن `revocation` مع السبب والوقت والأساس.
- لا يتغير الخيار المقبول داخل القرار نفسه بعد الاعتماد؛ اختيار بديل مختلف ينشئ قرارًا جديدًا يستبدل السابق.

## 9. قابلية التراجع

```yaml
reversibility:
  reversible: true
  rollback_action: Issue a superseding decision after fresh verification.
```

إذا كانت `reversible: true` يجب تسجيل إجراء التراجع. وإذا كانت `false` يمنع وجود `rollback_action`، لكن ذلك لا يجعل القرار غير قابل للمراجعة أو الإبطال.

## 10. التكامل مع المهمة

يمكن للقرار تحقيق مخرج مهمة:

```yaml
fulfills_output_id: driver-selection-decision
```

وعند إكمال المهمة يتحقق النظام من أن المخرج:

- موجود ومثبت بالإصدار.
- مرتبط بالمهمة نفسها.
- يطابق نوع `decision` ومعرف المخرج.
- حالته `accepted`.

## 11. ما لا يثبته المخطط وحده

تحتاج حالة المستودع والمدقق البرمجي إلى التحقق من:

- وجود المراجع وحداثتها.
- صلاحية السلطة عند وقت التسجيل.
- تطابق سلطة القرار مع عقد الهدف وخطة الفريق.
- سلامة رسم الاعتماديات وعدم وجود دورة.
- انتشار إعادة التقييم.
- صلاحية الموافقات وقبول المخاطر.
- عدم استعمال قرار يحتاج إعادة تقييم كأساس عادي.

## تحويل آثار القرار إلى ProtocolEvent

لا تعدل دوال نشر الاعتماديات قرارات التابعين مباشرة. تحول السياسات المسجلة إلى حمولات `ProtocolEvent` منفصلة:

- `reassess` → `reassessment_required`
- `block` → `execution_blocked`
- `invalidate` → `invalidation_required`

كما تولد إعادة فتح اعتراض حرج أحداث إعادة تقييم للقرارات المتأثرة. ويبقى الانتقال القانوني النهائي إصدارًا جديدًا من `Decision`.


## الظهور في FinalResult

لا يظهر القرار ضمن `accepted_decisions` إلا إذا كان أحدث إصدار مثبت وحالته `accepted`. تبقى تفاصيل القرار في كيانه، ويعرض `FinalResult` مرجعه ونتيجة الهدف فقط.
