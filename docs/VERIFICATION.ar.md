# Verification — W1-CIP v0.1

## 1. الغرض

يمثل `Verification` تطبيق طريقة محددة ومصدّرة على مساهمة واحدة أو أكثر من النوع `claim`، باستخدام أدلة مثبتة، ثم تسجيل النتيجة والافتراضات والقيود. لا يعدل التحقق الادعاء أو الدليل، ولا يحل محل `Review` أو `Decision`.

## 2. المبادئ

- حتمية الطريقة لا تعني صلاحيتها.
- `integrity: verified` في الدليل لا يعني أن الادعاء صحيح.
- لا تكون النتيجة `conclusive` إلا بطريقة `validated` وأدلة سلامتها `verified`.
- الطريقة `unvalidated` أو `disputed` تنتج نتيجة `qualified` فقط.
- لا يسمح لـ`disputed` بإعلان `passed`.
- الادعاء `not_currently_verifiable` لا يمكن أن يحصل على `passed` قبل إصدار جديد يغير قابليته للتحقق.

## 3. البنية المرجعية

```yaml
target_claims:
  - entity_type: contribution
    entity_id: claim-candidate-a-startup-001
    entity_version: 1

method:
  type: deterministic_rule
  method_id: peak-current-comparison
  method_version: 1
  procedure_source: fixture://rules/peak-current-v1
  validation:
    status: validated
    scope: compare pinned current limits in amperes
    basis: direct numeric comparison on equal units
    assessed_by_role_assignment:
      entity_type: role_assignment
      entity_id: role-assignment-verifier-001
      entity_version: 1
    assessed_at: 2026-08-04T15:20:02Z

evidence:
  - entity_type: evidence
    entity_id: evidence-startup-current-001
    entity_version: 1

result: failed
conclusion_status: conclusive
```

## 4. أنواع الطرق

```text
deterministic_rule
executable_test
calculation
source_cross_check
human_check
```

تحتاج كل طريقة إلى `method_id` و`method_version` و`procedure_source`. يحتاج `executable_test` إلى بيئة تنفيذ، ويحتاج `human_check` إلى معايير معلنة وأن ينفذه إنسان.

## 5. صلاحية الطريقة

الحالات:

```text
validated | unvalidated | disputed
```

- `validated` تحتاج نطاقًا وأساسًا ومقيّمًا مخولًا ووقت تقييم.
- `unvalidated` تعني أن الصلاحية لم تثبت بعد.
- `disputed` تحتاج سبب الخلاف، ولا تسمح بنتيجة `passed`.

صلاحية تقييم الطريقة هي `verification.validate_method`.

## 6. النتائج

```text
passed | failed | inconclusive | not_run
```

ويضاف:

```text
conclusive | qualified
```

`not_run` يحتاج سببًا ولا يحمل أدلة مستعملة. أما بقية النتائج فتحتاج أدلة مثبتة. يجب أن تغطي الأدلة جميع الادعاءات المستهدفة، وأن يظهر دليل داعم لنتيجة `passed` ودليل معارض لنتيجة `failed`.

## 7. الربط بالمهمة والسلطة

يجب أن تكون المهمة في مرحلة `verification` وحالة `in_progress`. ويجب أن يكون المنفذ مسؤول المهمة، بدور `verifier`، ومخولًا بـ`verification.create` أو `verification.next_version`. تتحقق أهلية `AgentCard` وإذن كتابة `verification`.

## 8. الاستقلال

إذا فرض `TeamPlan` فصل المتحقق عن مؤلف الادعاء، يقارن النظام الأشخاص الفعليين في `RoleAssignment`، لا أسماء الأدوار فقط.

## 9. الأدلة والتصنيف

كل دليل يجب أن يكون نشطًا، مثبت الإصدار، ذا صلة بأحد الادعاءات المستهدفة، ومن الهدف وخطة الفريق نفسيهما. لا يجوز أن يكون تصنيف التحقق أقل تقييدًا من أي دليل مستخدم.

## 10. دورة الحياة

```text
active | withdrawn
```

تشكل كل عملية تشغيل طريقة سجل `Verification` جديدًا. لا تغير النتيجة أو الطريقة أو الأدلة داخل هوية التحقق نفسها. يسمح فقط بإصدار سحب لاحق، والسحب نهائي.
