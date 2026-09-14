# مثال W1 Action Runtime

من جذر المشروع:

```bash
PYTHONPATH=src python examples/action-runtime/run_demo.py
```

أو عبر CLI بعد التثبيت:

```bash
w1 init .
w1 actions plan --request examples/action-runtime/write-file.json
w1 actions execute --request examples/action-runtime/write-file.json
w1 actions undo write-reviewed-output
```
