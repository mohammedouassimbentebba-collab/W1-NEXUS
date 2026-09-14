# مثال Orchestrator Core

من جذر المشروع:

```bash
PYTHONPATH=src python examples/orchestrator-core/run_demo.py
```

المثال:

1. ينشئ جلسة محلية ومالكًا بشريًا.
2. يبدأ مهمة تنفيذية تحتاج حقل `supply-voltage` فقط.
3. يحاكي نفاد حصة المورد القوي.
4. يحول إلى المورد البديل.
5. يتأكد أن `private-note` لم يصل إلى المزود.
6. يثبت أثر التشغيل في `SessionStore`.
7. يفحص سلامة سلسلة الأحداث.

هذا مثال مصغر. اختبار `test_end_to_end_routes_after_quota_exhaustion_and_persists_trace` يشغل دورة أوسع تشمل التنفيذ والدليل والتحقق والمراجعة والقرار والموافقة والتركيب النهائي.
