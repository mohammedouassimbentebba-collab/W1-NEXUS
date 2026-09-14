# مثال التشغيل المتوازي

يجب تشغيل المثال داخل مستودع Git نظيف ومهيأ كـW1 Workspace.

```bash
w1 --workspace . init
w1 --workspace . agents run \
  --plan examples/parallel-agents/plan.example.json \
  --approved-by human-owner

w1 --workspace . merge propose parallel-example \
  --test-argv-json '["python","-c","from pathlib import Path; assert Path(\"alpha.txt\").exists() and Path(\"beta.txt\").exists()"]'
```

بعد نسخ `proposal_id`:

```bash
w1 --workspace . merge review <proposal-id> \
  --reviewer independent-reviewer \
  --outcome approved \
  --rationale "Outputs and integration tests accepted"

w1 --workspace . merge apply <proposal-id> --approved-by human-owner
```
