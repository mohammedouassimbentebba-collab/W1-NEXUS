# مثال SessionStore

من جذر المشروع:

```bash
PYTHONPATH=src python examples/session-store/bootstrap_demo.py
```

يشغّل المثال حدث Bootstrap المرجعي داخل قاعدة SQLite محلية، ثم يتحقق من سلسلة التجزئة وإعادة التشغيل. إعادة تشغيل الأمر تعيد النتيجة نفسها بوضع `already_present` بدل إضافة حدث ثانٍ.
