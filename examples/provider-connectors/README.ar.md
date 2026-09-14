# أمثلة Provider Connectors

- `providers.example.json`: إعدادات بلا أسرار. استبدل معرفات النماذج المثبتة بما تختاره.
- `build_registry.py`: يبني الموصلات ولا يقرأ الأسرار ولا يرسل طلبًا.
- `mock_demo.py`: تشغيل كامل دون شبكة عبر `HTTPTransport` تجريبي.

تشغيل الأمثلة من جذر المشروع:

```bash
python examples/provider-connectors/build_registry.py
python examples/provider-connectors/mock_demo.py
```

لا تضع قيمة المفتاح في JSON. ضع اسم متغير البيئة في `api_key_reference`، ثم عرّف المتغير في بيئة الخادم أو في Vault خارجي لاحقًا.
