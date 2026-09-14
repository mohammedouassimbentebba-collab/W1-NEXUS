# Step 41 — External Benchmarks & Release Hardening

**الإصدار:** `0.1.0.dev41`

تضيف هذه الخطوة طبقة hardening وإثبات إصدار قابلة للتشغيل: threat model، fuzz/property probes محددة البذرة، load/restart/tamper checks، source manifest، dependency inventory، release gates، وسجل نتائج خارجي لا يسمح بنسبة benchmark إلى W1 دون raw evidence وSHA-256 وupstream provenance.

الـbenchmarks الخارجية المسجلة لا تعني أنها نُفذت. أي benchmark لم يُشغّل بأداته الرسمية يبقى `NOT_RUN`. كما لا تختار هذه الخطوة ترخيصًا قانونيًا نهائيًا نيابة عن مالك المشروع؛ النشر العام يبقى fail-closed حتى وجود `LICENSE` مقصود.
