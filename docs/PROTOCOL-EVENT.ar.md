# ProtocolEvent في W1-CIP v0.1

## 1. الغرض

`ProtocolEvent` هو الحمولة القانونية للأحداث المعيارية المشتقة التي لا تنشئ قرارًا أو مهمة أو دليلًا جديدًا، لكنها تسجل أثرًا تشغيليًا ملزمًا يجب أن يراه المنظم عند تحديد ما يجوز تنفيذه لاحقًا.

من أمثلته:

- إعادة تقييم قرار بعد إعادة فتح اعتراض حرج.
- طلب إبطال قرار تابع بعد سحب قرار أساسي.
- حجب تنفيذ كيان إلى أن يسجل إصدار جديد.
- تعطيل سند سلطة أو منحة سياق بعد اكتشاف عيب.
- تعويض أثر حدث سابق من دون حذفه من التاريخ.

لا يساوي `ProtocolEvent` الغلاف `ProtocolEnvelope`. الغلاف يحمل الهوية والفاعل والسلطة والتسلسل والزمن والسبب، أما `ProtocolEvent` فيحدد الأثر القانوني نفسه.

## 2. الهوية وعدم قابلية التعديل

يسجل الحدث المعياري داخل `VersionedEntity` بالثوابت التالية:

```yaml
entity:
  entity_type: protocol_event
  entity_id: evt-decision-reassessment-required-001
  entity_version: 1
```

قواعد `v0.1`:

1. كل `ProtocolEvent` لها الإصدار `1` فقط.
2. يجب أن يساوي `entity_id` قيمة `ProtocolEnvelope.message_id`.
3. يجب أن يساوي `created_by_event_id` معرف الرسالة نفسها.
4. يمنع `protocol_event.next_version`.
5. لا يصحح الحدث القديم أو يحذف؛ يسجل حدث `compensation_recorded` جديد.

## 3. أنواع الأحداث

| النوع | المعنى |
|---|---|
| `reassessment_required` | يجب إعادة تقييم الكيان وتسجيل إصدار تالٍ قبل اعتماده مجددًا. |
| `invalidation_required` | أصبح الأساس القانوني أو الفني غير صالح، ويلزم إصدار يزيل الاعتماد أو يبطل النتيجة. |
| `execution_blocked` | يمنع التنفيذ المرتبط بالكيان حتى استيفاء الإجراء اللاحق. |
| `authority_disabled` | يمنع استعمال `RoleAssignment` أو `Approval` المستهدفة. |
| `context_access_disabled` | يمنع استعمال `ContextGrant` المستهدفة. |
| `compensation_recorded` | يعطل أثر حدث معياري سابق مع إبقاء الحدثين في السجل. |

## 4. بنية أثر معياري

```yaml
 event_type: reassessment_required
 subject:
   entity_type: decision
   entity_id: decision-motor-driver-001
   entity_version: 3
 trigger_refs:
   - entity_type: challenge
     entity_id: challenge-startup-current-001
     entity_version: 5
 reason:
   code: critical_challenge_reopened
   description: The critical challenge was reopened.
 classification: internal
 propagation: declared_dependents
 blocking: true
 required_follow_up:
   operation: decision.next_version
   must_complete_before: decision.accepted
```

### `subject`

مرجع مثبت للإصدار الذي يتأثر مباشرة. الحدث لا يعدل حمولته القديمة، بل يضيف أثرًا تشغيليًا فوقها إلى أن يسجل الإجراء المطلوب أو حدث تعويضي صالح.

### `trigger_refs`

المصادر المثبتة التي ولدت الأثر. يجب أن يوجد سبب الغلاف `causation_id` ضمن أحداث إنشاء أحد هذه المصادر.

لا يسمح بتكرار هوية المصدر بإصدارين مختلفين داخل الحدث نفسه، ولا باستعمال الموضوع نفسه مصدرًا مباشرًا للحدث.

### `required_follow_up`

يحدد العملية التي تزيل الحالة المعلقة. في `v0.1` يجب أن تكون:

```text
<subject.entity_type>.next_version
```

مثل:

```text
decision.next_version
context_grant.next_version
role_assignment.next_version
```

هذا الحقل لا ينفذ العملية بنفسه ولا يمنح صلاحيتها.

## 5. السببية

كل حدث معياري مشتق يحتاج `causation_id`.

بالنسبة إلى حدث أثر عادي، يجب أن يشير السبب إلى حدث إنشاء أحد `trigger_refs`. وبالنسبة إلى التعويض، يجب أن يشير إلى حدث إنشاء `ProtocolEvent` المعوَّضة.

`related_events` لا تمنح سببية ولا صلاحية، ولا يجوز إدراج `causation_id` نفسه فيها.

## 6. الانتشار

```text
none | declared_dependents
```

- `none`: الأثر يخص الموضوع فقط.
- `declared_dependents`: يسمح للمنظم بإنتاج أحداث منفصلة للتابعين المسجلين في رسم الاعتماديات.

لا يعني الانتشار تعديل التابعين بصمت. كل تابع متأثر يحصل على `ProtocolEvent` مستقلة ومثبتة.

الأحداث `execution_blocked` و`authority_disabled` و`context_access_disabled` تستخدم `none` فقط.

## 7. التعويض

مثال:

