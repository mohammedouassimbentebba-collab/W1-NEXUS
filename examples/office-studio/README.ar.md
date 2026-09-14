# مثال W1 Office Studio

يحتوي المجلد على نماذج مرجعية للمستند والجداول والعرض، إضافة إلى Patch مبنية على JSON Pointer.

```bash
w1 --workspace ./project init
w1 --workspace ./project office validate --spec examples/office-studio/document.example.json
w1 --workspace ./project office create --spec examples/office-studio/document.example.json --created-by author-agent
w1 --workspace ./project office demo --output-dir office-demo
```

تشغيل الدورة البرمجية الكاملة:

```bash
PYTHONPATH=src python examples/office-studio/run_demo.py
```

لا يُسمح بتصدير Revision إلى ملفات المشروع قبل وجود مراجعة مستقلة معتمدة. الملفات الثنائية تمر عبر `file.publish_staged` ولا تُخزن بايتاتها في سجل Action Runtime.
