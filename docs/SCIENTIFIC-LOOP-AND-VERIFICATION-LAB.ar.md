# W1 Scientific Loop + Verification Lab

تضيف هذه الطبقة دورة علمية وهندسية قابلة للتدقيق إلى W1 Nexus. لا تعامل الفرضية أو التحليل الإحصائي أو ملخص النموذج على أنه حقيقة نهائية؛ بل تربط السؤال البحثي بخطة مسجلة مسبقًا، وتجربة مضبوطة، وملاحظات غير قابلة لإعادة الكتابة، وتحليل معلن، ومعايير دحض، وتكرار، ومراجعة مستقلة، وحزمة إعادة إنتاج.

## الهدف

تحتاج W1 Nexus الكاملة إلى القدرة على الانتقال من:

```text
سؤال
→ فرضية
→ تنبؤ قابل للاختبار
→ تجربة مضبوطة
→ ملاحظات
→ تحليل
→ محاولة دحض
→ تكرار مستقل
→ مراجعة علمية
→ حزمة قابلة لإعادة الإنتاج
```

المبدأ الحاكم:

> لا تُعد النتيجة العلمية قوية لأنها صيغت بإقناع، بل لأنها نجت من اختبار مسجل مسبقًا ويمكن تتبع بياناته وتحليله وإعادة إنتاجه.

## خطة الدراسة

تتضمن الخطة:

- `research_question`.
- فرضيات وفرضيات عدم.
- تنبؤات قابلة للاختبار.
- تصميم التجربة والمتغيرات والضوابط.
- الحد الأدنى للعينة.
- مخطط الملاحظة.
- الأمر وSandbox Profile عند وجود تنفيذ برمجي.
- خطة التحليل.
- معايير الدحض.
- سياسة التكرار.
- التصنيف الأمني.

مثال مختصر:

```json
{
  "study_id": "pump-current-study",
  "namespace_id": "w1-user",
  "project_id": "pump-project",
  "title": "Pump controller current comparison",
  "research_question": "Does candidate B draw less startup current than candidate A?",
  "hypotheses": [
    {
      "hypothesis_id": "h-lower-current",
      "statement": "Candidate B draws less startup current than candidate A.",
      "null_statement": "Candidate B does not draw less startup current than candidate A.",
      "predictions": [
        {
          "prediction_id": "p-current-gap",
          "metric": "peak-current",
          "direction": "negative"
        }
      ]
    }
  ]
}
```

## التسجيل المسبق

تبدأ الدراسة بـ`draft`، ثم:

```bash
w1 science preregister STUDY_ID
```

يثبت W1 بصمة الخطة قبل إدخال الملاحظات. بعد التسجيل المسبق لا توجد واجهة لتعديل الخطة في مكانها. تغيير الفرضية أو طريقة التحليل أو معيار الدحض يحتاج دراسة جديدة.

يمنع التنفيذ:

- التسجيل المسبق بعد إدخال البيانات.
- تحليل دراسة غير مسجلة مسبقًا.
- استبدال التحليل المخطط بتحليل استكشافي.
- إضافة بيانات إلى الدراسة بعد بدء التحليل.

## التنفيذ المضبوط

يمكن تنفيذ التجربة عبر `SecureExecutionFabric`:

```bash
w1 science run STUDY_ID --request sandbox-request.json
```

يتحقق W1 من تطابق:

- الأمر مع الأمر المسجل مسبقًا.
- Sandbox Profile مع الخطة.
- هوية التنفيذ.
- Result وAttestation وArtifacts.

تدخل سجلات التنفيذ وAttestation وحالة الموارد إلى حزمة إعادة الإنتاج. لا يدعي W1 أن ذكر `randomization` أو `blinding` في الخطة يعني تطبيقهما آليًا؛ يجب أن ينفذه Adapter التجربة أو الجهاز الفعلي، وتراجع الجهة العلمية ذلك.

## الملاحظات

الملاحظات Append-only ومربوطة بسلسلة SHA-256 داخل كل دراسة. كل ملاحظة تحمل:

- `observation_id` فريدًا.
- البيانات المطابقة للمخطط.
- مسجلها ووقت التسجيل.
- `source_run_id` اختياريًا.
- بصمة الملاحظة السابقة.

إذا ذُكر `source_run_id`، يجب أن يكون تشغيلًا موجودًا تابعًا للدراسة نفسها.

```bash
w1 science observe STUDY_ID --file observations.json
```

## التحليل المخطط والاستكشافي

الطرق المحلية الحالية:

```text
descriptive_mean
difference_in_means
proportion
pearson_correlation
```

يُشغل التحليل المسجل مسبقًا:

```bash
w1 science analyze STUDY_ID --analysis-id current-difference
```

أما التحليل الذي أضيف بعد رؤية البيانات فيجب التصريح به:

```bash
w1 science analyze STUDY_ID \
  --analysis-id exploratory-check \
  --exploratory-spec exploratory-analysis.json
```

