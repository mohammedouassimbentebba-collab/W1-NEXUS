# تجربة CLI السريعة

من جذر المشروع:

```bash
python -m pip install -e .

w1 --workspace /tmp/w1-demo init
w1 --workspace /tmp/w1-demo doctor
w1 --workspace /tmp/w1-demo demo --reset
w1 --workspace /tmp/w1-demo benchmark
w1 --workspace /tmp/w1-demo status run-w1-reference-demo-001
w1 --workspace /tmp/w1-demo sessions verify session-w1-reference-demo-001
w1 --workspace /tmp/w1-demo audit session-w1-reference-demo-001
```

لا تحتاج التجربة إلى شبكة أو مفاتيح API.