```yaml
 event_type: compensation_recorded
 subject:
   entity_type: protocol_event
   entity_id: evt-decision-reassessment-required-001
   entity_version: 1
 reason:
   code: challenge_reopen_reversed
   description: The reopening record was invalidated.
 classification: internal
 propagation: none
 blocking: false
 compensation:
   compensates_event:
     entity_type: protocol_event
     entity_id: evt-decision-reassessment-required-001
     entity_version: 1
   reason: Disable only the operational effect.
```

التعويض:

- لا يحذف الحدث الأصلي.
- لا يعدل تسلسله أو زمنه.
- لا يمكن تطبيقه مرتين على الأثر نفسه.
- لا يمكن أن يستهدف تعويضًا آخر.
- يجب أن يكون تصنيفه مساويًا أو أعلى من الحدث المستهدف.
- يمكن أن يشير إلى حدث بديل موجود سابقًا، لكنه لا يستطيع الإشارة إلى حدث مستقبلي.

## 8. السلطة

إنشاء `ProtocolEvent` يحتاج:

```text
protocol_event.create
```

وهو مقصور في `v0.1` على:

```text
orchestrator | human_owner
```

لا يجوز أن تستخدم سلطة ما حدث `authority_disabled` لتعطيل نفسها. يجب أن يأتي التعطيل من إسناد مستقل صالح.

## 9. التصنيف

القيم:

```text
public | internal | confidential | restricted
```

لا يمكن أن يكون تصنيف الحدث أقل من تصنيف الموضوع أو أي مصدر في `trigger_refs` أو الحدث الذي يجري تعويضه.

لا تنسخ القيم الحساسة إلى سبب الحدث. يسجل السبب وصفًا محدودًا ومراجع مثبتة فقط.

## 10. إعادة بناء الحالة

تنفذ الدالة المرجعية `replay_protocol_log` القواعد التالية:

1. تقبل أحداثًا فقط، ولا تعيد تشغيل الأوامر.
2. تبدأ `sequence` من `1` وتكون متصلة بلا فجوات.
3. تبقى جميع الأحداث داخل `session_id` واحدة.
4. تكون `message_id` فريدة.
5. يشير `causation_id` و`related_events` إلى أحداث سابقة فقط.
6. لا يتراجع `recorded_at` أثناء السجل المرتب.
7. تتحقق شروط إنشاء الكيان والإصدار التالي ومنع الفروع المتعارضة.
8. تدخل أحداث `protocol_event` وحدها في الإسقاط المعياري؛ تبقى الأحداث التشغيلية والأمنية تدقيقية.
9. يضاف الأثر إلى `active_protocol_events`.
10. يزيل التعويض الأثر من الإسقاط النشط، لكنه لا يزيل سجله من `records`.

إعادة تشغيل السجل نفسه مرتين تنتج الحالة نفسها.

## 11. التكامل مع Decision وChallenge

توجد مولدات مرجعية تحول الاعتماديات المسجلة إلى حمولات أحداث:

- `protocol_event_payloads_for_reopened_challenge`
- `protocol_event_payloads_for_decision_change`

### إعادة فتح اعتراض حرج

```text
Challenge: resolved → reopened
→ ProtocolEvent: reassessment_required
→ Decision يحتاج decision.next_version
```

### تغير قرار أساسي

تقرأ السياسة المسجلة في `depends_on_decisions`:

```text
notify      → لا أثر معياري حاجب
reassess    → reassessment_required
block       → execution_blocked
invalidate  → invalidation_required
```

لا يولد أثر اعتماد إلا من حافة مسجلة صراحة.

## 12. رموز الأخطاء الرئيسية

```text
protocol_event_version_must_be_one
protocol_event_next_version_forbidden
protocol_event_identity_must_match_message_id
protocol_event_envelope_type_mismatch
protocol_event_causation_required
protocol_event_causation_not_a_trigger_event
protocol_event_subject_not_found
protocol_event_trigger_not_found
protocol_event_follow_up_operation_mismatch
protocol_event_classification_below_sources
protocol_event_compensation_target_not_found
protocol_event_effect_already_compensated
protocol_event_compensation_of_compensation_forbidden
protocol_event_self_disabling_authority
protocol_log_sequence_gap
protocol_log_causation_not_prior
protocol_log_session_mismatch
protocol_log_recorded_time_regressed
```

## 13. ما لا يفعله ProtocolEvent

- لا يغير الحمولة التاريخية للكيان المستهدف.
- لا يمنح صلاحية تنفيذ الإجراء اللاحق.
- لا يحول الطلب إلى قرار مقبول تلقائيًا.
- لا يثبت أن السبب صحيح لمجرد تسجيله.
- لا يوفر في `v0.1` توقيعًا تشفيريًا أو تجزئة متسلسلة.


## أثر نفاد موارد النموذج

تغير الحصة يسجل أولًا في إصدار جديد من `ExecutionResourcePlan`. إذا لم يوجد بديل صالح، يمكن استخدام `execution_blocked` لاستهداف المهمة وإلزام `task.next_version`. لا يحتاج v0.1 إلى نوع حدث معياري منفصل لكل تحديث عداد؛ فالقياسات التفصيلية تبقى أحداثًا تشغيلية، بينما يسجل السجل المعياري الأثر الذي يغير الالتزامات أو يحجب التنفيذ.
