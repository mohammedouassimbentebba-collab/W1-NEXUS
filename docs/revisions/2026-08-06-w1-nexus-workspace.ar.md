# مذكرة اعتماد W1 Nexus Workspace + Live Operations Console

**التاريخ:** 2026-08-06  
**الحالة:** معتمد للتنفيذ المرجعي `dev31`

## القرار

اعتماد واجهة ويب محلية مضمنة داخل حزمة W1 Nexus، ترتبط بقواعد الحالة الحالية وتبث Snapshot حيًا عبر SSE. الواجهة محلية وقراءة فقط افتراضيًا، مع عمليات محدودة عند التفعيل الصريح.

## القرارات الأمنية

1. رفض أي bind غير loopback.
2. Token محلي عشوائي وعدم وضعه في URL الرئيسي.
3. حماية API وSSE بالـToken.
4. التحقق من Host header.
5. Content Security Policy بلا مصادر خارجية.
6. عدم كشف provider secrets أو قيم الذاكرة غير اللازمة.
7. عدم إنشاء مسار تنفيذ عام يتجاوز Action Runtime أو SessionStore.

## الأسطح المعتمدة

- Project Dashboard.
- Live Agent Graph.
- Task Timeline.
- Model and Quota Monitor.
- Approval Center.
- Memory and Knowledge Inspector.
- Scientific Lab view.
- Git and Merge view.
- Sandbox Telemetry.
- FinalResult viewer.
- Audit Explorer.

## قابلية التثبيت

تُضمّن ملفات HTML وCSS وJavaScript وSVG داخل Wheel، ويجب اختبار الواجهة من Wheel مثبتة خارج شجرة المصدر.

## ما لا يدعيه dev31

لا يدعي `dev31` أنه Desktop App نهائي أو محرر IDE أو منصة تعاون سحابية. هو Workspace محلية حية وقابلة للتثبيت، تشكل الأساس البصري لـW1 Nexus الكاملة.