التحليل الاستكشافي يبقى مفيدًا لتوليد فرضيات جديدة، لكنه لا يستطيع وحده إكمال معايير الدحض أو اعتماد الفرضية الأصلية.

`difference_in_means` في التنفيذ المحلي يستخدم تقريبًا طبيعيًا لقيمة `p` ويصرح بذلك في النتيجة. لا يدعي W1 أنه بديل كامل للاختبارات الدقيقة أو النماذج الإحصائية المتخصصة.

## الدحض وتقييم الفرضيات

تطبق:

```bash
w1 science evaluate STUDY_ID
```

معايير الدحض المسجلة مسبقًا. مخرجات الفرضية:

```text
supported
falsified
inconclusive
```

`inconclusive` ليست نجاحًا ولا فشلًا؛ تعني أن البيانات أو الاختبار لم يحسما النتيجة.

## التكرار

تقارن دراسة أصلية بدراسة تكرار مستقلة:

```bash
w1 science replicate ORIGINAL_STUDY REPLICATION_STUDY
```

النتائج:

```text
replicated
contradicted
inconclusive
```

إذا كانت السياسة تطلب استقلال التكرار، يرفض W1 أن يكون منشئ الدراسة الأصلية ومنشئ التكرار الطرف نفسه.

## المراجعة العلمية

```bash
w1 science review STUDY_ID \
  --reviewer-id independent-reviewer \
  --outcome approved \
  --rationale "Plan, data, analysis, and replication checks accepted."
```

لا يستطيع منشئ الدراسة أو محللها اعتمادها. قبل `approved` يفحص W1:

- أن التسجيل المسبق سبق الملاحظات.
- أن بصمة الخطة لم تتغير.
- أن التحليلات المخططة مكتملة.
- أن سلسلة البيانات سليمة.
- أن التقييم موجود.
- أن الحد الأدنى للتكرار تحقق.
- أن التحليلات الاستكشافية معلنة.

## حزمة إعادة الإنتاج

```bash
w1 science package STUDY_ID --output reports/study.zip
```

تشمل:

```text
study.json
observations.jsonl
experiment-runs.json
analyses.json
evaluation.json
replications.json
reviews.json
environment.json
manifest.json
```

يحمل `manifest.json` حجم وبصمة SHA-256 لكل ملف. ويمكن التحقق:

```bash
w1 science verify-package --file reports/study.zip
```

يرفض التحقق الملفات الناقصة أو الإضافية غير المعلنة أو مختلفة البصمة.

## جسر W1-CIP

لا ينشئ Scientific Loop كيان `Evidence` مزيفًا دون معرفة مراجع المهمة والادعاء والأدوار داخل الجلسة. بدلًا من ذلك ينشئ Manifest وسيطًا:

```bash
w1 science bridge STUDY_ID
```

يتضمن:

- بصمات الخطة والبيانات والتحليلات.
- علاقة مقترحة: `supports` أو `refutes` أو `contextualizes`.
- مصدر الحزمة العلمية.
- حالة Integrity.
- المراجعة المستقلة.
- متطلبات فصل الأدوار.
- Mapping مقترح إلى `Evidence` و`Verification`.

ثم ينشئ Orchestrator كيانات W1-CIP القانونية مع المراجع المثبتة الخاصة بالجلسة.

## التكامل مع Memory Fabric

لا تحفظ النتائج العلمية تلقائيًا. بعد مراجعة مستقلة ونتيجة `supported` يمكن النشر صراحة:

```bash
w1 science publish-memory STUDY_ID \
  --hypothesis-id h-lower-current
```

تُحفظ الذاكرة مع Provenance تشير إلى الدراسة وPlan Hash والفرضية، ولا تتحول نتيجة `falsified` أو `inconclusive` إلى حقيقة مدعومة.

## السجل والسلامة

تملك الطبقة:

- سلسلة أحداث علمية Append-only.
- سلسلة ملاحظات مستقلة لكل دراسة.
- Triggers تمنع تعديل وحذف الملاحظات والتحليلات والأحداث.
- Plan Hash ثابتًا.
- Data Digest لكل تحليل.

```bash
w1 science verify
w1 science verify --study-id STUDY_ID
```

## حدود التنفيذ الحالي

لم يكتمل بعد:

- اختبارات إحصائية دقيقة شاملة أو Bayesian inference متقدم.
- تصحيح متعدد المقارنات تلقائي لكل تصميم.
- إدارة أجهزة مخبرية عامة دون Adapters.
- Randomization وBlinding ماديان من دون تكامل جهاز.
- دفتر مختبر تعاوني رسومي.
- تنسيقات صناعية مثل Jupyter/RO-Crate/CWL بصورة كاملة.
- توقيعات خارجية أو Timestamp Authority.

هذه الحدود معلنة حتى لا يقدم W1 نتيجة محلية تقريبية بوصفها اعتمادًا علميًا عامًا.
